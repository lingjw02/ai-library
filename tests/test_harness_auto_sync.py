"""Confirmed task sync preserves scope, raw sources and durable receipts."""
import pytest
from backend.adapters.harness.context_bridge import HarnessContextBridge
from backend.adapters.mcp.stdio_server import build_service
from tests.test_harness_plugin import seed, topics_for, member


def setup(tmp_path, enabled=True):
    bridge = HarnessContextBridge(build_service(tmp_path, 'project-a', True, 'deepseek-harness', 'deepseek'),
                                  allow_confirmed_sync=enabled)
    project, child = topics_for(bridge)
    return bridge, project, child


def call(bridge, project, operation, **fields):
    return bridge.handle({'operation': operation, 'session_key': 'chat-a',
                          'project_id': project['id'] if project else None, **fields})


def stage(bridge, project, suffix='', text='Use SQLite for the local evidence store.'):
    call(bridge, project, 'sync_event', event_id='user'+suffix, event_type='user_message', turn_id='turn'+suffix, text='Choose storage')
    call(bridge, project, 'sync_event', event_id='assistant'+suffix, event_type='assistant_message', turn_id='turn'+suffix, text=text)
    return call(bridge, project, 'sync_event', event_id='complete'+suffix, event_type='turn_completed', turn_id='turn'+suffix)


def confirm(bridge, project, batch, **fields):
    return call(bridge, project, 'sync_confirm', batch_id=batch['id'], digest=batch['digest'],
                item_ids=[item['id'] for item in batch['items']], **fields)


def test_stage_only_then_review_and_confirm_preserves_unverified_raw_sources(tmp_path):
    bridge, project, _ = setup(tmp_path)
    stage(bridge, project)
    assert bridge.service.notes_mgr.list_notes(scope='project-a') == []
    assert call(bridge, project, 'sync_status')['pending_count'] == 1
    batch = call(bridge, project, 'sync_review')['batch']
    assert batch['items'][0]['verification'] == 'UNVERIFIED'
    result = confirm(bridge, project, batch)
    assert result['status'] == 'confirmed'
    assert len(result['note_ids']) == len(result['memory_ids']) == 1
    note = bridge.service.notes_mgr.get_note(result['note_ids'][0])
    assert 'Choose storage' in note.content and 'Use SQLite' in note.content
    memory = bridge.service.storage.get_node(result['memory_ids'][0])
    assert memory.authority.value == 'INFERRED' and memory.verification_props['status'] == 'UNVERIFIED'
    assert memory.provenance.source_id == note.id and memory.provenance.content_hash == note.content_hash
    assert bridge.service.read_only
    assert confirm(bridge, project, batch) == result
    assert call(bridge, project, 'sync_status')['pending_count'] == 0
    bridge.close()


def test_dedupe_changed_replay_cross_chat_project_and_stale_review(tmp_path):
    bridge, project, child = setup(tmp_path)
    stage(bridge, project)
    assert call(bridge, project, 'sync_event', event_id='complete', event_type='turn_completed', turn_id='turn')['duplicate']
    with pytest.raises(ValueError, match='replay'):
        call(bridge, child, 'sync_event', event_id='complete', event_type='turn_completed', turn_id='turn')
    with pytest.raises(ValueError, match='replay'):
        call(bridge, project, 'sync_event', event_id='assistant', event_type='assistant_message', turn_id='turn', text='changed')
    batch = call(bridge, project, 'sync_review')['batch']
    with pytest.raises(ValueError):
        bridge.handle({'operation':'sync_confirm','session_key':'chat-b','project_id':project['id'],
                       'batch_id':batch['id'],'digest':batch['digest'],'item_ids':[batch['items'][0]['id']]})
    stage(bridge, project, '2')
    with pytest.raises(ValueError, match='review'):
        confirm(bridge, project, batch)
    bridge.close()


def test_restart_receipt_and_revoked_grant_never_regrant(tmp_path):
    bridge, project, _ = setup(tmp_path)
    stage(bridge, project)
    batch = call(bridge, project, 'sync_review')['batch']
    result = confirm(bridge, project, batch)
    bridge.close()
    restarted = HarnessContextBridge(build_service(tmp_path,'project-a',True,'deepseek-harness','deepseek'), allow_confirmed_sync=True)
    assert confirm(restarted, project, batch) == result
    grant = restarted.service.access_policy.get_grant('deepseek-harness','project-a')
    restarted.service.access_policy.update_grant('deepseek-harness','project-a',read=True,write=False,
                                               expected_revision=grant['revision'],actor='owner')
    with pytest.raises(PermissionError): confirm(restarted, project, batch)
    restarted.close()


