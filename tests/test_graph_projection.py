import pytest
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.schema.models import GraphNode, GraphEdge, NodeType, RelationshipType
from backend.auth.policy import AccessPolicy


def env(tmp_path):
    from backend.projections.manager import GraphProjection, ViewStateStore
    storage = CanonicalStorage(str(tmp_path / 'db'))
    for nid in ('a', 'b', 'c'):
        storage.upsert_node(GraphNode(id=nid, title=nid, content=nid, node_type=NodeType.FACT, scope='x'))
    storage.upsert_edge(GraphEdge(id='ab', source_id='a', target_id='b', relation=RelationshipType.RELATED_TO, scope='x'))
    return storage, GraphProjection(storage), ViewStateStore(storage)


def test_projection_rebuild_and_incremental_replay_have_contiguous_checkpoint(tmp_path):
    storage, graph, _ = env(tmp_path)
    first = graph.rebuild()
    page = graph.query(scope='x', node_budget=2)
    assert len(page['nodes']) == 2 and page['continuation']
    assert len(graph.query(scope='x', node_budget=2, cursor=page['continuation'])['nodes']) == 1
    node = storage.get_node('a')
    node.content = 'Changed'
    storage.upsert_node(node, expected_revision=1)
    before = graph.query(scope='x')
    assert before['coverage'] == 'degraded'
    assert 'a' not in [n['id'] for n in before['nodes']]
    result = graph.pump()
    assert result['checkpoint'] > first['checkpoint']
    assert graph.query(scope='x')['nodes'][0]['content'] == 'Changed'
    assert graph.pump()['checkpoint'] == result['checkpoint']


def test_tombstones_validity_scope_and_old_cursors_are_safe(tmp_path):
    storage, graph, _ = env(tmp_path)
    graph.rebuild()
    cursor = graph.query(scope='x', node_budget=1)['continuation']
    node = storage.get_node('a')
    node.is_active = False
    storage.upsert_node(node, expected_revision=1)
    assert 'a' not in [n['id'] for n in graph.query(scope='x')['nodes']]
    graph.pump()
    assert graph.query(scope='y')['nodes'] == []
    assert graph.query(scope='x')['edges'] == []
    with pytest.raises(ValueError):
        graph.query(scope='x', cursor=cursor)
    old_generation = graph.status()['generation']
    assert graph.rebuild()['generation'] != old_generation


def test_owner_viewstate_cas_reset_and_filtered_references_do_not_change_knowledge(tmp_path):
    storage, graph, layout = env(tmp_path)
    graph.rebuild()
    view = layout.save('owner', 'workspace', 'x', {'node_positions': {'a': {'x': 1, 'y': 2}, 'private': {'x': 0, 'y': 0}},
        'pinned_nodes': ['a', 'private'], 'viewport': {'x': 0, 'y': 0, 'zoom': 2}}, expected_revision=0)
    assert set(view['state']['node_positions']) == {'a'}
    assert view['state']['pinned_nodes'] == ['a']
    assert layout.get('other', 'workspace', 'x')['revision'] == 0
    with pytest.raises(ValueError):
        layout.save('owner', 'workspace', 'x', {}, expected_revision=0)
    with pytest.raises(ValueError):
        layout.save('owner', 'workspace', 'x', {'viewport': {'x': float('nan'), 'y': 0, 'zoom': 1}}, expected_revision=1)
    layout.reset('owner', 'workspace', 'x', expected_revision=1)
    assert len(storage.list_nodes(scope='x')) == 3
    assert layout.get('owner', 'workspace', 'x')['state']['node_positions'] == {}


def test_projection_and_layout_agent_permissions(tmp_path):
    storage, graph, layout = env(tmp_path)
    graph.rebuild()
    policy = AccessPolicy(storage)
    policy.ensure_launch_grant('agent', 'x')
    with policy.activate('agent', 'x', write=False):
        assert len(graph.query(scope='x')['nodes']) == 3
        with pytest.raises(PermissionError):
            graph.query(scope='y')
        with pytest.raises(PermissionError):
            graph.rebuild()
        with pytest.raises(PermissionError):
            layout.get('owner', 'workspace', 'x')
