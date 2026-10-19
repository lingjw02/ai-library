/** Workspace-only, reviewed source changes. Never edits an installed profile. */
import { createHash, randomUUID } from 'node:crypto';
import { readFile, writeFile, mkdir, realpath, lstat, rename } from 'node:fs/promises';
import { resolve, join, relative } from 'node:path';
import { spawn } from 'node:child_process';
export const developmentFiles=Object.freeze(['index.js','bridge-client.js','project-state.js','control-state.js',
  'development-workspace.js','client.js','locale/en.json','locale/zh.json']);
const digest=text=>createHash('sha256').update(text).digest('hex');
const limit=128000;
function delta(before,after,file) {
  const a=before.split('\n'),b=after.split('\n');let start=0,end=0;
  while(start<a.length&&start<b.length&&a[start]===b[start])start++;
  while(end<a.length-start&&end<b.length-start&&a[a.length-1-end]===b[b.length-1-end])end++;
  return `--- ${file} (before)\n+++ ${file} (proposal)\n@@ line ${start+1} @@\n`
    +a.slice(start,a.length-end).map(line=>'-'+line).join('\n')+'\n'
    +b.slice(start,b.length-end).map(line=>'+'+line).join('\n');
}
export class DevelopmentWorkspace {
  constructor(sourceDir,workDir) {this.sourceDir=resolve(sourceDir);this.workDir=resolve(workDir);this.records=new Map();this.locks=new Map();}
  async filePath(file) {
    if(!developmentFiles.includes(file))throw new Error('File is outside the plugin edit list');
    const base=await realpath(this.sourceDir),path=resolve(base,file),actual=await realpath(path);
    const stat=await lstat(path);
    if(stat.isSymbolicLink()||!stat.isFile()||stat.nlink!==1||relative(base,actual).replaceAll('\\','/')!==file)
      throw new Error('File must be a regular file in the plugin source directory');
    if(stat.size>limit*4)throw new Error('Source file exceeds edit limit');
    return path;
  }
  async read(file) {
    const content=await readFile(await this.filePath(file),'utf8');
    if(content.length>limit)throw new Error('Source file exceeds edit limit');
    return {file,content,hash:digest(content),sourceDir:this.sourceDir};
  }
  async stage(owner,file,content) {
    if(this.records.size>=64)throw new Error('Too many development proposals; reload this plugin after preserving backups');
    if(typeof content!=='string'||content.length>limit)throw new Error('Proposal exceeds edit limit');
    const source=await this.read(file),id=randomUUID(),dir=join(this.workDir,id);
    await mkdir(dir,{recursive:true});
    const record={id,owner,file,before:source.content,baseHash:source.hash,path:join(dir,file.endsWith('.json')?'proposal.json':'proposal.mjs'),
      backup:join(dir,'before.txt'),checkedHash:null,appliedHash:null};
    await writeFile(record.path,content,{flag:'wx'});await writeFile(record.backup,record.before,{flag:'wx'});
    this.records.set(id,record);
    return this.review(owner,id);
  }
  record(owner,id) {
    const record=this.records.get(id);
    if(!record||record.owner!==owner)throw new Error('Proposal is unavailable for this chat');
    return record;
  }
  async proposal(owner,id) {
    const r=this.record(owner,id),actual=await realpath(r.path);
    const stat=await lstat(r.path);
    if(actual!==r.path||stat.isSymbolicLink()||!stat.isFile()||stat.nlink!==1||stat.size>limit*4)throw new Error('Proposal file changed or exceeds limit');
    const content=await readFile(r.path,'utf8');
    if(content.length>limit)throw new Error('Proposal exceeds edit limit');
    return {r,content};
  }
  async review(owner,id) {
    const {r,content}=await this.proposal(owner,id),diff=delta(r.before,content,r.file);
    return {id,file:r.file,path:r.path,content,baseHash:r.baseHash,hash:digest(content),diff:diff.slice(0,50000),diff_truncated:diff.length>50000,
      checked:r.checkedHash===digest(content),backup:r.backup,sourceDir:this.sourceDir};
  }
  async check(owner,id) {
    const {r,content}=await this.proposal(owner,id);r.checkedHash=null;
    let result;
    if(r.file.endsWith('.json')) {
      try { const parsed=JSON.parse(content);if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))throw new Error('Locale must be an object');result={ok:true,diagnostic:''}; }
      catch(error){result={ok:false,diagnostic:error.message};}
    } else result=await new Promise((fulfill,reject)=>{
      const child=spawn(process.execPath,['--input-type=module','--check'],{shell:false,windowsHide:true,stdio:['pipe','pipe','pipe']});
      child.stdin.on('error',()=>{});
      child.stdin.end(content);
      let output='';const collect=data=>{output=(output+data.toString()).slice(-12000);};
      child.stdout.on('data',collect);child.stderr.on('data',collect);
      const timer=setTimeout(()=>{child.kill();},10000);
      child.once('error',error=>{clearTimeout(timer);reject(error);});
      child.once('close',code=>{clearTimeout(timer);fulfill({ok:code===0,diagnostic:output});});
    });
    if(result.ok) {
      const after=await this.proposal(owner,id);
      if(digest(after.content)!==digest(content))throw new Error('Proposal changed during check');
      r.checkedHash=digest(content);
    }
    return {...result,review:await this.review(owner,id),checkKind:'syntax_only_no_code_execution'};
  }
  async locked(file,fn) {
    const previous=this.locks.get(file)??Promise.resolve();
    const task=previous.catch(()=>{}).then(fn);this.locks.set(file,task);
    try{return await task;}finally{if(this.locks.get(file)===task)this.locks.delete(file);}
  }
  async replace(file,content) {
    const path=await this.filePath(file),temporary=join(this.workDir,randomUUID()+'.replacement');
    await mkdir(this.workDir,{recursive:true});await writeFile(temporary,content,{flag:'wx'});
    await rename(temporary,path);
  }
  async apply(owner,id) {
    const record=this.record(owner,id);
    return this.locked(record.file,async()=>{
      const {r,content}=await this.proposal(owner,id);
      if(!r.checkedHash)throw new Error('Check proposal before applying');
      if(r.checkedHash!==digest(content))throw new Error('Proposal changed after check');
      if((await this.read(r.file)).hash!==r.baseHash)throw new Error('Source changed since proposal; review a new proposal');
      await this.replace(r.file,content);r.appliedHash=digest(content);
      return {saved:true,loaded:false,file:r.file,backup:r.backup,sourceDir:this.sourceDir,hash:r.appliedHash};
    });
  }
  async restore(owner,id) {
    const r=this.record(owner,id);
    return this.locked(r.file,async()=>{
      if(!r.appliedHash||(await this.read(r.file)).hash!==r.appliedHash)throw new Error('Source changed since apply; automatic restore refused');
      await this.replace(r.file,r.before);r.appliedHash=null;
      return {restored:true,loaded:false,file:r.file};
    });
  }
}
