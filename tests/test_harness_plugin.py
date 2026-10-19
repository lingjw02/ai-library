"""Native harness reads must remain scoped, bounded and source-attributed."""
import json
import pytest
from backend.adapters.mcp.stdio_server import build_service
from backend.schema.models import GraphNode, NodeType, Provenance


def bridge_for(tmp_path, *, max_context_chars=6000, max_items=3, excerpt_chars=240):
    from backend.adapters.harness.context_bridge import HarnessContextBridge
    service = build_service(tmp_path, 'project-a', True, 'deepseek-harness', 'deepseek')
    return HarnessContextBridge(service, max_context_chars=max_context_chars,
                                max_items=max_items, excerpt_chars=excerpt_chars)


def seed(service, *, content='SQLite canonical evidence', scope='project-a', identity='memory-one'):
    note = service.notes_mgr.create_note('Source', content, scope=scope)
    service.storage.upsert_node(GraphNode(id=identity, node_type=NodeType.FACT,
        title='SQLite decision', content=content, scope=scope,
        verification_props={'status': 'UNVERIFIED'},
        provenance=Provenance(source_id=note.id, source_revision=1, content_hash=note.content_hash)))
    return note


def test_prepare_keeps_exact_source_and_does_not_return_other_scope(tmp_path):
    bridge = bridge_for(tmp_path)
    note = seed(bridge.service)
    seed(bridge.service, scope='secret', identity='hidden', content='SQLite SECRET')
    packet = bridge.handle({'operation': 'prepare', 'query': 'SQLite', 'session_key': 'chat-a'})
    assert packet['scope'] == 'project-a'
    assert 'SECRET' not in packet['context_text']
    assert note.id in packet['context_text'] and note.content_hash in packet['context_text']
    assert 'UNVERIFIED' in packet['context_text']
    assert 'untrusted' in packet['context_text'].lower()
    assert 'combined_prompt' not in packet and 'stable_context' not in packet
    assert packet['accounting']['unit'] == 'characters_not_model_tokens'
    assert len(packet['context_text']) <= 6000
    bridge.close()


def test_large_source_is_an_explicit_excerpt_with_unchanged_original_hash(tmp_path):
    bridge = bridge_for(tmp_path, max_context_chars=2800, max_items=1, excerpt_chars=80)
    note = seed(bridge.service, content='SQLite evidence. ' * 500)
    packet = bridge.handle({'operation': 'prepare', 'query': 'SQLite', 'session_key': 'chat-a'})
    assert packet['partial']
    assert packet['evidence_count'] == 1
    assert 'excerpt_only' in packet['context_text']
    assert '"partial":true' in packet['context_text']
    assert '"scope":"project-a"' in packet['context_text']
    assert len(packet['context_text']) <= 2800
    assert bridge.service.notes_mgr.get_note(note.id).content_hash == note.content_hash
    bridge.close()


def test_prepare_never_reuses_stale_content_or_revoked_permission(tmp_path):
    bridge = bridge_for(tmp_path)
    seed(bridge.service)
    first = bridge.handle({'operation': 'prepare', 'query': 'SQLite', 'session_key': 'chat-a'})
    assert 'canonical evidence' in first['context_text']
    node = bridge.service.storage.get_node('memory-one')
    node.content = 'SQLite updated canonical evidence'
    bridge.service.storage.upsert_node(node)
    second = bridge.handle({'operation': 'prepare', 'query': 'SQLite', 'session_key': 'chat-a'})
    assert 'updated canonical evidence' in second['context_text']
    grant = bridge.service.access_policy.get_grant('deepseek-harness', 'project-a')
    bridge.service.access_policy.update_grant('deepseek-harness', 'project-a',
        read=False, write=False, expected_revision=grant['revision'], actor='owner')
    with pytest.raises(PermissionError):
        bridge.handle({'operation': 'prepare', 'query': 'SQLite', 'session_key': 'chat-a'})
    bridge.close()