def test_no_project_stays_sidecar_and_tools_do_not_retain_content(tmp_path):
    bridge, project, _ = setup(tmp_path)
    stage(bridge, None)
    assert call(bridge, None, 'sync_review')['batch'] is None
    assert call(bridge, project, 'sync_review')['batch'] is None
    with pytest.raises(ValueError):
        call(bridge, project, 'sync_event', event_id='tool', event_type='tool_completed', text='SECRET TOOL OUTPUT', tool_name='lookup')
    call(bridge, project, 'sync_event', event_id='tool', event_type='tool_completed', tool_name='lookup')
    observed = call(bridge, project, 'observe')['session']
    assert observed['events'][-1]['tool'] == 'lookup' and observed['events'][-1]['action'] == 'observe'
    assert not bridge.service.notes_mgr.list_notes(scope='project-a')
    bridge.close()


def test_browse_subtree_read_partial_fingerprint_and_revocation(tmp_path):
    bridge, project, child = setup(tmp_path)
    seed(bridge.service, identity='selected', content='SQLite '+ 'x'*45000)
    seed(bridge.service, identity='outside', content='PRIVATE CONTENT')
    member(bridge,child,'selected')
    before = call(bridge,project,'fingerprint')['fingerprint']
    page = call(bridge,project,'browse',limit=1)
    assert page['total'] == 1 and page['items'][0]['id'] == 'selected'
    obj = call(bridge,project,'read_object',kind='memory',object_id='selected')['object']
    assert obj['partial'] and len(obj['content']) <= 40000
    with pytest.raises(ValueError): call(bridge,project,'read_object',kind='memory',object_id='outside')
    node = bridge.service.storage.get_node('selected'); node.content='SQLite changed'; bridge.service.storage.upsert_node(node)
    assert call(bridge,project,'fingerprint')['fingerprint'] != before
    grant=bridge.service.access_policy.get_grant('deepseek-harness','project-a')
    bridge.service.access_policy.update_grant('deepseek-harness','project-a',read=False,write=False,expected_revision=grant['revision'],actor='owner')
    with pytest.raises(PermissionError): call(bridge,project,'browse')
    bridge.close()


def test_disabled_confirm_unknown_fields_and_invalid_limits(tmp_path):
    bridge, project, _ = setup(tmp_path, False)
    stage(bridge, project)
    batch=call(bridge,project,'sync_review')['batch']
    with pytest.raises(PermissionError): confirm(bridge,project,batch)
    for fields in ({'limit':101},{'scope':'other'},{'kind':'secret'}):
        with pytest.raises(ValueError): call(bridge,project,'browse',**fields)
    with pytest.raises(ValueError):
        call(bridge,project,'sync_event',event_id='long',event_type='user_message',text='x'*8001)
    bridge.close()


def test_partial_confirm_retry_creates_no_duplicate_and_rejects_changed_source(tmp_path, monkeypatch):
    bridge, project, _ = setup(tmp_path)
    stage(bridge,project)
    batch=call(bridge,project,'sync_review')['batch']
    original=bridge.service.bridge.accept_candidate
    def failed(*a,**kw): raise RuntimeError('temporary promotion failure')
    monkeypatch.setattr(bridge.service.bridge,'accept_candidate',failed)
    with pytest.raises(RuntimeError): confirm(bridge,project,batch)
    notes=bridge.service.notes_mgr.list_notes(scope='project-a')
    assert len(notes)==1
    monkeypatch.setattr(bridge.service.bridge,'accept_candidate',original)
    result=confirm(bridge,project,batch)
    assert len(bridge.service.notes_mgr.list_notes(scope='project-a'))==1
    assert len(result['memory_ids'])==1
    bridge.service.notes_mgr.revise_note(notes[0].id,title='Manual revision',content='changed by owner',expected_revision=1)
    with pytest.raises(ValueError,match='source'): confirm(bridge,project,batch)
    bridge.close()


