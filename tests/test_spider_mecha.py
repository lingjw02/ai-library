"""Real MCP boundary and owner snapshots; no animation can invent these events."""
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from backend.adapters.api.app import create_app
from backend.adapters.mcp.stdio_server import build_service
from backend.collections.manager import CollectionManager


@pytest.fixture
def world(tmp_path):
    service = build_service(tmp_path, 'default', False, 'codex')
    topics = CollectionManager(service.storage)
    a = topics.create('Root')
    b = topics.create('Neighbor', parent_id=a['id'])
    c = topics.create('Second hop', parent_id=b['id'])
    other = topics.create('Unrelated')
    n = service.notes_mgr.create_note('Relevant source', 'prefix ' * 80 + 'needle evidence ' + 'suffix ' * 80)
    private = service.notes_mgr.create_note('Foreign', 'secret needle', scope='private')
    unrelated = service.notes_mgr.create_note('Unrelated', 'needle elsewhere')
    topics.membership(a['id'], 'default', 'note', n.id, a['revision'])
    topics.membership(other['id'], 'default', 'note', unrelated.id, other['revision'])
    client = TestClient(create_app(str(tmp_path/'knowledge_os.db'), str(tmp_path/'raw_sources'), str(tmp_path/'artifacts')))
    return service, client, {'a':a['id'], 'b':b['id'], 'c':c['id'], 'other':other['id'], 'note':n, 'private':private}, tmp_path


def invoke(service, name, **args):
    result = service.execute_tool(name, args)
    assert not result.get('isError'), result
    return json.loads(result['content'][0]['text'])


def begin(service, key='chat-a', label='First chat'):
    return invoke(service, 'begin_spider_session', session_key=key, label=label)['session_id']


def snapshot(client, scope='default'):
    response = client.get('/api/spider-mecha', params={'scope':scope})
    assert response.status_code == 200, response.text
    return response.json()


def sessions(client):
    return [s for a in snapshot(client)['applications'] for s in a['sessions']]


def test_snapshot_is_read_only_and_empty_before_activity(world):
    _, client, _, _ = world
    assert snapshot(client)['applications'] == []
    assert client.post('/api/spider-mecha', json={}).status_code == 405


def test_two_chats_share_application_but_keep_separate_positions(world):
    service, client, ids, _ = world
    first, second = begin(service), begin(service, 'chat-b', 'Second chat')
    assert begin(service) == first
    invoke(service, 'drive_spider', session_id=first, action='navigate', topic_id=ids['a'])
    invoke(service, 'drive_spider', session_id=second, action='navigate', topic_id=ids['other'])
    apps = snapshot(client)['applications']
    assert len(apps) == 1 and apps[0]['id'] == 'codex'
    by_id = {s['id']:s for s in apps[0]['sessions']}
    assert by_id[first]['position']['topic_id'] == ids['a']
    assert by_id[second]['position']['topic_id'] == ids['other']
    assert {s['identity_kind'] for s in by_id.values()} == {'chat'}


def test_scope_and_connection_cannot_impersonate_session(world):
    service, client, _, path = world
    session = begin(service)
    other = build_service(path, 'default', False, 'codex')
    private = build_service(path, 'private', False, 'codex')
    for caller in (other, private):
        assert caller.execute_tool('set_spider_session_state', {'session_id':session,'state':'idle'}).get('isError')
    assert len(sessions(client)) == 1


def test_ordinary_reads_observe_real_results_without_copying_body(world):
    service, client, ids, _ = world
    session = begin(service)
    result = invoke(service, 'get_user_note', note_id=ids['note'].id, spider_session_id=session)
    assert result['content'] == ids['note'].content
    s = sessions(client)[0]
    assert s['position']['object_id'] == ids['note'].id
    assert any(e['object_id'] == ids['note'].id and e['status'] == 'completed' for e in s['events'])
    serialized = json.dumps(snapshot(client))
    assert ids['note'].content not in serialized and 'secret needle' not in serialized


def test_ambiguous_ordinary_read_is_connection_projection_not_last_chat(world):
    service, client, ids, _ = world
    first, second = begin(service), begin(service, 'chat-b')
    invoke(service, 'get_user_note', note_id=ids['note'].id)
    by_id = {s['id']:s for s in sessions(client)}
    assert by_id[first]['events'] == by_id[second]['events'] == []
    unregistered = [s for s in by_id.values() if s['identity_kind'] == 'connection']
    assert len(unregistered) == 1 and unregistered[0]['state'] == 'unknown'