@pytest.mark.parametrize('frame', [
    {'operation': 'create_user_note', 'query': 'SQLite', 'session_key': 'chat-a'},
    {'operation': 'prepare', 'query': 'SQLite', 'session_key': 'chat-a', 'scope': 'secret'},
    {'operation': 'prepare', 'query': '', 'session_key': 'chat-a'},
    {'operation': 'prepare', 'query': 'SQLite', 'session_key': ''},
])
def test_protocol_rejects_writes_scope_override_and_invalid_input(tmp_path, frame):
    bridge = bridge_for(tmp_path)
    with pytest.raises(ValueError): bridge.handle(frame)
    bridge.close()


def test_mecha_sessions_are_distinct_and_clean_up(tmp_path):
    bridge = bridge_for(tmp_path)
    seed(bridge.service)
    for key in ('chat-a', 'chat-b'):
        bridge.handle({'operation': 'prepare', 'query': 'SQLite', 'session_key': key})
    snapshot = bridge.service.spider_activity.snapshot('project-a')
    app = next(a for a in snapshot['applications'] if a['id'] == 'deepseek')
    assert len(app['sessions']) == 2
    bridge.handle({'operation': 'end_session', 'session_key': 'chat-a'})
    bridge.close()
    snapshot = bridge.service.spider_activity.snapshot('project-a')
    assert all(not a['sessions'] for a in snapshot['applications'])


def test_conflict_warning_survives_excerpt_compaction(tmp_path):
    bridge = bridge_for(tmp_path, max_items=1)
    seed(bridge.service)
    bridge.service.storage.upsert_node(GraphNode(id='reported-conflict', node_type=NodeType.CONFLICT,
        title='Open contradiction', content='The storage choices disagree', scope='project-a',
        core_props={'status': 'OPEN', 'conflicting_nodes': ['memory-one']}))
    packet = bridge.handle({'operation': 'prepare', 'query': 'memory-one', 'session_key': 'chat-a'})
    assert 'reported-conflict' in packet['context_text']
    assert 'conflict' in packet['context_text'].lower()
    bridge.close()


def topics_for(bridge):
    manager = bridge.service.collections
    parent = manager.create('Project A', scope='project-a')
    child = manager.create('Child topic', scope='project-a', parent_id=parent['id'])
    return parent, child


def member(bridge, topic, identity):
    bridge.service.collections.membership(topic['id'], 'project-a', 'memory', identity, topic['revision'])
    topic['revision'] += 1


def test_catalog_and_selection_use_existing_topics_without_new_projects(tmp_path):
    bridge = bridge_for(tmp_path)
    parent, child = topics_for(bridge)
    hidden = bridge.service.collections.create('PRIVATE TITLE', scope='secret')
    catalog = bridge.handle({'operation': 'list_projects', 'limit': 1})
    assert catalog['total'] == 2 and catalog['has_more']
    assert len(catalog['projects']) == 1
    assert 'PRIVATE TITLE' not in json.dumps(catalog)
    selected = bridge.handle({'operation': 'select_project', 'project_id': parent['id']})
    assert selected['project']['id'] == parent['id']
    assert bridge.handle({'operation': 'select_project'})['project'] is None
    with pytest.raises(ValueError): bridge.handle({'operation': 'select_project', 'project_id': hidden['id']})
    bridge.close()


@pytest.mark.parametrize('indexed', [False, True])
def test_project_filter_precedes_ranking_and_includes_child_members(tmp_path, indexed):
    bridge = bridge_for(tmp_path)
    parent, child = topics_for(bridge)
    seed(bridge.service, identity='selected', content='SQLite selected child evidence')
    member(bridge, child, 'selected')
    for n in range(24):
        seed(bridge.service, identity=f'outside-{n}', content='SQLite SQL SQLite SQL outside project')
    if indexed: bridge.service.coordinator.keyword_index.rebuild()
    selected = bridge.handle({'operation': 'prepare', 'query': 'SQLite', 'session_key': 'chat-a', 'project_id': parent['id']})
    assert 'selected child evidence' in selected['context_text']
    assert 'outside project' not in selected['context_text']
    assert parent['id'] in selected['context_text']
    outside_exact = bridge.handle({'operation': 'prepare', 'query': 'outside-0', 'session_key': 'chat-a', 'project_id': parent['id']})
    assert outside_exact['evidence_count'] == 0
    whole = bridge.handle({'operation': 'prepare', 'query': 'outside-0', 'session_key': 'chat-b'})
    assert 'outside project' in whole['context_text']
    bridge.close()


