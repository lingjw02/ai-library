import json
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters
from scripts.chat_import.import_batch import import_batch, digest, prepare_batch


def test_transcript_artifact_preserves_mixed_line_endings(tmp_path):
    folder=tmp_path/'session-one'
    folder.mkdir()
    meta={'id':'session-one','category':'Project A'}
    (tmp_path/'manifest.json').write_text(json.dumps([meta]),encoding='utf8')
    (folder/'summary.json').write_text(json.dumps({'session_id':'session-one','category':'Project A',
        'source_message_numbers':[1],'memory_claims':[]}),encoding='utf8')
    (folder/'messages.json').write_text(json.dumps([{'text':'first\r\nsecond\nthird\r\n',
        'role':'user','timestamp':'t','source_file':'original','source_line':1}]),encoding='utf8')
    batch=prepare_batch(tmp_path)
    text=''.join(batch[0]['chunks'])
    assert 'first\r\nsecond\nthird\r\n' in text
    assert (folder/'import-transcript.md').read_bytes()==text.encode('utf8')


@pytest.mark.asyncio
async def test_real_mcp_import_is_recoverable_without_checkpoint_and_keeps_unverified_provenance(tmp_path):
    meta={'id':'session-one','cwd':'project','subagent':False,'parent_id':None,'sources':[]}
    summary={'category':'Project A','title':'History','classification':'source_project_path',
        'classification_reason':'existing project','summary_markdown':'User chose a local model.',
        'source_message_numbers':[1],'coverage':'fixture','memory_claims':[
            {'claim':'Use a local model.','node_type':'Decision','evidence':[1]}]}
    batch=[{'meta':meta,'summary':summary,'chunks':['User: use a local model.'],
        'transcript_hash':digest('User: use a local model.'),'imported_messages':1,
        'redactions':{},'excluded_runtime_messages':[]}]
    entry=Path(__file__).resolve().parents[1]/'scripts/run_mcp.py'
    params=StdioServerParameters(command=sys.executable,args=[str(entry),'--data-dir',str(tmp_path/'data'),
        '--scope','default','--principal','test-history'])
    async with Client(params,read_timeout_seconds=30) as client:
        async def call(name,args):
            response=await client.call_tool(name,args)
            assert not response.is_error,response.content
            return json.loads(response.content[0].text)
        first=await import_batch(call,batch,tmp_path/'checkpoint1.json')
        second=await import_batch(call,batch,tmp_path/'checkpoint2.json')
        assert first['notes']==second['notes']
        assert first['candidates']==second['candidates']
        assert len(await call('list_user_notes',{}))==4
        assert len(first['candidates'])==2
        assert all(item['status']=='PENDING_OWNER_APPROVAL' for item in first['candidates'].values())
        candidates=await call('list_candidates',{})
        assert len(candidates)==2
        assert all(candidate['status']!='ACCEPTED' for candidate in candidates)
