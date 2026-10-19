"""Dynamic owner definitions are organization data, never result snapshots."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json

import pytest
from fastapi.testclient import TestClient

from backend.adapters.api.app import create_app
from backend.schema.models import utc_now


@pytest.fixture
def setup(tmp_path):
    args = (str(tmp_path / 'knowledge_os.db'), str(tmp_path / 'raw_sources'), str(tmp_path / 'artifacts'))
    return TestClient(create_app(*args)), args


def create_view(client, workspace='notes', **kwargs):
    payload = {'name': '当前相关资料', 'scope': 'default', 'query': {
        'schema_version': 'v1', 'workspace': workspace, 'filters': {'query': 'needle'},
        'sort': [{'field': 'id', 'direction': 'ASC'}], 'limit': 2}, **kwargs}
    response = client.post('/api/saved-views', json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def execute(client, view, **kwargs):
    return client.post(f"/api/saved-views/{view['id']}/execute", json={
        'scope': view['scope'], 'expected_revision': view['revision'], **kwargs})


def test_definition_is_persistent_but_results_are_current(setup):
    client, args = setup
    view = create_view(client)
    assert execute(client, view).json()['total'] == 0
    notes = [client.post('/api/notes', json={'title': f'{i}', 'content': 'needle'}).json() for i in range(3)]
    first = execute(client, view).json()
    assert first['total'] == 3 and first['has_more'] and len(first['items']) == 2
    second = execute(client, view, offset=2).json()
    assert {n['id'] for n in first['items'] + second['items']} == {n['id'] for n in notes}
    client.post(f"/api/notes/{notes[0]['id']}/revisions", json={
        'title': 'updated', 'content': 'no longer matching', 'expected_revision': 1})
    restarted = TestClient(create_app(*args))
    current = execute(restarted, view).json()
    assert current['total'] == 2
    assert current['view_revision'] == 1
    assert restarted.get(f"/api/saved-views/{view['id']}").json() == view


def test_explicit_update_history_cas_and_archive_preserve_source(setup):
    client, _ = setup
    source = client.post('/api/notes', json={'title': 'original', 'content': 'needle'}).json()
    view = create_view(client)
    patch = {'scope': 'default', 'expected_revision': 1, 'name': 'renamed'}
    changed = client.patch(f"/api/saved-views/{view['id']}", json=patch)
    assert changed.status_code == 200
    changed = changed.json()
    assert changed['revision'] == 2
    assert client.patch(f"/api/saved-views/{view['id']}", json=patch).status_code == 409
    assert execute(client, view).status_code == 409
    archived = client.patch(f"/api/saved-views/{view['id']}", json={
        'expected_revision': 2, 'archived': True}).json()
    assert execute(client, archived).status_code == 409
    assert client.get('/api/saved-views').json()['total'] == 0
    restored = client.patch(f"/api/saved-views/{view['id']}", json={
        'expected_revision': 3, 'archived': False}).json()
    assert execute(client, restored).json()['total'] == 1
    history = client.get(f"/api/saved-views/{view['id']}/history").json()
    assert [v['revision'] for v in history['items']] == [4, 3, 2, 1]
    assert client.get(f"/api/notes/{source['id']}").json() == source


@pytest.mark.parametrize('change', [
    {'schema_version': 'old'}, {'workspace': 'all'}, {'sql': 'SELECT * FROM notes'},
    {'filters': {'scope': 'private'}}, {'filters': {'verification_status': 'VERIFIED'}},
    {'filters': {'author': True}}, {'sort': [{'field': 'title; DROP TABLE notes', 'direction': 'ASC'}]},
    {'sort': [{'field': 'title', 'direction': 'sideways'}]}, {'sort': []},
    {'sort': [{'field': 'id'}, {'field': 'id'}]}, {'limit': True}, {'limit': 101},
    {'updated_within_days': 0}, {'updated_within_days': '7'},
    {'updated_after': '2026-10-12'}, {'updated_before': 'not a time'},
    {'updated_after': '0001-01-01T00:00:00+01:00'},
    {'updated_before': '9999-12-31T23:59:59-01:00'},
    {'updated_after': '2026-10-13T00:00:00Z', 'updated_before': '2026-10-12T00:00:00Z'},
])
def test_unavailable_executable_or_unbounded_queries_are_rejected(setup, change):
    client, _ = setup
    response = client.post('/api/saved-views', json={'name': 'invalid',
        'query': {'workspace': 'notes', **change}})
    assert response.status_code == 422
    assert client.get('/api/saved-views').json()['total'] == 0


def test_scope_filters_literal_query_and_budget(setup):
    client, _ = setup
    hidden = client.post('/api/notes', json={'title': 'needle', 'content': 'needle', 'scope': 'private'}).json()
    source = client.post('/api/notes', json={'title': 'literal', 'content': "%' OR 1=1 --"}).json()
    view = create_view(client, query={'workspace': 'notes', 'filters': {'query': "%' OR 1=1 --"}, 'limit': 1})
    assert [x['id'] for x in execute(client, view).json()['items']] == [source['id']]
    assert hidden['id'] not in json.dumps(execute(client, view).json())
    for suffix in ['', '/history']:
        assert client.get(f"/api/saved-views/{view['id']}{suffix}", params={'scope': 'private'}).status_code == 404
    assert execute(client, view, scope='private').status_code == 404
    assert execute(client, view, limit=2).status_code == 422
    assert execute(client, view, offset=-1).status_code == 422
    assert client.post(f"/api/saved-views/{view['id']}/execute", json={'scope': 'default'}).status_code == 422
    assert execute(client, view, query={'workspace': 'memory'}).status_code == 422


def test_memory_is_current_active_valid_and_explicitly_filtered(setup):
    from backend.schema.models import GraphNode, NodeType, TemporalValidity
    from backend.storage.canonical.sqlite_storage import CanonicalStorage
    client, args = setup
    store = CanonicalStorage(args[0])
    now = utc_now()
    nodes = [
        GraphNode(id='valid', title='valid', node_type=NodeType.FACT, content='needle', verification_props={'status': 'UNVERIFIED'}),
        GraphNode(id='private', title='private', node_type=NodeType.FACT, scope='private', content='needle'),
        GraphNode(id='inactive', title='inactive', node_type=NodeType.FACT, content='needle', is_active=False),
        GraphNode(id='expired', title='expired', node_type=NodeType.FACT, content='needle', temporal_props=TemporalValidity(valid_to=now - timedelta(days=1))),
        GraphNode(id='future', title='future', node_type=NodeType.FACT, content='needle', temporal_props=TemporalValidity(valid_from=now + timedelta(days=1))),
        GraphNode(id='superseded', title='superseded', node_type=NodeType.FACT, content='needle', temporal_props=TemporalValidity(superseded_by='valid')),
        GraphNode(id='verified', title='verified', node_type=NodeType.FACT, content='needle', verification_props={'status': 'VERIFIED'}),
    ]
    for node in nodes:
        store.upsert_node(node)
    view = create_view(client, 'memory', query={'workspace': 'memory', 'filters': {'verification_status': 'UNVERIFIED'}, 'limit': 100})
    assert [n['id'] for n in execute(client, view).json()['items']] == ['valid']
    store.upsert_node(nodes[0].model_copy(update={'is_active': False}), expected_revision=1)
    assert execute(client, view).json()['total'] == 0


def test_relative_dates_are_resolved_at_each_execution_and_timezone_is_explicit(setup, monkeypatch):
    from backend.views import manager
    client, _ = setup
    source = client.post('/api/notes', json={'title': 'recent', 'content': 'needle'}).json()
    now = utc_now()
    view = create_view(client, query={'workspace': 'notes', 'updated_within_days': 7})
    assert execute(client, view).json()['total'] == 1
    monkeypatch.setattr(manager, 'utc_now', lambda: now + timedelta(days=8))
    assert execute(client, view).json()['total'] == 0
    before = (now + timedelta(days=1)).astimezone().isoformat()
    absolute = create_view(client, query={'workspace': 'notes', 'updated_before': before})
    assert execute(client, absolute).json()['items'][0]['id'] == source['id']


@pytest.mark.parametrize('field, expected', [('updated_after', 0), ('updated_before', 1)])
def test_absolute_time_preserves_source_microsecond_precision(setup, field, expected):
    from backend.storage.canonical.sqlite_storage import CanonicalStorage
    client, args = setup
    source = client.post('/api/notes', json={'title': 'precise', 'content': 'needle'}).json()
    with CanonicalStorage(args[0]).session() as conn:
        conn.execute('UPDATE notes SET created_at = ? WHERE id = ?', ('2026-10-12T00:00:00.123100+00:00', source['id']))
    view = create_view(client, query={'workspace': 'notes', field: '2026-10-12T00:00:00.123101+00:00'})
    assert execute(client, view).json()['total'] == expected


def test_date_sort_preserves_microseconds_and_instant_not_timezone_string(setup):
    from backend.storage.canonical.sqlite_storage import CanonicalStorage
    client, args = setup
    with CanonicalStorage(args[0]).session() as conn:
        conn.executemany('INSERT INTO notes VALUES (?, 1, ?, ?, ?, ?, ?, 1, ?)', [
            ('a-older', 'old', 'needle', 'fixture', 'default', 'user', '2026-10-12T08:00:00.123100+08:00'),
            ('z-newer', 'new', 'needle', 'fixture', 'default', 'user', '2026-10-12T00:00:00.123101+00:00')])
    view = create_view(client, query={'workspace': 'notes'})
    assert [x['id'] for x in execute(client, view).json()['items']] == ['z-newer', 'a-older']


@pytest.mark.parametrize('field, expected', [('valid_from', 0), ('valid_to', 1)])
def test_memory_time_eligibility_preserves_microseconds(setup, monkeypatch, field, expected):
    from datetime import datetime
    from backend.schema.models import GraphNode, NodeType, TemporalValidity
    from backend.storage.canonical.sqlite_storage import CanonicalStorage
    from backend.views import manager
    client, args = setup
    now = datetime.fromisoformat('2026-10-12T00:00:00.123100+00:00')
    validity = TemporalValidity(**{field: now + timedelta(microseconds=1)})
    CanonicalStorage(args[0]).upsert_node(GraphNode(id='time-boundary', title='precise',
        node_type=NodeType.FACT, content='needle', temporal_props=validity))
    view = create_view(client, query={'workspace': 'memory'})
    monkeypatch.setattr(manager, 'utc_now', lambda: now)
    assert execute(client, view).json()['total'] == expected


def test_collection_dependency_never_silently_expands_or_leaks(setup):
    client, _ = setup
    topic = client.post('/api/topics', json={'title': 'owned'}).json()
    source = client.post('/api/notes', json={'title': 'current', 'content': 'needle'}).json()
    view = create_view(client, query={'workspace': 'notes', 'filters': {'topic_id': topic['id']}})
    assert execute(client, view).json()['total'] == 0
    response = client.post(f"/api/topics/{topic['id']}/members", json={
        'kind': 'note', 'object_id': source['id'], 'expected_revision': 1})
    assert response.status_code == 200
    assert execute(client, view).json()['total'] == 1
    foreign = client.post('/api/topics', json={'title': 'secret', 'scope': 'private'}).json()
    bad = create_view(client, query={'workspace': 'notes', 'filters': {'topic_id': foreign['id']}})
    assert execute(client, bad).status_code == 404
    missing = create_view(client, query={'workspace': 'notes', 'filters': {'topic_id': 'missing'}})
    assert execute(client, missing).status_code == 404


def test_backlog_and_logsheet_views_are_maintenance_only(setup):
    client, _ = setup
    captured = client.post('/api/maintenance/backlog', json={'type': 'VERIFY_CLAIM', 'targets': [], 'reason': 'needle evidence'})
    assert captured.status_code == 200, captured.text
    item = captured.json()
    view = create_view(client, 'backlog', query={'workspace': 'backlog', 'filters': {'status': 'OPEN'}})
    result = execute(client, view)
    assert result.status_code == 200, result.text
    assert result.json()['items'][0]['id'] == item['id']
    assert result.json()['items'][0]['targets'] == []
    logs = create_view(client, 'logsheet', query={'workspace': 'logsheet', 'filters': {'event_type': 'ITEM_CREATED', 'backlog_id': item['id']}})
    entries = execute(client, logs).json()['items']
    assert len(entries) == 1 and entries[0]['event_type'] == 'ITEM_CREATED'
    assert entries[0]['actor'] and 'recorded_at' in entries[0]


def test_concurrent_cas_noop_and_organization_audit_are_atomic(setup):
    from backend.storage.canonical.sqlite_storage import CanonicalStorage
    from backend.views.manager import SavedViewManager
    client, args = setup
    view = create_view(client)
    updates = [{'expected_revision': 1, 'name': str(i)} for i in range(3)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda update: client.patch(f"/api/saved-views/{view['id']}", json=update), updates))
    assert sorted(r.status_code for r in results) == [200, 409, 409]
    current = client.get(f"/api/saved-views/{view['id']}").json()
    assert client.patch(f"/api/saved-views/{view['id']}", json={
        'expected_revision': 2, 'name': current['name']}).json()['revision'] == 2
    store = CanonicalStorage(args[0])
    with store.session() as conn:
        assert conn.execute('SELECT COUNT(*) FROM saved_view_audit').fetchone()[0] == 2
        assert conn.execute('SELECT COUNT(*) FROM saved_view_revisions').fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM transactional_outbox WHERE aggregate_type='SAVED_VIEW'").fetchone()[0] == 2
        assert conn.execute('SELECT COUNT(*) FROM system_memory_logsheet').fetchone()[0] == 0
    manager = SavedViewManager(store)
    original = store._insert_outbox_event
    def fail(*args, **kwargs):
        raise RuntimeError('injected transaction failure')
    store._insert_outbox_event = fail
    with pytest.raises(RuntimeError):
        manager.update(view['id'], expected_revision=2, name='never committed')
    store._insert_outbox_event = original
    assert manager.get(view['id']) == current
    assert manager.history(view['id'])['total'] == 2


def test_unknown_stored_schema_fails_closed(setup):
    from backend.storage.canonical.sqlite_storage import CanonicalStorage
    client, args = setup
    view = create_view(client)
    with CanonicalStorage(args[0]).session() as conn:
        conn.execute('UPDATE saved_views SET query_json = ? WHERE id = ?',
                     (json.dumps({'schema_version': 'future', 'workspace': 'notes'}), view['id']))
    assert execute(client, view).status_code == 422


def test_schema_contract_and_strict_owner_updates(setup):
    client, _ = setup
    contract = client.get('/api/saved-views/schema').json()
    assert contract['workspaces'] == ['notes', 'memory', 'backlog', 'logsheet']
    assert contract['max_page_size'] == 100 and not contract['historical_as_of']
    view = create_view(client)
    for payload in [{'expected_revision': True, 'name': 'bad'}, {'expected_revision': 1},
                    {'expected_revision': 1, 'archived': 'true'},
                    {'expected_revision': 1, 'owner_id': 'other', 'name': 'bad'},
                    {'expected_revision': 1, 'name': '   '}]:
        assert client.patch(f"/api/saved-views/{view['id']}", json=payload).status_code == 422
    assert client.get(f"/api/saved-views/{view['id']}").json() == view


def test_query_deadline_stops_sql_and_preserves_definition(setup, monkeypatch):
    from types import SimpleNamespace
    from backend.storage.canonical.sqlite_storage import CanonicalStorage
    from backend.views import manager
    client, args = setup
    view = create_view(client)
    with CanonicalStorage(args[0]).session() as conn:
        conn.executemany('INSERT INTO notes VALUES (?, 1, ?, ?, ?, ?, ?, 1, ?)', [
            (f'n-{i}', 'deadline', 'needle', 'fixture', 'default', 'user', utc_now().isoformat()) for i in range(500)])
    calls = iter([0])
    monkeypatch.setattr(manager, 'time', SimpleNamespace(monotonic=lambda: next(calls, 4)))
    response = execute(client, view)
    assert response.status_code == 504, response.text
    assert 'budget' in response.json()['detail']
    assert client.get(f"/api/saved-views/{view['id']}").json() == view


def test_owner_only_definition_changes_and_revoked_reads(setup):
    from backend.auth.policy import AccessPolicy
    from backend.storage.canonical.sqlite_storage import CanonicalStorage
    from backend.views.manager import SavedViewManager
    client, args = setup
    view = create_view(client)
    store = CanonicalStorage(args[0])
    manager, policy = SavedViewManager(store), AccessPolicy(store)
    policy.ensure_launch_grant('reader', 'default')
    with policy.activate('reader', 'default'):
        assert manager.get(view['id']) == view
        assert manager.execute(view['id'], expected_revision=1)['total'] == 0
        with pytest.raises(PermissionError):
            manager.list(scope='private')
        with pytest.raises(PermissionError):
            manager.update(view['id'], expected_revision=1, name='not owner')
    grant = policy.get_grant('reader', 'default')
    policy.update_grant('reader', 'default', read=False, write=False, expected_revision=grant['revision'], actor='local-owner')
    with pytest.raises(PermissionError):
        with policy.activate('reader', 'default'):
            manager.execute(view['id'], expected_revision=1)


def test_snapshot_restores_definitions_history_and_dynamic_query(setup, tmp_path):
    from backend.storage.recovery import create_snapshot, restore_snapshot
    client, args = setup
    client.post('/api/notes', json={'title': 'source', 'content': 'needle'})
    view = create_view(client)
    create_snapshot(tmp_path, tmp_path.parent / (tmp_path.name + '-snapshot'))
    restored = tmp_path.parent / (tmp_path.name + '-restore')
    restore_snapshot(tmp_path.parent / (tmp_path.name + '-snapshot'), restored)
    other = TestClient(create_app(str(restored / 'knowledge_os.db'), str(restored / 'raw_sources'), str(restored / 'artifacts')))
    assert other.get(f"/api/saved-views/{view['id']}").json() == view
    assert execute(other, view).json()['total'] == 1
    assert other.get(f"/api/saved-views/{view['id']}/history").json()['total'] == 1


@pytest.mark.asyncio
async def test_readonly_saved_views_over_real_stdio(setup, tmp_path):
    from pathlib import Path
    import sys
    from mcp import Client, StdioServerParameters
    client, _ = setup
    view = create_view(client)
    source = client.post('/api/notes', json={'title': 'source', 'content': 'needle'}).json()
    script = Path(__file__).resolve().parents[1] / 'scripts/run_mcp.py'
    params = StdioServerParameters(command=sys.executable, args=[str(script), '--data-dir', str(tmp_path),
        '--scope', 'default', '--principal', 'saved-view-reader', '--read-only'], cwd=str(tmp_path),
        env={'AI_LIBRARY_DATA_DIR': str(tmp_path / 'module-data')})
    async with Client(params, read_timeout_seconds=15) as mcp:
        tools = {t.name for t in (await mcp.list_tools()).tools}
        assert {'list_saved_views', 'get_saved_view', 'execute_saved_view'} <= tools
        assert 'create_saved_view' not in tools
        result = await mcp.call_tool('execute_saved_view', {'view_id': view['id'], 'expected_revision': 1})
        assert not result.is_error
        assert json.loads(result.content[0].text)['items'][0]['id'] == source['id']
        assert (await mcp.call_tool('execute_saved_view', {'view_id': view['id'], 'expected_revision': 1, 'scope': 'private'})).is_error
        client.patch(f"/api/saved-views/{view['id']}", json={'expected_revision': 1, 'name': 'new definition'})
        assert (await mcp.call_tool('execute_saved_view', {'view_id': view['id'], 'expected_revision': 1})).is_error
        listed = json.loads((await mcp.call_tool('list_saved_views', {'workspace': 'notes'})).content[0].text)
        assert listed['items'][0]['revision'] == 2
        fetched = json.loads((await mcp.call_tool('get_saved_view', {'view_id': view['id']})).content[0].text)
        assert fetched['name'] == 'new definition'