def test_missing_topic_or_revoked_permission_never_falls_back_to_whole_library(tmp_path):
    bridge = bridge_for(tmp_path)
    seed(bridge.service)
    with pytest.raises(ValueError):
        bridge.handle({'operation': 'prepare', 'query': 'SQLite', 'session_key': 'chat-a', 'project_id': 'missing'})
    grant = bridge.service.access_policy.get_grant('deepseek-harness', 'project-a')
    bridge.service.access_policy.update_grant('deepseek-harness', 'project-a', read=False, write=False,
        expected_revision=grant['revision'], actor='owner')
    with pytest.raises(PermissionError): bridge.handle({'operation': 'list_projects'})
    bridge.close()


def test_empty_project_and_graph_queries_cannot_broaden_selection(tmp_path):
    from backend.schema.models import GraphEdge, RelationshipType
    bridge = bridge_for(tmp_path)
    parent, child = topics_for(bridge)
    seed(bridge.service, identity='selected', content='SQLite project evidence')
    seed(bridge.service, identity='neighbor', content='PRIVATE NEIGHBOR CONTENT')
    member(bridge, child, 'selected')
    bridge.service.storage.upsert_edge(GraphEdge(id='connected', source_id='selected', target_id='neighbor',
        scope='project-a', relation=RelationshipType.RELATED_TO))
    packet = bridge.handle({'operation': 'prepare', 'query': 'related to selected', 'session_key': 'chat-a', 'project_id': parent['id']})
    assert 'PRIVATE NEIGHBOR CONTENT' not in packet['context_text']
    empty = bridge.service.collections.create('Empty', scope='project-a')
    packet = bridge.handle({'operation': 'prepare', 'query': 'SQLite', 'session_key': 'chat-a', 'project_id': empty['id']})
    assert packet['evidence_count'] == 0 and packet['project']['id'] == empty['id']
    bridge.close()


def test_membership_removal_during_prepare_drops_the_result(tmp_path, monkeypatch):
    bridge = bridge_for(tmp_path)
    parent, child = topics_for(bridge)
    seed(bridge.service, identity='selected', content='SQLite removed evidence')
    member(bridge, child, 'selected')
    original = bridge._call
    def remove_after_read(name, values):
        result = original(name, values)
        if name == 'get_memory_node':
            with bridge.service.storage.session() as conn:
                conn.execute('DELETE FROM collection_members WHERE topic_id=? AND object_id=?', (child['id'], 'selected'))
        return result
    monkeypatch.setattr(bridge, '_call', remove_after_read)
    packet = bridge.handle({'operation': 'prepare', 'query': 'SQLite', 'session_key': 'chat-a', 'project_id': parent['id']})
    assert packet['partial'] and packet['evidence_count'] == 0
    assert 'removed evidence' not in packet['context_text']
    bridge.close()


def test_exact_selected_object_is_not_lost_to_compiler_order_and_excerpt_limit(tmp_path):
    bridge = bridge_for(tmp_path, max_items=1)
    parent, child = topics_for(bridge)
    for identity in ['mem-000', 'mem-001', 'mem-002', 'mem-target']:
        seed(bridge.service, identity=identity, content='mem target project evidence')
        member(bridge, child, identity)
    packet = bridge.handle({'operation': 'prepare', 'query': 'mem-target', 'session_key': 'chat-a', 'project_id': parent['id']})
    assert packet['evidence_refs'][0]['id'] == 'mem-target'
    bridge.close()
