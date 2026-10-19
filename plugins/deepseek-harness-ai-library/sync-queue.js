import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readdir, readFile, writeFile, rename, unlink, stat } from 'node:fs/promises';
import { resolve } from 'node:path';

const hash=value=>createHash('sha256').update(value).digest('hex');
const frameLimit=32768, queueLimit=256;

/** Durable, scoped background transport. Only staging/activity and lifecycle frames may replay. */
export class SyncQueue {
  constructor({directory,principal,scope,send,retryMs=1000}) {
    this.owner=hash(JSON.stringify([principal,scope]));
    this.directory=resolve(directory,this.owner);this.send=send;this.retryMs=retryMs;
    this.items=[];this.errors=new Map();this.losses=new Map();this.stopped=false;this.sequence=0;this.reserved=0;
    this.ready=this.load();this.chain=this.ready;this.lossChain=this.ready;
  }
  validate(frame) {
    if(!frame||!['sync_event','end_session'].includes(frame.operation)
      ||typeof frame.session_key!=='string'||!frame.session_key||frame.session_key.length>200)throw new Error('Invalid sync frame');
    const text=JSON.stringify(frame);
    if(Buffer.byteLength(text)>frameLimit)throw new Error('Sync frame exceeds byte limit');
    return text;
  }
  async load() {
    await mkdir(this.directory,{recursive:true});
    const lossesPath=resolve(this.directory,'capture-errors.json');
    try {
      if((await stat(lossesPath)).size>131072)throw new Error('Sync capture error record exceeds limit');
      const saved=JSON.parse(await readFile(lossesPath,'utf8'));
      if(saved.owner!==this.owner||!Array.isArray(saved.errors)||saved.errors.length>128)throw new Error('Invalid sync capture error record');
      for(const pair of saved.errors){
        if(!Array.isArray(pair)||pair.length!==2||typeof pair[0]!=='string'||pair[0].length>200||typeof pair[1]!=='string'||pair[1].length>300)throw new Error('Invalid sync capture error entry');
        this.losses.set(...pair);
      }
    } catch(error){if(error.code!=='ENOENT')throw error;}
    const names=(await readdir(this.directory)).filter(name=>/^[a-f0-9]{64}\.json$/.test(name));
    if(names.length>queueLimit)throw new Error('Persisted sync queue exceeds limit');
    for(const name of names) {
      const path=resolve(this.directory,name);
      if((await stat(path)).size>frameLimit+1024)throw new Error('Persisted sync frame exceeds limit');
      const row=JSON.parse(await readFile(path,'utf8'));
      const content=this.validate(row.frame);
      if(row.owner!==this.owner||row.digest!==hash(content)||typeof row.key!=='string'
        ||hash(row.key)+'.json'!==name||!Number.isFinite(row.order))throw new Error('Persisted sync frame changed');
      this.items.push({...row,path});this.sequence=Math.max(this.sequence,row.order);
    }
    this.items.sort((a,b)=>a.order-b.order);
  }
  enqueue(frame) {
    return this.enqueueBatch([frame]);
  }
  recordLoss(frames,error) {
    for(const frame of Array.isArray(frames)?frames:[]){
      const id=String(frame?.session_key??'').slice(0,200);
      this.losses.set(id,String(error.message).slice(0,300));
      if(this.losses.size>128)this.losses.delete(this.losses.keys().next().value);
    }
    this.persistLoss();
  }
  persistLoss() {
    this.lossDirty=true;
    if(this.lossWriting)return;
    this.lossWriting=true;
    this.lossChain=(async()=>{
      await this.ready;
      while(this.lossDirty) {
      this.lossDirty=false;
      const path=resolve(this.directory,'capture-errors.json'),temporary=path+'.'+randomUUID()+'.tmp';
      await writeFile(temporary,JSON.stringify({owner:this.owner,errors:[...this.losses]}),{encoding:'utf8',flag:'wx'});
      await rename(temporary,path);
      }
    })().catch(error=>{this.lossStorageError=error.message;}).finally(()=>{
      this.lossWriting=false;
      if(this.lossDirty)this.persistLoss();
    });
  }
  enqueueBatch(frames) {
    let snapshots;
    try {
      if(this.stopped)throw new Error('Sync queue is closed');
      if(!Array.isArray(frames)||!frames.length||frames.length>queueLimit)throw new Error('Invalid sync batch');
      snapshots=frames.map(frame=>JSON.parse(this.validate(frame)));
      if(this.items.length+this.reserved+snapshots.length>queueLimit)throw new Error('Sync queue exceeds pending limit');
      this.reserved+=snapshots.length;
    } catch(error) {
      this.recordLoss(frames,error);
      return Promise.reject(error);
    }
    const task=this.chain.then(async()=>{
      if(this.stopped)throw new Error('Sync queue is closed');
      const staged=[];
      try {for(const frame of snapshots) {
      const content=JSON.stringify(frame);
      const key=JSON.stringify([frame.session_key,frame.operation,frame.event_id??randomUUID()]);
      const digest=hash(content),existing=[...this.items,...staged].find(row=>row.key===key);
      if(existing){if(existing.digest!==digest)throw new Error('Repeated sync event changed');continue;}
      if(this.items.length>=queueLimit)throw new Error('Sync queue exceeds pending limit');
      const path=resolve(this.directory,hash(key)+'.json');
      const row={owner:this.owner,key,digest,frame,order:Math.max(Date.now(),this.sequence+1)};
      this.sequence=row.order;
      const temporary=path+'.'+randomUUID()+'.tmp';
      await writeFile(temporary,JSON.stringify(row),{encoding:'utf8',flag:'wx'});
      try {await rename(temporary,path);}catch(error){await unlink(temporary).catch(()=>{});throw error;}
      staged.push({...row,path});
      }
      this.items.push(...staged);
      } catch(error) {await Promise.allSettled(staged.map(row=>unlink(row.path)));throw error;}
    }).finally(()=>{this.reserved-=snapshots.length;});
    this.chain=task.catch(error=>{this.recordLoss(snapshots,error);});
    void task.then(()=>this.pump(),()=>{});
    return task;
  }
  async pump() {
    if(this.stopped)return;
    if(this.active)return this.active;
    clearTimeout(this.timer);
    this.active=(async()=>{
      const blocked=new Set();
      for(const item of [...this.items]) {
        if(this.stopped)break;
        if(blocked.has(item.frame.session_key))continue;
        this.controller=new AbortController();
        try {
          await this.send(item.frame,this.controller.signal);
          await unlink(item.path);this.items.splice(this.items.indexOf(item),1);this.errors.delete(item.frame.session_key);
        } catch(error) {
          this.errors.set(item.frame.session_key,error.message);blocked.add(item.frame.session_key);
        } finally {this.controller=null;}
      }
    })();
    try {await this.active;}
    finally {
      this.active=null;
      if(!this.stopped&&this.items.length){this.timer=setTimeout(()=>void this.pump(),this.retryMs);this.timer.unref?.();}
    }
  }
  async flush() {await this.chain;await this.ready;await this.active;await this.pump();}
  status(sessionId) {
    return {queued_events:this.items.filter(row=>row.frame.session_key===sessionId).length,
      sync_error:this.losses.get(sessionId)??this.errors.get(sessionId)??this.lossStorageError??null};
  }
  close() {
    if(this.closePromise)return this.closePromise;
    this.closePromise=(async()=>{
      await this.chain;this.stopped=true;clearTimeout(this.timer);
      this.controller?.abort(new Error('Sync paused on plugin shutdown; event retained'));
      await this.active;await this.lossChain;
    })();
    return this.closePromise;
  }
}
