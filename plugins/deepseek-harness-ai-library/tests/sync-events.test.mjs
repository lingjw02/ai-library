import test from 'node:test';
import assert from 'node:assert/strict';
import { NativeSyncEvents } from '../sync-events.js';

const event=(type,seq,data)=>({type,seq,data});
const start=event('turn/start',1,{turn:1});
const user=event('user/message',2,{source:{kind:'user'},content:[{type:'text',text:'Finish SQLite task'}]});
const reply=event('assistant/message',3,{turn:1,message:{source:{kind:'assistant'},content:[{type:'text',text:'Completed work; tests passed.'}]}});
const end=event('turn/end',4,{turn:1,reason:{kind:'completed'}});
test('completed native turn stages exact user/final reply and preserves captured project',()=>{
  const capture=new NativeSyncEvents();
  assert.deepEqual(capture.capture('chat',start,'project'),[]);
  assert.deepEqual(capture.capture('chat',user,'project'),[]);
  assert.deepEqual(capture.capture('chat',reply,'project'),[]);
  const frames=capture.capture('chat',end,'project');
  assert.deepEqual(frames.map(row=>row.event_type),['user_message','assistant_message','turn_completed']);
  assert.equal(frames[1].text,'Completed work; tests passed.');
  assert.ok(frames.every(row=>row.project_id==='project'&&row.turn_id==='turn:1'));
});
test('tool actions contain no arguments/results and resolve native toolCallId',()=>{
  const capture=new NativeSyncEvents();capture.capture('chat',start,'project');
  const called=capture.capture('chat',event('tool/call',2,{turn:1,callId:'call',name:'read_file',args:{secret:'NO-TRANSMIT'}}),'project');
  const result=capture.capture('chat',event('tool/result',3,{turn:1,message:{toolCallId:'call',isError:true,content:[{type:'text',text:'NO-TRANSMIT'}]}}),'project');
  assert.equal(called[0].event_type,'tool_started');assert.equal(result[0].event_type,'tool_failed');
  assert.equal(result[0].tool_name,'read_file');assert.doesNotMatch(JSON.stringify([...called,...result]),/NO-TRANSMIT/);
});
test('snapshots/commands/interrupted or changed-project turns never become memory drafts',()=>{
  for(const mode of ['snapshot','command','interrupted','changed','midturn','no-project','replace']) {
    const capture=new NativeSyncEvents(),project=mode==='no-project'?null:'project';
    if(mode!=='midturn')capture.capture('chat',start,project);
    const message=mode==='snapshot'?{...user,data:{...user.data,source:{kind:'ai-library'}}}
      :mode==='command'?{...user,data:{...user.data,content:[{type:'text',text:'/library {}'}]}}
      :mode==='replace'?{...user,surfaceOp:{kind:'replace',from:0,to:0}}:user;
    capture.capture('chat',message,project);
    capture.capture('chat',mode==='interrupted'?{...reply,data:{...reply.data,interrupted:true}}:reply,mode==='changed'?'other':project);
    const frames=capture.capture('chat',end,project);
    assert.equal(frames.filter(row=>row.event_type==='assistant_message').length,0,mode);
    assert.equal(frames.at(-1).event_type,'turn_finished',mode);
  }
});

test('a turn captured without a project never attributes later tool actions to a newly selected one',()=>{
 const capture=new NativeSyncEvents();capture.capture('chat',start,null);
 const frames=capture.capture('chat',event('tool/call',2,{callId:'c',name:'read_file'}),'new-project');
 assert.equal(frames[0].project_id,null);
});

test('unrelated records do not consume chat capacity and overflow is reported',()=>{
 const capture=new NativeSyncEvents();
 for(let i=0;i<600;i++)capture.capture('passive-'+i,event('command/run',1,{}),null);
 assert.equal(capture.chats.size,0);
 for(let i=0;i<512;i++)capture.capture('active-'+i,start,null);
 assert.throws(()=>capture.capture('overflow',start,null),/chat limit/);
 capture.forget('active-0');assert.deepEqual(capture.capture('overflow',start,null),[]);
});