def test_subset_confirmation_excludes_items_and_partial_source_is_labeled(tmp_path):
    bridge, project, _ = setup(tmp_path)
    stage(bridge,project,'one')
    call(bridge,project,'sync_event',event_id='partial',event_type='assistant_message',turn_id='partial',
         text='This source was clipped.',text_truncated=True)
    call(bridge,project,'sync_event',event_id='partial-complete',event_type='turn_completed',turn_id='partial')
    batch=call(bridge,project,'sync_review')['batch']
    assert len(batch['items'])==2 and batch['items'][1]['source_partial']
    result=call(bridge,project,'sync_confirm',batch_id=batch['id'],digest=batch['digest'],item_ids=[batch['items'][1]['id']])
    assert result['items'][0]['status']=='excluded'
    assert len(result['note_ids'])==len(result['memory_ids'])==1
    node=bridge.service.storage.get_node(result['memory_ids'][0])
    assert node.custom_props['harness']['source_partial']
    with pytest.raises(ValueError,match='selection'): confirm(bridge,project,batch)
    bridge.close()


def test_project_changed_after_review_preserves_draft_and_existing_objects(tmp_path):
    bridge,project,_=setup(tmp_path)
    stage(bridge,project)
    batch=call(bridge,project,'sync_review')['batch']
    bridge.service.collections.rename(project['id'],'Owner updated title','project-a',project['revision'])
    with pytest.raises(ValueError,match='Project changed'): confirm(bridge,project,batch)
    assert not bridge.service.notes_mgr.list_notes(scope='project-a')
    assert call(bridge,project,'sync_status')['pending_count']==1
    fresh=call(bridge,project,'sync_review')['batch']
    assert fresh['digest'] != batch['digest']
    assert confirm(bridge,project,fresh)['status']=='confirmed'
    bridge.close()


@pytest.mark.parametrize('change',['remove','revoke'])
def test_read_rechecks_membership_and_permission_before_return(tmp_path,monkeypatch,change):
    bridge,project,child=setup(tmp_path)
    seed(bridge.service,identity='selected'); member(bridge,child,'selected')
    original=bridge._call
    def changed(name,values):
        result=original(name,values)
        if name=='get_memory_node':
            if change=='remove':
                with bridge.service.storage.session() as conn:
                    conn.execute('DELETE FROM collection_members WHERE topic_id=? AND object_id=?',(child['id'],'selected'))
            else:
                grant=bridge.service.access_policy.get_grant('deepseek-harness','project-a')
                bridge.service.access_policy.update_grant('deepseek-harness','project-a',read=False,write=False,
                    expected_revision=grant['revision'],actor='owner')
        return result
    monkeypatch.setattr(bridge,'_call',changed)
    with pytest.raises((ValueError,PermissionError)):
        call(bridge,project,'read_object',kind='memory',object_id='selected')
    bridge.close()


def test_manual_memory_edit_is_preserved_and_receipt_reports_conflict(tmp_path):
    bridge,project,_=setup(tmp_path)
    stage(bridge,project)
    batch=call(bridge,project,'sync_review')['batch']; result=confirm(bridge,project,batch)
    node=bridge.service.storage.get_node(result['memory_ids'][0]); node.content='Manual owner correction'
    bridge.service.storage.upsert_node(node)
    with pytest.raises(ValueError,match='Memory changed'): confirm(bridge,project,batch)
    assert bridge.service.storage.get_node(node.id).content=='Manual owner correction'
    bridge.close()


def test_missing_or_corrupt_raw_source_blocks_repeat_confirmation(tmp_path):
    bridge,project,_=setup(tmp_path)
    stage(bridge,project)
    batch=call(bridge,project,'sync_review')['batch']; result=confirm(bridge,project,batch)
    note=bridge.service.notes_mgr.get_note(result['note_ids'][0])
    blob=bridge.service.notes_mgr.raw_store.base_dir / (note.content_hash+'.raw')
    blob.write_bytes(b'owner tampered with raw source')
    with pytest.raises(ValueError): confirm(bridge,project,batch)
    bridge.close()


def test_full_review_batch_rejects_completion_without_losing_event_retry(tmp_path,monkeypatch):
    from backend.adapters.harness import sync_store
    bridge,project,_=setup(tmp_path)
    stage(bridge,project)
    batch=call(bridge,project,'sync_review')['batch']
    one_size=len(sync_store.encoded({'batch':batch}).encode('utf-8'))
    monkeypatch.setattr(sync_store,'MAX_REVIEW_BYTES',int(one_size*1.5))
    with pytest.raises(ValueError,match='batch is full'): stage(bridge,project,'queued')
    with bridge.sync_store.db() as conn:
        assert not conn.execute('SELECT 1 FROM sync_events WHERE event_id=?',('completequeued',)).fetchone()
    assert confirm(bridge,project,batch)['status']=='confirmed'
    assert stage(bridge,project,'queued')['pending_count']==1
    bridge.close()


