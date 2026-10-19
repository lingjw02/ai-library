"""Collections remain references; the universe reads canonical current content."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from backend.adapters.api.app import create_app


@pytest.fixture
def setup(tmp_path):
    args = (str(tmp_path / 'db'), str(tmp_path / 'raw'), str(tmp_path / 'art'))
    return TestClient(create_app(*args)), args


def topic(client, title='工程', **kwargs):
    response = client.post('/api/topics', json={'title': title, **kwargs})
    assert response.status_code == 201, response.text
    return response.json()


def note(client, title='原文', content='保留原文', **kwargs):
    return client.post('/api/notes', json={'title': title, 'content': content, **kwargs}).json()


def member(client, t, n, method='POST', kind='note', **kwargs):
    return client.request(method, f"/api/topics/{t['id']}/members", json={
        'scope': t['scope'], 'expected_revision': t['revision'],
        'kind': kind, 'object_id': n['id'], **kwargs})


def test_persistent_membership_rename_remove_preserves_note(setup):
    client, args = setup
    n = note(client)
    t = topic(client)
    t = member(client, t, n).json()
    restarted = TestClient(create_app(*args))
    result = restarted.get('/api/universe', params={'topic_id': t['id']}).json()
    assert result['items'][0]['id'] == n['id']
    assert result['topic']['count'] == 1
    renamed = restarted.patch(f"/api/topics/{t['id']}", json={
        'title': '结构', 'expected_revision': t['revision']}).json()
    assert renamed['title'] == '结构'
    assert member(restarted, renamed, n, method='DELETE').status_code == 200
    assert restarted.get(f"/api/notes/{n['id']}").json() == n
    assert restarted.get('/api/universe', params={'topic_id': 'unclassified'}).json()['total'] == 1


def test_duplicate_add_is_idempotent_and_stale_changes_are_rejected(setup):
    client, _ = setup
    n, t = note(client), topic(client)
    added = member(client, t, n).json()
    again = member(client, added, n)
    assert again.status_code == 200 and again.json()['revision'] == added['revision']
    assert member(client, t, n, method='DELETE').status_code == 409
    assert client.patch(f"/api/topics/{t['id']}", json={'title': 'lost', 'expected_revision': 1}).status_code == 409
    assert client.patch(f"/api/topics/{t['id']}", json={'title': 'lost'}).status_code == 422


def test_concurrent_membership_cas_allows_one_change(setup):
    client, _ = setup
    t = topic(client)
    notes = [note(client, title=str(i)) for i in range(3)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda n: member(client, t, n), notes))
    assert sorted(r.status_code for r in results) == [200, 409, 409]
    assert client.get('/api/universe', params={'topic_id': t['id']}).json()['total'] == 1


def test_scope_membership_parent_and_immutable_hierarchy(setup):
    client, _ = setup
    private = topic(client, scope='private')
    t = topic(client)
    foreign = note(client, scope='private')
    assert member(client, t, foreign).status_code == 404
    assert member(client, private, foreign, scope='default').status_code == 404
    assert client.post('/api/topics', json={'title': 'bad', 'parent_id': private['id']}).status_code == 404
    assert client.get('/api/universe', params={'topic_id': private['id']}).status_code == 404
    child = topic(client, '子类', parent_id=t['id'])
    assert client.patch(f"/api/topics/{t['id']}", json={
        'title': 'bad', 'parent_id': child['id'], 'expected_revision': 1}).status_code == 422
    assert member(client, t, t, kind='topic').status_code == 422
    assert client.get('/api/topics').json()['total'] == 2
    assert client.get('/api/universe', params={'topic_id': child['id']}).json()['breadcrumbs'] == [
        {'id': t['id'], 'title': t['title']}, {'id': child['id'], 'title': child['title']}]


def test_bounded_paging_current_search_and_locate_across_planes(setup):
    client, _ = setup
    notes = [note(client, title=f'编号 {i}', content='needle') for i in range(5)]
    t = topic(client)
    member(client, t, notes[0])
    candidate = client.post('/api/candidates', json={
        'source_id': notes[0]['id'], 'claim': 'needle memory', 'suggested_node_type': 'Concept',
        'payload': {'title': '记忆'}}).json()
    memory = client.post(f"/api/candidates/{candidate['id']}/accept").json()['promoted_node']
    found = client.get('/api/universe', params={'query': 'needle', 'limit': 2}).json()
    assert found['total'] == 6 and len(found['items']) == 2 and found['partial']
    pages = [client.get('/api/universe', params={'query': 'needle', 'limit': 2, 'offset': i}).json()
             for i in (0, 2, 4)]
    items = [item for page in pages for item in page['items']]
    assert len({(i['kind'], i['id']) for i in items}) == 6
    assert next(i for i in items if i['id'] == notes[0]['id'])['topic_ids'] == [t['id']]
    assert next(i for i in items if i['id'] == memory['id'])['kind'] == 'memory'
    assert client.get('/api/universe', params={'query': 'needle', 'kind': 'memory'}).json()['total'] == 1
    all_result = client.get('/api/universe', params={'topic_id': 'all'}).json()
    assert all_result['counts']['memories'] == 2  # Includes real Source object, without invented text.
    assert client.get('/api/universe', params={'limit': 501}).status_code == 422
    assert client.get('/api/universe', params={'offset': -1}).status_code == 422
    client.post(f"/api/notes/{notes[1]['id']}/revisions", json={
        'title': '修订', 'content': 'replacement', 'expected_revision': 1})
    assert client.get('/api/universe', params={'query': 'needle', 'kind': 'note'}).json()['total'] == 4


def test_marker_import_explicit_idempotent_and_does_not_restore_removed_members(setup):
    client, args = setup
    n = note(client, title='【工程】原文')
    note(client, title='Unknown')
    assert client.get('/api/universe').json()['topics'] == []
    response = client.post('/api/topics/import-markers', json={}).json()
    assert response['topics_created'] == 1 and response['members_added'] == 1
    t = client.get('/api/topics').json()['topics'][0]
    assert t['title'] == '工程'
    assert member(client, t, n, method='DELETE').status_code == 200
    restarted = TestClient(create_app(*args))
    assert restarted.post('/api/topics/import-markers', json={}).json()['already_imported']
    assert restarted.get('/api/universe', params={'topic_id': t['id']}).json()['total'] == 0
    assert restarted.get(f"/api/notes/{n['id']}").json() == n
    with sqlite3.connect(args[0]) as conn:
        row = conn.execute('SELECT original_title, object_revision FROM collection_marker_evidence').fetchone()
    assert row == ('【工程】原文', 1)


def test_many_topics_page_without_silent_truncation_and_empty_input_rejected(setup):
    client, _ = setup
    for i in range(4):
        topic(client, str(i))
    first = client.get('/api/topics', params={'limit': 2}).json()
    second = client.get('/api/topics', params={'limit': 2, 'offset': 2}).json()
    assert first['total'] == 4 and first['partial']
    assert len({t['id'] for t in first['topics'] + second['topics']}) == 4
    assert client.get('/api/universe', params={'limit': 2}).json()['total'] == 4
    assert client.post('/api/topics', json={'title': '   '}).status_code == 422
    assert client.get('/api/universe', params={'scope': ' '}).status_code == 422


def test_mixed_child_content_page_and_multi_topic_reference_tracks_revision(setup):
    client, _ = setup
    parent = topic(client)
    other = topic(client, '其它')
    child = topic(client, '子类', parent_id=parent['id'])
    n = note(client)
    parent = member(client, parent, n).json()
    member(client, other, n)
    first = client.get('/api/universe', params={'topic_id': parent['id'], 'limit': 1}).json()
    second = client.get('/api/universe', params={'topic_id': parent['id'], 'limit': 1, 'offset': 1}).json()
    assert first['total'] == second['total'] == 2
    assert first['topics'][0]['id'] == child['id'] and first['items'] == []
    assert second['topics'] == [] and second['items'][0]['id'] == n['id']
    client.post(f"/api/notes/{n['id']}/revisions", json={
        'title': 'current 100%_literal', 'content': 'fresh', 'expected_revision': 1})
    for t in (parent, other):
        item = client.get('/api/universe', params={'topic_id': t['id']}).json()['items'][0]
        assert item['revision'] == 2 and item['title'] == 'current 100%_literal'
        assert set(item['topic_ids']) == {parent['id'], other['id']}
    assert client.get('/api/universe', params={'query': '%_'}).json()['total'] == 1
    assert client.get('/api/universe', params={'query': 'unmatched%'}).json()['total'] == 0


def test_memory_membership_and_import_never_change_canonical_content(setup):
    client, args = setup
    n = note(client, title='【工程】原文')
    candidate = client.post('/api/candidates', json={
        'source_id': n['id'], 'claim': 'Memory claim', 'suggested_node_type': 'Concept',
        'payload': {'title': '【工程】Memory'}}).json()
    memory = client.post(f"/api/candidates/{candidate['id']}/accept").json()['promoted_node']
    def canonical_snapshot():
        with sqlite3.connect(args[0]) as conn:
            # Organization events are now required by §97. They must not change
            # canonical content or add memory-operation Logsheet records.
            return {table: conn.execute(f'SELECT * FROM {table}' +
                    (" WHERE aggregate_type != 'COLLECTION'" if table == 'transactional_outbox' else '') + ' ORDER BY rowid').fetchall()
                    for table in ('notes', 'nodes', 'node_revisions', 'edges', 'edge_revisions',
                                  'candidates', 'raw_manifests', 'transactional_outbox', 'system_memory_logsheet')}
    before = canonical_snapshot()
    t = topic(client)
    t = member(client, t, memory, kind='memory').json()
    assert member(client, t, memory, kind='memory', method='DELETE').status_code == 200
    assert client.post('/api/topics/import-markers', json={}).status_code == 200
    assert canonical_snapshot() == before
    with sqlite3.connect(args[0]) as conn:
        conn.execute('UPDATE nodes SET is_active = 0 WHERE id = ?', (memory['id'],))
    assert all(i['id'] != memory['id'] for i in client.get('/api/universe', params={'topic_id': 'all'}).json()['items'])


def test_import_failure_rolls_back_topics_members_and_migration_marker(setup):
    client, args = setup
    note(client, title='【工程】原文')
    with sqlite3.connect(args[0]) as conn:
        conn.execute("""CREATE TRIGGER reject_marker BEFORE INSERT ON collection_marker_evidence
            BEGIN SELECT RAISE(ABORT, 'injected evidence failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match='injected evidence failure'):
        client.post('/api/topics/import-markers', json={})
    assert client.get('/api/topics').json()['total'] == 0
    with sqlite3.connect(args[0]) as conn:
        assert conn.execute('SELECT COUNT(*) FROM collection_imports').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM collection_members').fetchone()[0] == 0
