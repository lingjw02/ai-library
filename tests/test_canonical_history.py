from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.schema.models import GraphNode, GraphEdge, NodeType, RelationshipType
from backend.storage.canonical.sqlite_storage import CanonicalStorage, CanonicalRevisionConflict


def make_node(id='one', scope='a', content='original'):
    return GraphNode(id=id, scope=scope, content=content, title=id, node_type=NodeType.FACT)


def test_update_retains_immutable_history_and_rejects_stale_write(tmp_path):
    storage = CanonicalStorage(str(tmp_path / 'db'))
    node = make_node()
    storage.upsert_node(node)
    node.content = 'second'
    storage.upsert_node(node, expected_revision=1)
    assert storage.get_node_revision(node.id, 1, scope='a').content == 'original'
    assert storage.get_node_revision(node.id, 2, scope='a').content == 'second'
    with pytest.raises(CanonicalRevisionConflict):
        storage.upsert_node(make_node(content='stale'), expected_revision=1)
    assert storage.get_node('one').content == 'second'
    assert [x['version'] for x in storage.list_node_revisions('one', scope='a')] == [2, 1]


def test_history_respects_current_scope_and_deactivation(tmp_path):
    storage = CanonicalStorage(str(tmp_path / 'db'))
    node = make_node()
    storage.upsert_node(node)
    node.scope = 'b'
    storage.upsert_node(node, expected_revision=1)
    assert storage.get_node_revision('one', 1, scope='a') is None
    assert storage.get_node_revision('one', 1, scope='b') is None
    assert storage.get_node_revision('one', 2, scope='b')
    node.is_active = False
    storage.upsert_node(node, expected_revision=2)
    assert storage.list_node_revisions('one', scope='b') == []


def test_transaction_rollback_and_concurrent_revision(tmp_path):
    storage = CanonicalStorage(str(tmp_path / 'db'))
    storage.upsert_node(make_node())
    with pytest.raises(RuntimeError):
        with storage.session() as conn:
            conn.execute('BEGIN IMMEDIATE')
            storage.upsert_node(make_node(content='rolled back'), conn=conn, expected_revision=1)
            raise RuntimeError('injected failure')
    assert len(storage.list_node_revisions('one', scope='a')) == 1
    def update(content):
        try:
            storage.upsert_node(make_node(content=content), expected_revision=1)
            return True
        except CanonicalRevisionConflict:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, ['first writer', 'second writer'])) == [False, True]
    assert len(storage.list_node_revisions('one', scope='a')) == 2


def test_edge_versions_preserve_endpoints_and_rollback(tmp_path):
    storage = CanonicalStorage(str(tmp_path / 'db'))
    for id in ['one', 'two', 'three']:
        storage.upsert_node(make_node(id))
    edge = GraphEdge(id='link', source_id='one', target_id='two', scope='a', relation=RelationshipType.RELATED_TO)
    storage.upsert_edge(edge)
    edge.target_id = 'three'
    storage.upsert_edge(edge, expected_revision=1)
    assert storage.get_edge_revision('link', 1, scope='a').target_id == 'two'
    assert storage.get_edge_revision('link', 2, scope='a').target_id == 'three'
    assert storage.get_edges_for_node('two') == []
    assert storage.get_edges_for_node('three')[0].version == 2


def test_existing_snapshot_bootstrap_does_not_invent_old_history(tmp_path):
    storage = CanonicalStorage(str(tmp_path / 'db'))
    node = make_node()
    node.version = 7
    storage.upsert_node(node)
    with storage.session() as conn:
        conn.execute('DELETE FROM node_revisions')  # Simulate a pre-migration DB.
    restarted = CanonicalStorage(str(tmp_path / 'db'))
    assert [r['version'] for r in restarted.list_node_revisions('one', scope='a')] == [7]
    assert restarted.get_node_revision('one', 1, scope='a') is None


def test_api_requires_revision_on_update_and_scope_on_history(tmp_path):
    from fastapi.testclient import TestClient
    from backend.adapters.api.app import create_app
    with TestClient(create_app(str(tmp_path/'api.db'), str(tmp_path/'raw'), str(tmp_path/'artifacts'))) as client:
        data = make_node().model_dump(mode='json')
        assert client.post('/api/memory/nodes', json=data).status_code == 201
        assert client.post('/api/memory/nodes', json=data).status_code == 428
        data['content'] = 'new'
        assert client.post('/api/memory/nodes?expected_revision=1', json=data).status_code == 201
        assert client.post('/api/memory/nodes?expected_revision=1', json=data).status_code == 409
        assert client.get('/api/memory/nodes/one?revision=1&scope=a').json()['content'] == 'original'
        assert client.get('/api/memory/nodes/one?revision=1&scope=b').status_code == 404
        assert len(client.get('/api/memory/nodes/one/revisions?scope=a').json()) == 2