@pytest.mark.parametrize('phase',['note','promotion','membership'])
def test_committed_phase_without_receipt_recovers_after_restart(tmp_path,monkeypatch,phase):
    bridge,project,_=setup(tmp_path)
    stage(bridge,project)
    batch=call(bridge,project,'sync_review')['batch']
    owner,name={'note':(bridge.service.notes_mgr,'create_note'),
                'promotion':(bridge.service.bridge,'accept_candidate'),
                'membership':(bridge.service.collections,'membership')}[phase]
    original=getattr(owner,name)
    def interrupted(*args,**kwargs):
        original(*args,**kwargs)
        raise RuntimeError('Interrupted after canonical commit')
    monkeypatch.setattr(owner,name,interrupted)
    with pytest.raises(RuntimeError): confirm(bridge,project,batch)
    bridge.close()
    restarted=HarnessContextBridge(build_service(tmp_path,'project-a',True,'deepseek-harness','deepseek'),allow_confirmed_sync=True)
    result=confirm(restarted,project,batch)
    assert len(restarted.service.notes_mgr.list_notes(scope='project-a'))==1
    with restarted.service.storage.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM nodes WHERE scope='project-a' AND node_type='Fact'").fetchone()[0]==1
        assert conn.execute('SELECT COUNT(*) FROM collection_members WHERE topic_id=?',(project['id'],)).fetchone()[0]==2
    assert len(result['note_ids'])==len(result['memory_ids'])==1
    restarted.close()


@pytest.mark.parametrize('project_state',['selected','none','removed'])
def test_metadata_turn_finished_returns_idle_without_staging_memory(tmp_path,project_state):
    bridge,_,_=setup(tmp_path)
    project=bridge.service.collections.create('Lifecycle project',scope='project-a') if project_state!='none' else None
    call(bridge,project,'sync_event',event_id='started',event_type='tool_started',tool_name='lookup',turn_id='aborted')
    assert call(bridge,None,'observe')['session']['state']=='active'
    if project_state=='removed':
        with bridge.service.storage.session() as conn:
            conn.execute('DELETE FROM collection_topics WHERE id=?',(project['id'],))
    call(bridge,project,'sync_event',event_id='finished',event_type='turn_finished',turn_id='aborted')
    assert call(bridge,None,'observe')['session']['state']=='idle'
    assert not bridge.service.notes_mgr.list_notes(scope='project-a')
    assert call(bridge,None,'sync_review')['batch'] is None
    with bridge.sync_store.db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM sync_batches').fetchone()[0]==0
    with pytest.raises(ValueError):
        call(bridge,project,'sync_event',event_id='invalid-finished',event_type='turn_finished',text='must not capture this')
    if project_state=='removed':
        with pytest.raises(ValueError):
            call(bridge,project,'sync_event',event_id='invalid-source',event_type='assistant_message',text='Removed project content')
    bridge.close()


def test_explicit_reject_all_clears_pending_without_any_canonical_write(tmp_path):
    bridge,project,_=setup(tmp_path)
    stage(bridge,project,'first'); stage(bridge,project,'second')
    batch=call(bridge,project,'sync_review')['batch']
    def canonical_counts():
        with bridge.service.storage.session() as conn:
            tables=[row['name'] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return {table:conn.execute('SELECT COUNT(*) FROM "'+table.replace('"','""')+'"').fetchone()[0]
                    for table in tables}
    before=canonical_counts()
    result=call(bridge,project,'sync_confirm',batch_id=batch['id'],digest=batch['digest'],item_ids=[])
    assert result['status']=='confirmed' and result['note_ids']==result['memory_ids']==[]
    assert len(result['items'])==2 and all(item['status']=='excluded' for item in result['items'])
    assert canonical_counts()==before
    assert call(bridge,project,'sync_status')['pending_count']==0
    assert call(bridge,project,'sync_review')['batch'] is None
    assert call(bridge,project,'sync_confirm',batch_id=batch['id'],digest=batch['digest'],item_ids=[])==result
    assert canonical_counts()==before
    for fields in ({},{'item_ids':None}):
        with pytest.raises(ValueError):
            call(bridge,project,'sync_confirm',batch_id=batch['id'],digest=batch['digest'],**fields)
    bridge.close()