def test_navigation_returns_only_relevant_scoped_member_snippets(world):
    service, _, ids, _ = world
    session = begin(service)
    result = invoke(service, 'drive_spider', session_id=session, action='navigate', topic_id=ids['a'], query='needle')
    assert [r['id'] for r in result['items']] == [ids['note'].id]
    assert 'needle evidence' in result['items'][0]['excerpt']
    assert len(result['items'][0]['excerpt']) < len(ids['note'].content)
    assert result['items'][0]['revision'] == 1
    assert 'content' not in result['items'][0]
    assert result['retrieval_mode'] == 'literal_relevance'


def test_catalogue_and_full_read_preserve_source_without_new_writes(world):
    service, _, ids, _ = world
    session = begin(service)
    invoke(service, 'drive_spider', session_id=session, action='navigate', topic_id=ids['a'])
    page = invoke(service, 'drive_spider', session_id=session, action='catalogue', limit=1)
    assert page['total'] == 1 and page['items'][0]['id'] == ids['note'].id
    full = invoke(service, 'drive_spider', session_id=session, action='read', kind='note', object_id=ids['note'].id)
    assert full['content'] == ids['note'].content
    assert invoke(service, 'inspect_system_memory_logsheet') == []
    assert invoke(service, 'inspect_system_memory_backlog') == []


def test_follow_is_one_hop_from_fixed_origin_not_transitive(world):
    service, client, ids, _ = world
    session = begin(service)
    invoke(service, 'drive_spider', session_id=session, action='navigate', topic_id=ids['a'])
    result = invoke(service, 'drive_spider', session_id=session, action='follow', topic_id=ids['b'])
    assert result['topic']['id'] == ids['b']
    assert service.execute_tool('drive_spider', {'session_id':session,'action':'follow','topic_id':ids['c']}).get('isError')
    s = next(s for s in sessions(client) if s['id'] == session)
    assert s['position']['topic_id'] == ids['b'] and s['mode'] == 'crawl'
    assert s['events'][-1]['status'] == 'failed'


def test_unrelated_or_foreign_targets_rejected_without_position_leak(world):
    service, client, ids, _ = world
    session = begin(service)
    invoke(service, 'drive_spider', session_id=session, action='navigate', topic_id=ids['a'])
    for args in [dict(action='follow',topic_id=ids['other']),
                 dict(action='read',kind='note',object_id=ids['private'].id),
                 dict(action='read',kind='note',object_id='missing')]:
        assert service.execute_tool('drive_spider', {'session_id':session,**args}).get('isError')
    s = sessions(client)[0]
    assert s['position']['topic_id'] == ids['a']
    assert ids['private'].id not in json.dumps(snapshot(client))
    assert all(e['object_id'] != ids['private'].id for e in s['events'])


def test_read_only_connections_can_drive_without_mutating_library(world):
    _, client, ids, path = world
    reader = build_service(path, 'default', True, 'antigravity')
    session = begin(reader)
    result = invoke(reader, 'drive_spider', session_id=session, action='navigate', topic_id=ids['a'])
    assert result['items'][0]['id'] == ids['note'].id
    assert reader.execute_tool('create_user_note', {'title':'forbidden','content':'x'}).get('isError')
    assert snapshot(client)['applications'][0]['id'] == 'antigravity'


