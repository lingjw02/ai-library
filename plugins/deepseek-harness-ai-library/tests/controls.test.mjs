import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, readFile, symlink, link, mkdir, access } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

test('controls replay user preferences and consume a queued preview exactly once', async () => {
  const {createControlProjection, controlProtocol, controlKey} = await import('../control-state.js');
  const unit=createControlProjection('default'); let state=unit.init();
  const command=(operation,response)=>{
    const id=operation;
    state=unit.apply(state,{type:'command/run',data:{name:'library',commandId:id,source:{kind:'user'},args:JSON.stringify({operation})}});
    state=unit.apply(state,{type:'command/done',data:{commandId:id,kind:'success',text:JSON.stringify({protocol:controlProtocol,operation,scope:'default',...response})}});
  };
  assert.equal(controlKey,'aiLibraryControls');
  command('configure',{settings:{autoRead:false,maxContextChars:3200,maxItems:1,excerptChars:80}});
  command('queue_preview',{next:{ticket:'ticket',query:'SQLite',project_id:null}});
  assert.equal(state.settings.autoRead,false); assert.equal(state.next.query,'SQLite');
  const saved=unit.stateSchema.parse(JSON.parse(JSON.stringify(state)));
  command('consume_preview',{ticket:'wrong'});assert.ok(state.next);
  command('consume_preview',{ticket:'ticket'});assert.equal(state.next,null);
  assert.ok(saved.next); assert.equal(unit.apply(state,{type:'user/message',data:{text:'configure'}}),state);
  assert.throws(()=>unit.stateSchema.parse({...state,settings:{...state.settings,maxItems:100}}));
});

test('source edits reject hardlinks and directory junctions and validate JSON without execution', async () => {
  const {DevelopmentWorkspace}=await import('../development-workspace.js');
  const dir=await mkdtemp(join(tmpdir(),'library-developer-links-'));
  const source=join(dir,'source'),outside=join(dir,'outside');await mkdir(source);await mkdir(outside);
  await writeFile(join(outside,'en.json'),'{}');
  await symlink(outside,join(source,'locale'),'junction');
  const dev=new DevelopmentWorkspace(source,join(dir,'work'));
  await assert.rejects(dev.read('locale/en.json'),/regular file/);
  await writeFile(join(source,'client.js'),'const test=1;');await link(join(source,'client.js'),join(outside,'shared.js'));
  await assert.rejects(dev.read('client.js'),/regular file/);
  const clean=join(dir,'clean');await mkdir(join(clean,'locale'),{recursive:true});await writeFile(join(clean,'locale/en.json'),'{}');
  const jsonDev=new DevelopmentWorkspace(clean,join(dir,'json-work'));
  const broken=await jsonDev.stage('chat','locale/en.json','{"incomplete":');assert.equal((await jsonDev.check('chat',broken.id)).ok,false);
});

test('developer edits require checked unchanged proposals, retain backups and reject paths', async () => {
  const {DevelopmentWorkspace}=await import('../development-workspace.js');
  const dir=await mkdtemp(join(tmpdir(),'library-developer-'));
  const source=join(dir,'source');const work=join(dir,'work');
  const {mkdir}=await import('node:fs/promises');await mkdir(source);
  await writeFile(join(source,'client.js'),'const original = 1;\n');
  const dev=new DevelopmentWorkspace(source,work);
  const notExecuted=join(dir,'must-not-exist');
  const esm=await dev.stage('chat','client.js',`import {writeFileSync} from 'node:fs'; writeFileSync(${JSON.stringify(notExecuted)},'executed');`);
  assert.equal((await dev.check('chat',esm.id)).ok,true);await assert.rejects(access(notExecuted));
  await assert.rejects(dev.read('../secret'),/file/i);
  const proposal=await dev.stage('chat','client.js','const updated = 2;\n');
  await assert.rejects(dev.apply('chat',proposal.id),/check/i);
  const checked=await dev.check('chat',proposal.id);assert.equal(checked.ok,true);
  await assert.rejects(dev.apply('other',proposal.id),/proposal/i);
  const saved=await dev.apply('chat',proposal.id);assert.equal(saved.loaded,false);
  assert.equal(await readFile(join(source,'client.js'),'utf8'),'const updated = 2;\n');
  await dev.restore('chat',proposal.id);
  assert.equal(await readFile(join(source,'client.js'),'utf8'),'const original = 1;\n');
  const invalid=await dev.stage('chat','client.js','const = ;');assert.equal((await dev.check('chat',invalid.id)).ok,false);
  await assert.rejects(dev.apply('chat',invalid.id),/check/i);
  const race=await dev.stage('chat','client.js','const different = 3;');await dev.check('chat',race.id);
  await writeFile(join(source,'client.js'),'const external = 4;');
  await assert.rejects(dev.apply('chat',race.id),/changed/i);
  const modified=await dev.stage('chat','client.js','const next = 5;');await dev.check('chat',modified.id);
  await writeFile(modified.path,'const tampered = 6;');await assert.rejects(dev.apply('chat',modified.id),/changed/i);
});
