"""Official SDK subprocess calls observed through the owner ASGI interface."""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp import Client, StdioServerParameters

from backend.adapters.api.app import create_app
from backend.collections.manager import CollectionManager
from backend.notes.notes_manager import NotesManager
from backend.storage.canonical.sqlite_storage import CanonicalStorage
from backend.storage.raw.raw_store import RawSourceStore


SCRIPT=Path(__file__).resolve().parents[1]/'scripts/run_mcp.py'


def decoded(result):
    assert not result.is_error,result.content
    return json.loads(result.content[0].text)


def connection(path,principal='codex',read_only=False,application=None):
    args=[str(SCRIPT),'--data-dir',str(path),'--principal',principal]
    if read_only:
        args.append('--read-only')
    if application:
        args.extend(['--application',application])
    return Client(StdioServerParameters(command=sys.executable,args=args,cwd=str(path)),read_timeout_seconds=15)


def setup(path):
    storage=CanonicalStorage(str(path/'knowledge_os.db'))
    notes=NotesManager(storage,RawSourceStore(str(path/'raw_sources')))
    source=notes.create_note('Actual source','needle source text')
    topics=CollectionManager(storage)
    root=topics.create('Root')
    child=topics.create('Neighbor',parent_id=root['id'])
    topics.membership(root['id'],'default','note',source.id,root['revision'])
    owner=TestClient(create_app(str(path/'knowledge_os.db'),str(path/'raw_sources'),str(path/'artifacts')))
    return owner,source,root['id'],child['id']


def snapshot(owner):
    response=owner.get('/api/spider-mecha')
    assert response.status_code==200,response.text
    return response.json()


@pytest.mark.asyncio
async def test_two_applications_and_two_chats_over_actual_stdio(tmp_path):
    owner,source,root,child=setup(tmp_path)
    async with connection(tmp_path) as codex,connection(tmp_path,'antigravity') as anti:
        assert 'begin_spider_session' in {t.name for t in (await codex.list_tools()).tools}
        one=decoded(await codex.call_tool('begin_spider_session',{'session_key':'one','label':'First'}))['session_id']
        two=decoded(await codex.call_tool('begin_spider_session',{'session_key':'two','label':'Second'}))['session_id']
        three=decoded(await anti.call_tool('begin_spider_session',{'session_key':'one','label':'Other application'}))['session_id']
        decoded(await codex.call_tool('drive_spider',{'session_id':one,'action':'navigate','topic_id':root,'query':'needle'}))
        decoded(await codex.call_tool('drive_spider',{'session_id':two,'action':'navigate','topic_id':child}))
        result=decoded(await anti.call_tool('get_user_note',{'note_id':source.id,'spider_session_id':three}))
        assert result['content']=='needle source text'
        assert (await anti.call_tool('set_spider_session_state',{'session_id':one,'state':'idle'})).is_error
        apps={a['id']:a for a in snapshot(owner)['applications']}
        assert set(apps)=={'codex','antigravity'}
        chats={s['id']:s for s in apps['codex']['sessions']}
        assert chats[one]['position']['topic_id']==root
        assert chats[two]['position']['topic_id']==child
        assert apps['antigravity']['sessions'][0]['position']['object_id']==source.id
        decoded(await codex.call_tool('end_spider_session',{'session_id':one}))
        apps={a['id']:a for a in snapshot(owner)['applications']}
        assert [s['id'] for s in apps['codex']['sessions']]==[two]
    assert all(a['sessions']==[] for a in snapshot(owner)['applications'])


@pytest.mark.asyncio
async def test_read_only_driving_and_original_result_shape(tmp_path):
    owner,source,root,_=setup(tmp_path)
    async with connection(tmp_path,read_only=True) as client:
        names={t.name for t in (await client.list_tools()).tools}
        assert {'begin_spider_session','drive_spider','set_spider_session_state','end_spider_session'} <= names
        assert 'create_user_note' not in names
        session=decoded(await client.call_tool('begin_spider_session',{'session_key':'reader','label':'Reader'}))['session_id']
        page=decoded(await client.call_tool('drive_spider',{'session_id':session,'action':'navigate','topic_id':root}))
        assert page['items'][0]['id']==source.id
        result=await client.call_tool('drive_spider',{'session_id':session,'action':'read','kind':'note','object_id':source.id})
        assert decoded(result)['content']==source.content
        assert result.structured_content['id']==source.id
        decoded(await client.call_tool('set_spider_session_state',{'session_id':session,'state':'idle'}))
        assert snapshot(owner)['applications'][0]['sessions'][0]['state']=='idle'
        assert not any('needle source text' in json.dumps(e) for e in snapshot(owner)['applications'][0]['sessions'][0]['events'])


@pytest.mark.asyncio
async def test_undeclared_host_connection_is_not_a_fabricated_chat(tmp_path):
    owner,source,_,_=setup(tmp_path)
    async with connection(tmp_path,'mcp-agent') as client:
        result=decoded(await client.call_tool('get_user_note',{'note_id':source.id}))
        assert result['revision']==1
        app=snapshot(owner)['applications'][0]
        assert app['id'].startswith('unknown-')
        assert app['sessions'][0]['identity_kind']=='connection'
        assert app['sessions'][0]['state']=='unknown'
    assert snapshot(owner)['applications'][0]['sessions']==[]


@pytest.mark.asyncio
async def test_trusted_application_override_does_not_widen_scope(tmp_path):
    owner,source,_,_=setup(tmp_path)
    async with connection(tmp_path,'dedicated-reader',application='gemini') as client:
        decoded(await client.call_tool('get_user_note',{'note_id':source.id}))
        assert snapshot(owner)['applications'][0]['id']=='gemini'
        assert (await client.call_tool('get_user_note',{'note_id':source.id,'scope':'foreign'})).is_error
