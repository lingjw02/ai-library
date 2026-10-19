import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { SyncQueue } from '../sync-queue.js';

const frame=(event_id='event-1',text='original')=>({operation:'sync_event',session_key:'chat-a',project_id:'topic-a',event_id,event_type:'assistant_message',text});
const options=directory=>({directory,principal:'deepseek-harness',scope:'default',retryMs:10});
test('failed sends persist through restart and are delivered once in order',async()=>{
  const directory=await mkdtemp(resolve(tmpdir(),'library-outbox-'));
  let queue;
  try {
    queue=new SyncQueue({...options(directory),send:async()=>{throw new Error('offline');}});
    await queue.enqueue(frame());await queue.enqueue(frame('event-2'));
    await queue.close();
    const received=[];
    queue=new SyncQueue({...options(directory),send:async item=>received.push(item.event_id)});
    await queue.flush();
    assert.deepEqual(received,['event-1','event-2']);
    assert.equal(queue.status('chat-a').queued_events,0);
  }finally{await queue?.close();await rm(directory,{recursive:true,force:true});}
});

test('flush sends new frames even when an older pump is still settling',async()=>{
 const directory=await mkdtemp(resolve(tmpdir(),'library-outbox-'));let release,entered;
 const gate=new Promise(resolve=>{release=resolve;}),started=new Promise(resolve=>{entered=resolve;}),sent=[];
 const queue=new SyncQueue({...options(directory),retryMs:10000,send:async row=>{
  if(row.event_id==='old'){entered();await gate;}sent.push(row.event_id);
 }});
 try {
  await queue.enqueue(frame('old'));await started;await queue.enqueue(frame('new'));
  const flushing=queue.flush();release();await flushing;
  assert.deepEqual(sent,['old','new']);assert.equal(queue.status('chat-a').queued_events,0);
 }finally{release();await queue.close();await rm(directory,{recursive:true,force:true});}
});

test('one blocked chat preserves its order while other chats continue syncing',async()=>{
 const directory=await mkdtemp(resolve(tmpdir(),'library-outbox-')),received=[];
 const queue=new SyncQueue({...options(directory),send:async row=>{if(row.session_key==='chat-a')throw new Error('revoked');received.push(row.event_id);}});
 try {
  await queue.enqueueBatch([frame(),frame('event-2'),{...frame('other'),session_key:'chat-b'}]);await queue.flush();
  assert.deepEqual(received,['other']);assert.equal(queue.status('chat-a').queued_events,2);assert.match(queue.status('chat-a').sync_error,/revoked/);
 }finally{await queue.close();await rm(directory,{recursive:true,force:true});}
});

test('reservations bound incoming writes synchronously and capture loss remains visible after restart',async()=>{
 const directory=await mkdtemp(resolve(tmpdir(),'library-outbox-'));let queue;
 try {
  queue=new SyncQueue({...options(directory),send:async()=>{throw new Error('offline');}});await queue.ready;
  const accepted=queue.enqueueBatch(Array.from({length:255},(_,i)=>frame('event-'+i)));
  await assert.rejects(queue.enqueueBatch([frame('overflow-a'),frame('overflow-b')]),/pending limit/);
  await accepted;await queue.close();
  queue=new SyncQueue({...options(directory),send:async()=>{}});await queue.flush();
  assert.equal(queue.status('chat-a').queued_events,0);assert.match(queue.status('chat-a').sync_error,/pending limit/);
 }finally{await queue?.close();await rm(directory,{recursive:true,force:true});}
});
test('repeated identities deduplicate but changed payloads cannot replace pending evidence',async()=>{
  const directory=await mkdtemp(resolve(tmpdir(),'library-outbox-'));
  const queue=new SyncQueue({...options(directory),send:async()=>{throw new Error('offline');}});
  try {
    await queue.enqueue(frame());await queue.enqueue(frame());
    assert.equal(queue.status('chat-a').queued_events,1);
    await assert.rejects(queue.enqueue(frame('event-1','changed')),/changed/);
    assert.equal(queue.status('other-chat').queued_events,0);
  }finally{await queue.close();await rm(directory,{recursive:true,force:true});}
});
test('outboxes isolate principals and reject oversized frames instead of silently losing data',async()=>{
  const directory=await mkdtemp(resolve(tmpdir(),'library-outbox-'));
  const one=new SyncQueue({...options(directory),send:async()=>{throw new Error('offline');}});
  let other;
  try {
    await one.enqueue(frame());await one.close();
    const received=[];
    other=new SyncQueue({...options(directory),principal:'another',send:async value=>received.push(value)});
    await other.flush();assert.equal(received.length,0);
    await assert.rejects(other.enqueue({...frame(),text:'x'.repeat(40000)}),/limit/);
  }finally{await one.close();await other?.close();await rm(directory,{recursive:true,force:true});}
});