def test_explicit_idle_docks_and_end_erases_new_session_history(world):
    service, client, ids, _ = world
    session = begin(service)
    invoke(service, 'get_user_note', note_id=ids['note'].id, spider_session_id=session)
    invoke(service, 'set_spider_session_state', session_id=session, state='idle')
    assert sessions(client)[0]['state'] == 'idle'
    assert sessions(client)[0]['events']
    invoke(service, 'end_spider_session', session_id=session)
    apps = snapshot(client)['applications']
    assert apps[0]['sessions'] == []
    with sqlite3.connect(service.spider_activity.path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM spider_events').fetchone()[0] == 0
    assert ids['note'].content == service.notes_mgr.get_note(ids['note'].id).content


def test_revoked_grant_hides_existing_projection_and_rejects_calls(world):
    service, client, ids, _ = world
    session = begin(service)
    invoke(service, 'get_user_note', note_id=ids['note'].id, spider_session_id=session)
    grant = service.access_policy.get_grant('codex','default')
    service.access_policy.update_grant('codex','default',read=False,write=False,expected_revision=grant['revision'],actor='local-owner')
    assert sessions(client) == []
    assert service.execute_tool('drive_spider', {'session_id':session,'action':'navigate','topic_id':ids['a']}).get('isError')


def test_expired_connection_is_disconnected_then_history_purged(world):
    service, client, ids, _ = world
    session = begin(service)
    invoke(service, 'get_user_note', note_id=ids['note'].id, spider_session_id=session)
    service.spider_activity.heartbeat(service.spider_connection, now=time.time()-45)
    assert sessions(client)[0]['state'] == 'disconnected'
    service.spider_activity.heartbeat(service.spider_connection, now=time.time()-180)
    assert sessions(client) == []


def test_removed_membership_and_moved_source_stop_disclosing_old_references(world):
    service, client, ids, _ = world
    session = begin(service)
    invoke(service, 'get_user_note', note_id=ids['note'].id, spider_session_id=session)
    with service.storage.session() as conn:
        conn.execute('UPDATE notes SET scope=? WHERE id=?', ('private',ids['note'].id))
    s = sessions(client)[0]
    assert s['position'].get('object_id') is None
    assert ids['note'].id not in json.dumps(s['events'])


def test_poll_history_is_bounded_and_distinct_processes_observe_same_activity(world):
    service, client, ids, path = world
    session = begin(service)
    for _ in range(110):
        invoke(service, 'get_user_note', note_id=ids['note'].id, spider_session_id=session)
    second_client = TestClient(create_app(str(path/'knowledge_os.db'),str(path/'raw_sources'),str(path/'artifacts')))
    one, two = sessions(client)[0], sessions(second_client)[0]
    assert one['events'] == two['events'] and 0 < len(one['events']) <= 100
    assert one['events'][-1]['status'] == 'completed'


def test_unknown_principal_does_not_masquerade_as_known_application(world):
    _, client, _, path = world
    service = build_service(path,'default',False,'mcp-agent')
    begin(service)
    app = snapshot(client)['applications'][0]
    assert app['id'] != 'codex' and app['id'] != 'antigravity'
    assert '未识别' in app['name']


def test_ordinary_read_survives_observation_storage_failure(world,monkeypatch):
    service, _, ids, _ = world
    def unavailable(*args,**kwargs):
        raise sqlite3.OperationalError('isolated telemetry failure')
    monkeypatch.setattr(service.spider_activity,'connect',unavailable)
    result = invoke(service,'get_user_note',note_id=ids['note'].id)
    assert result['content'] == ids['note'].content


def test_enter_and_full_read_cannot_skip_current_navigation(world):
    service, _, ids, _ = world
    session = begin(service)
    invoke(service,'drive_spider',session_id=session,action='navigate',topic_id=ids['a'])
    for action in ('enter','catalogue'):
        assert service.execute_tool('drive_spider',{'session_id':session,'action':action,'topic_id':ids['other']}).get('isError')
    assert service.execute_tool('drive_spider',{'session_id':session,'action':'read','kind':'note','object_id':ids['note'].id,'topic_id':ids['other']}).get('isError')


def test_application_can_only_be_set_by_trusted_launch_not_tool_args(world):
    _, client, _, path = world
    service = build_service(path,'default',False,'dedicated-library-reader',application='codex')
    assert service.execute_tool('begin_spider_session',{'session_key':'chat','label':'chat','application':'antigravity'}).get('isError')
    begin(service)
    assert snapshot(client)['applications'][0]['id']=='codex'
    assert service.access_policy.get_grant('antigravity','default') is None


def test_temporally_ineligible_memory_cannot_be_read_or_shown(world):
    from backend.schema.models import GraphNode,NodeType,TemporalValidity,utc_now
    from datetime import timedelta
    service, client, ids, _ = world
    node=GraphNode(id='expired-memory',node_type=NodeType.FACT,title='Expired',content='obsolete evidence',
                   temporal_props=TemporalValidity(valid_to=utc_now()-timedelta(days=1)))
    service.storage.upsert_node(node)
    topics=CollectionManager(service.storage)
    with service.storage.session() as conn:
        revision=conn.execute('SELECT revision FROM collection_topics WHERE id=?',(ids['a'],)).fetchone()[0]
    topics.membership(ids['a'],'default','memory',node.id,revision)
    session=begin(service)
    page=invoke(service,'drive_spider',session_id=session,action='navigate',topic_id=ids['a'])
    assert node.id not in [r['id'] for r in page['items']]
    assert service.execute_tool('drive_spider',{'session_id':session,'action':'read','kind':'memory','object_id':node.id}).get('isError')
    assert node.id not in json.dumps(snapshot(client))


def test_search_records_only_returned_evidence_and_reactivates_idle_chat(world):
    service,client,ids,_=world
    session=begin(service)
    invoke(service,'set_spider_session_state',session_id=session,state='idle')
    result=invoke(service,'search_knowledge_context',query='needle',plane='NOTES',spider_session_id=session)
    assert 'needle evidence' in result['dynamic_context']
    s=sessions(client)[0]
    assert s['state']=='active'
    assert any(e['object_id']==ids['note'].id and e['revision']==1 for e in s['events'])


def test_truncated_search_does_not_claim_unreturned_evidence(world):
    service,client,_,_=world
    session=begin(service)
    invoke(service,'set_spider_session_state',session_id=session,state='idle')
    result=invoke(service,'search_knowledge_context',query='needle',plane='NOTES',token_budget=1,spider_session_id=session)
    assert result['is_truncated']
    s=sessions(client)[0]
    assert s['state']=='active'
    assert not any(e['object_id'] for e in s['events'])


def test_driver_snippets_record_sources_without_moving_off_entered_topic(world):
    service,client,ids,_=world
    session=begin(service)
    invoke(service,'drive_spider',session_id=session,action='navigate',topic_id=ids['a'])
    s=sessions(client)[0]
    assert s['position']=={'topic_id':ids['a']}
    assert any(e['object_id']==ids['note'].id and e['revision']==1 for e in s['events'])


def test_repeat_navigation_does_not_reset_one_hop_origin(world):
    service,_,ids,_=world
    session=begin(service)
    invoke(service,'drive_spider',session_id=session,action='navigate',topic_id=ids['a'])
    invoke(service,'drive_spider',session_id=session,action='follow',topic_id=ids['b'])
    invoke(service,'drive_spider',session_id=session,action='navigate',topic_id=ids['b'])
    assert service.execute_tool('drive_spider',{'session_id':session,'action':'follow','topic_id':ids['c']}).get('isError')


def test_new_launch_purges_expired_traces_without_owner_poll_and_keeps_lost_state(world):
    service,_,ids,path=world
    session=begin(service)
    invoke(service,'get_user_note',note_id=ids['note'].id,spider_session_id=session)
    service.spider_activity.heartbeat(service.spider_connection,now=time.time()-180)
    other=build_service(path,'default',False,'antigravity')
    begin(other)
    with sqlite3.connect(other.spider_activity.path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM spider_sessions WHERE id=?',(session,)).fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM spider_events WHERE session_id=?',(session,)).fetchone()[0]==0
    app=next(a for a in other.spider_activity.snapshot('default')['applications'] if a['id']=='codex')
    assert app['state']=='disconnected' and app['sessions']==[]


def test_saved_view_notes_and_revision_lists_have_correct_source_identity(world):
    service,client,ids,_=world
    session=begin(service)
    view=client.post('/api/saved-views',json={'name':'notes','scope':'default','query':{
        'schema_version':'v1','workspace':'notes','filters':{'query':'needle'},'limit':2}}).json()
    invoke(service,'execute_saved_view',view_id=view['id'],expected_revision=1,spider_session_id=session)
    assert any(e['object_id']==ids['note'].id and e['kind']=='note' for e in sessions(client)[0]['events'])
    invoke(service,'list_note_revisions',note_id=ids['note'].id,spider_session_id=session)
    assert any(e['tool']=='list_note_revisions' and e['object_id']==ids['note'].id and e['revision']==1 for e in sessions(client)[0]['events'])


def test_optional_cleanup_contention_does_not_prevent_mcp_startup(world):
    service,_,ids,path=world
    session=begin(service)
    service.spider_activity.heartbeat(service.spider_connection,now=time.time()-180)
    with sqlite3.connect(service.spider_activity.path) as locked:
        locked.execute('BEGIN IMMEDIATE')
        other=build_service(path,'default',False,'antigravity')
        result=invoke(other,'get_user_note',note_id=ids['note'].id)
        assert result['content']==ids['note'].content


def test_failed_page_observation_does_not_claim_partial_returned_sources(world,monkeypatch):
    service,client,ids,_=world
    extra=service.notes_mgr.create_note('Second source','second page evidence')
    topics=CollectionManager(service.storage)
    with service.storage.session() as conn:
        revision=conn.execute('SELECT revision FROM collection_topics WHERE id=?',(ids['a'],)).fetchone()[0]
    topics.membership(ids['a'],'default','note',extra.id,revision)
    session=begin(service)
    original=service.spider_activity.event
    count=0
    def fail_after_first_source(*args,**kwargs):
        nonlocal count
        if args[2]=='read' and args[3]=='completed':
            count+=1
            if count==2:
                raise sqlite3.OperationalError('page observation interrupted')
        return original(*args,**kwargs)
    monkeypatch.setattr(service.spider_activity,'event',fail_after_first_source)
    result=service.execute_tool('drive_spider',{'session_id':session,'action':'navigate','topic_id':ids['a']})
    assert result.get('isError')
    assert not any(e['status']=='completed' and e['object_id'] for e in sessions(client)[0]['events'])
