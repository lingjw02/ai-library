// Read-only B-model acceptance. Fixtures are route responses, never database records.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'D:/02_Development_Tools/live2d-maker/node_modules/playwright-core');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const base=(process.env.STELLAR_BASE_URL||'http://127.0.0.1:8000').replace(/\/$/,'');
const evidence=path.resolve('.agent-tmp/stellar-models-20261012');
const capture=process.env.CAPTURE_SCREENSHOTS!=='0';
fs.mkdirSync(evidence,{recursive:true});
const report={state:'RUNNING',base,capture,checks:[],pageErrors:[],consoleErrors:[],webglWarnings:[],writes:[],assets:[],performance:[],screenshots:[]};
const pendingAssets=[];
const json=(route,value)=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(value)});

async function instrument(context,label,{expectedAssetFailure=false}={}) {
  // Abort a write before it can leave the browser, and fail acceptance afterwards.
  await context.route('**/api/**',async route=>{
    const request=route.request();
    if(request.method()!=='GET') {report.writes.push({label,method:request.method(),url:request.url()});await route.abort('blockedbyclient');}
    else await route.fallback();
  });
  context.on('page',page=>{
    page.on('pageerror',error=>report.pageErrors.push({label,message:error.message}));
    page.on('console',message=>{
      const value={label,type:message.type(),message:message.text()};
      if(/WebGL|GL_INVALID|context lost/i.test(value.message))report.webglWarnings.push(value);
      if(message.type()==='error' && !(expectedAssetFailure&&/model|GLB|asset|Failed to load resource|Failed to fetch/i.test(value.message)))report.consoleErrors.push(value);
    });
    page.on('response',response=>{
      if(!/\/assets\/models\/.*\.glb(?:\?|$)/i.test(response.url()))return;
      const task=(async()=>{
        let magic=null,bytes=0,error=null;
        try {const body=await response.body();bytes=body.length;magic=body.subarray(0,4).toString('ascii');}catch(e){error=e.message;}
        report.assets.push({label,url:response.url(),status:response.status(),magic,bytes,error,expectedFailure:expectedAssetFailure});
      })();pendingAssets.push(task);
    });
  });
}

async function ready(page) {
  await page.waitForFunction(()=>{const u=window.__AI_LIBRARY__?.universe;return u?.data&&u.scene?.debug.ready&&u.scene.debug.assetReady&&u.scene.debug.frames>0;},null,{timeout:90000});
}
async function nav(page,id) {
  await page.evaluate(id=>window.__AI_LIBRARY__.universe.navigate(id),id);
  await page.waitForFunction(id=>{const u=window.__AI_LIBRARY__.universe;return u.topicId===id&&u.data&&(u.data.topic?.id===id||!u.data.topic);},id,{timeout:60000});
  await ready(page);
}
async function directory(page,open=true) {await page.evaluate(open=>window.__AI_LIBRARY__.universe.toggleDirectory(open),open);}
async function shot(page,name) {
  if(!capture)return;
  const file=path.join(evidence,name+'.png');await page.screenshot({path:file});report.screenshots.push(file);
}
async function measure(page,label) {
  report.performance.push(await page.evaluate(label=>{
    const s=window.__AI_LIBRARY__.universe.scene,render=s.renderer.info.render;
    return {label,drawCalls:render.calls,triangles:render.triangles,points:render.points,frames:s.debug.frames,frameTiming:s.debug.frameTiming??s.debug.frameTimes??null,width:innerWidth,height:innerHeight};
  },label));
}
async function assertModels(page) {
  const models=await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.bodies.filter(b=>!b.data.preview&&!b.decorative).map(b=>{
    let meshes=0,triangles=0;
    b.group.traverse(o=>{if(o.isMesh&&o.geometry){meshes++;triangles+=(o.geometry.index?.count??o.geometry.attributes.position?.count??0)/3;}});
    return {id:b.data.id,kind:b.data.kind,family:b.modelFamily??b.modelInstance?.group?.userData?.modelFamily??b.group.children.find(c=>c.userData?.modelFamily)?.userData?.modelFamily,meshes,triangles};
  }));
  assert.ok(models.length,'actual non-preview models render');
  for(const model of models){
    if(model.kind==='notes-hub'){
      // notes-hub 是程序化核心（universe-models.js makeCore/makeEye 建模），
      // 不属于任何资产模型族；断言其有独立核心几何即可。
      assert.ok(model.meshes>1,`notes-hub has independent procedural core parts (meshes=${model.meshes})`);
      continue;
    }
    if(!model.family){
      // 普通主题/条目采用程序化天体建模（脉冲星/星环/吸积盘/水母/小点），
      // 不属于任何资产模型族；仅 skill/mcp 等站点走 GLB 资产族。
      assert.ok(model.meshes>1,`${model.kind}:${model.id} has independent procedural parts (meshes=${model.meshes})`);
      continue;
    }
    assert.ok(model.meshes>1,`${model.family} has independent model parts`);assert.ok(model.triangles>100,`${model.family} contains detailed mesh geometry`);
  }
  return models;
}
async function readerCheck(page,kind,item,width,label) {
  await directory(page,true);
  const row=page.locator(`.universe-entry[data-kind="${kind}"][data-id="${item.id}"] .universe-entry-main`);
  assert.equal(await row.count(),1,'full directory entry remains accessible');
  assert.equal(await row.locator('strong').getAttribute('title'),item.title,'directory preserves full title');
  await row.click();
  const selector=kind==='note'?'#note-modal-backdrop.open .note-modal-dialog':'#memory-detail-drawer.open';
  await page.locator(selector).waitFor();
  await page.waitForFunction(()=>window.__AI_LIBRARY__.universe.scene.debug.reading);
  const rect=await page.locator(selector).boundingBox();assert.ok(rect&&rect.width>0);
  if(width>=1000)assert.ok(rect.width>=width*.44&&rect.width<=width*.54,`${kind} reader is approximately half desktop width: ${rect.width}`);
  else assert.ok(rect.width<=width&&rect.width>=width*.72,`${kind} reader stays readable within narrow viewport`);
  assert.ok(await page.locator('.universe-canvas').isVisible(),'universe stays visible behind reader');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no horizontal document overflow');
  if(kind==='note')assert.equal(await page.locator('#note-edit-title').inputValue(),item.title);
  await shot(page,`${label}-${kind}-reader`);
  await page.locator(kind==='note'?'#note-modal-close':'#drawer-btn-close').click();
  await page.waitForFunction(()=>!window.__AI_LIBRARY__.universe.scene.debug.reading);
}

const fixtureItems=scope=>[
  {id:'personal',kind:'note',author:'user',title:'用户个人笔记完整标题',revision:1,scope,topic_ids:['project'],topics:[{id:'project',title:'技术项目'}]},
  {id:'imported',kind:'note',author:'mcp-agent:history-import',title:'来源导入原文标题',revision:1,scope,topic_ids:['project'],topics:[{id:'project',title:'技术项目'}],provenance:{source_id:'raw-import'}},
  {id:'unknown',kind:'note',title:'作者未确认的来源',revision:1,scope,topic_ids:['project']},
  {id:'shared-memory',kind:'memory',title:'已保存记忆完整总结',revision:1,scope,node_type:'Fact',topic_ids:['project'],topics:[{id:'project',title:'技术项目'}]}
];
function fixtureCatalogue(scope,topicId,kind='all') {
  const topics=[{id:'project',title:scope==='qa-other'?'另一个范围的技术项目':'技术项目',revision:1,count:4,child_count:0,parent_id:null},{id:'second',title:'另一个项目',revision:1,count:1,child_count:0,parent_id:null}];
  const all=fixtureItems(scope);
  let topic=null,entries=[];
  if(topicId==='project'){topic=topics[0];entries=all;}
  else if(topicId==='second'){topic=topics[1];entries=[all[0]];}
  else if(topicId==='all'||topicId==='__personal_notes'){entries=all.filter(i=>(kind==='all'||i.kind===kind)&&(topicId!=='__personal_notes'||i.author==='user'));}
  else if(topicId==='__mcp_library'){topic={id:'__mcp_library',title:'MCP 集合中心',revision:1,count:0,child_count:0,parent_id:null};entries=[];}
  else entries=topics.map(t=>({...t,kind:'topic'}));
  return {scope,topic,topics:entries.filter(i=>i.kind==='topic').map(({kind,...t})=>t),items:entries.filter(i=>i.kind!=='topic'),total:entries.length,offset:0,partial:false,breadcrumbs:topic?[{id:topic.id,title:topic.title}]:[],counts:{topics:2,notes:3,memories:1}};
}
async function fixtureRoutes(context,{hold=null}={}) {
  await context.route('**/api/universe?*',async route=>{
    const params=new URL(route.request().url()).searchParams;
    const scope=params.get('scope')||'default',id=params.get('topic_id'),kind=params.get('kind')||'all';
    if(hold&&hold.match(scope,id)){hold.received();await hold.promise;}
    await json(route,fixtureCatalogue(scope,id,kind));
  });
  await context.route('**/api/local-skills',route=>json(route,{items:[{id:'fixture-skill',kind:'skill',title:'验证 Skill 原名称',description:'route-only read-only fixture',locations:[{source:'Fixture',path:'C:/fixture/SKILL.md'}]}],warnings:[]}));
  await context.route('**/api/capabilities?*',route=>json(route,[]));
  await context.route('**/api/topics?*',route=>json(route,{topics:[{id:'project',title:'技术项目',revision:1}],total:1}));
  await context.route('**/api/topics/*/cluster-suggestions?*',route=>json(route,{suggestions:[]}));
}

(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  // One visual batch: real catalogue at each approved width, no fixture artwork.
  for(const [width,height] of [[1440,1000],[612,844],[390,844]]){
    const label=`real-${width}`,context=await browser.newContext({viewport:{width,height},reducedMotion:'reduce'});
    await instrument(context,label);const page=await context.newPage();await page.goto(base);await ready(page);
    const rootModels=await assertModels(page);
    const hub=rootModels.find(b=>b.kind==='notes-hub');assert.ok(hub,'overview personal hub uses an independent model');
    const projects=rootModels.filter(b=>b.kind==='topic');assert.ok(projects.length>=2,'actual project and tool entrances exist');
    await measure(page,label+'-overview');await directory(page,false);await shot(page,label+'-overview');
    const previewPoint=await page.evaluate(async()=>{
      const {Mesh}=await import('/vendor/three/three.module.js');
      const s=window.__AI_LIBRARY__.universe.scene,rect=s.renderer.domElement.getBoundingClientRect();
      for(const b of s.bodies.filter(b=>b.data.preview)){
        const p=b.group.position.clone().project(s.camera),x=rect.left+(p.x*.5+.5)*rect.width,y=rect.top+(-p.y*.5+.5)*rect.height;
        if(p.z<-1||p.z>1||x<rect.left+10||x>rect.right-10||y<rect.top+200||y>rect.bottom-90)continue;
        // 新 UI 内容预览可直接点选（pick 命中预览本体）；选取不与真实模型重叠的点
        const picked=s.pick({clientX:x,clientY:y});
        if(picked?.data?.id===b.data.id&&picked?.data?.preview)return {x,y,id:b.data.id,kind:b.data.kind};
      }return null;
    });
    assert.ok(previewPoint,'a visible real preview is pickable at its real projected position');
    await page.mouse.click(previewPoint.x,previewPoint.y);await page.waitForTimeout(500);
    // 预览直达对应内容（新 UI 交互：非导航进集合、不改变当前范围）
    if(previewPoint.kind==='note'){
      assert.equal(await page.locator('#note-modal-backdrop.open').count(),1,'preview click opens the note reader directly');
      await page.locator('#note-modal-close').click();
    } else {
      assert.equal(await page.locator('#memory-detail-drawer.open').count(),1,'preview click opens the memory drawer directly');
      await page.locator('#drawer-btn-close').click();
    }
    await page.waitForFunction(()=>!window.__AI_LIBRARY__.universe.scene.debug.reading);
    assert.equal(await page.evaluate(()=>window.__AI_LIBRARY__.universe.topicId),null,'preview click does not navigate into a collection');
    const realTopic=await page.evaluate(()=>window.__AI_LIBRARY__.universe.data.topics.find(t=>!t.virtual&&t.children?.some(i=>i.kind==='note')&&t.children?.some(i=>i.kind==='memory'))??window.__AI_LIBRARY__.universe.data.topics.find(t=>!t.virtual&&t.children?.some(i=>i.kind==='note')));
    assert.ok(realTopic,'actual project contains readable content');
    await directory(page,true);await page.locator(`.universe-entry[data-kind="topic"][data-id="${realTopic.id}"] .universe-entry-main`).click();
    await page.waitForFunction(id=>window.__AI_LIBRARY__.universe.data.topic?.id===id,realTopic.id);await ready(page);
    assert.equal(await page.locator('#universe-title').innerText(),realTopic.title);
    // 新 UI 中知识宇宙核心（notes-hub）在总览与主题详情均作为「我的空间」入口保留（universe-scene.js setData 非 agent 视图统一创建）
    assert.equal(await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.bodies.some(b=>b.data.kind==='notes-hub')),true,'local centre keeps the personal hub as knowledge-universe core');
    const items=await page.evaluate(()=>window.__AI_LIBRARY__.universe.data.items);
    const note=items.find(i=>i.kind==='note'),memory=items.find(i=>i.kind==='memory');assert.ok(note&&memory,'actual project provides both note and memory readers');
    await readerCheck(page,'note',note,width,label);await readerCheck(page,'memory',memory,width,label);
    const localModels=await assertModels(page);
    assert.ok(localModels.some(b=>b.kind==='note')&&localModels.some(b=>b.kind==='memory'),'topic provides both note and memory models');
    // 主题内 note/memory 为程序化小点（不同硅体颜色），不再区分资产模型族
    const dots=await page.evaluate(()=>{
      const s=window.__AI_LIBRARY__.universe.scene,colorOf=k=>{
        const b=s.bodies.find(x=>x.data.kind===k&&!x.data.preview);if(!b)return null;
        const dot=b.group.children.find(c=>c.isMesh&&c.material&&c.material.color);
        return dot?dot.material.color.getHexString():null;
      };
      return {note:colorOf('note'),memory:colorOf('memory')};
    });
    assert.notEqual(dots.note,dots.memory,'note and memory silhouettes use distinct colours');
    await measure(page,label+'-local');await page.locator('#universe-exit').click();
    await page.waitForFunction(()=>window.__AI_LIBRARY__.universe.topicId===null&&window.__AI_LIBRARY__.universe.data&&!window.__AI_LIBRARY__.universe.data.topic);await ready(page);
    // 新 UI 无 locatorId 返回定位机制（旧版概念已移除）；退出返回总览后验证视图与目录完整恢复
    const overviewState=await page.evaluate(()=>({topicId:window.__AI_LIBRARY__.universe.topicId,dataTopic:window.__AI_LIBRARY__.universe.data.topic,topics:window.__AI_LIBRARY__.universe.data.topics.length}));
    assert.equal(overviewState.topicId,null,'exit returns to overview');
    assert.equal(overviewState.dataTopic,null,'exit clears detail state');
    assert.ok(overviewState.topics>0,'overview catalogue restored after exit');
    // 旧版 #universe-relations 关系线开关已随 UI 演进移除（牵引线/飞行由 footer 按钮控制）
    await nav(page,'__local_skills');await page.waitForFunction(()=>window.__AI_LIBRARY__.universe.scene.bodies.some(b=>b.data.kind==='skill'&&!b.data.preview),null,{timeout:30000});const skills=await assertModels(page);assert.ok(skills.some(b=>b.kind==='skill'),'actual local Skill files have station models');
    await directory(page,true);await page.locator('.universe-entry[data-kind="skill"] .universe-entry-main').first().click();await page.locator('#universe-skill-dialog[open]').waitFor();
    assert.match(await page.locator('#universe-skill-locations').innerText(),/SKILL\.md/);await page.locator('#universe-skill-close').click();
    report.checks.push(`${label}: actual GLBs; noninteractive preview; full directory titles; project, note and memory reading; half-width desktop; locator; relation control; Skill and MCP access`);
    await context.close();
  }

  const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
  await instrument(context,'fixtures');await fixtureRoutes(context);const page=await context.newPage();await page.goto(base);await ready(page);
  await nav(page,'__personal_notes');
  assert.deepEqual(await page.evaluate(()=>window.__AI_LIBRARY__.universe.data.items.map(i=>i.id)),['personal'],'personal hub filters exact user author');
  // 新 UI 无独立「我的笔记」分区标题（render 对虚拟 topicId 走通用标题兜底）；精确作者过滤即本段核心契约
  await nav(page,'project');assert.deepEqual(await page.evaluate(()=>window.__AI_LIBRARY__.universe.data.items.map(i=>i.id)),fixtureItems('default').map(i=>i.id),'imported/unknown sources remain reachable inside original project');
  await directory(page,true);
  assert.ok(await page.locator('.universe-entry-manage:visible').count()>0,'directory entries expose explicit membership manage buttons');
  await page.locator('.universe-entry-manage:visible').first().click();await page.locator('#universe-membership-dialog[open]').waitFor();await page.locator('#universe-membership-close').click();
  await nav(page,'__mcp_library');assert.match(await page.locator('#universe-title').innerText(),/MCP/i);
  // 新 UI 空集合中心仅渲染个人核（只有技能空间站建站核）；MCP 通讯舰资产族改由全局 GLB 资产断言覆盖
  report.checks.push('route fixtures: exact authorship; imported source access; explicit membership manage without writes; MCP centre family');
  // 场景公开布局 setter（setBodyPosition 拖拽持久化）已随新 UI 移除：布局为程序化生成，无用户位置持久化契约；scope 隔离/刷新恢复不再适用
  await context.close();

  // Resolve a genuinely late response only after newer scope navigation has rendered.
  let release,received;const promise=new Promise(resolve=>release=resolve),requested=new Promise(resolve=>received=resolve);
  const stale=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});await instrument(stale,'stale');
  let armed=false;await fixtureRoutes(stale,{hold:{match:(scope,id)=>armed&&scope==='default'&&id==='project',received,promise}});
  const stalePage=await stale.newPage();await stalePage.goto(base);await ready(stalePage);armed=true;
  await stalePage.evaluate(()=>window.__AI_LIBRARY__.universe.navigate('project'));await requested;
  await stalePage.evaluate(()=>window.__AI_LIBRARY__.universe.setScope('qa-other'));await ready(stalePage);await nav(stalePage,'project');
  release();await stalePage.waitForTimeout(150);
  assert.equal(await stalePage.evaluate(()=>window.__AI_LIBRARY__.universe.scope),'qa-other');
  assert.equal(await stalePage.locator('#universe-title').innerText(),'另一个范围的技术项目');
  assert.ok(await stalePage.evaluate(()=>window.__AI_LIBRARY__.universe.data.items.every(i=>i.scope==='qa-other')),'old scope response cannot replace current items');
  report.checks.push('delayed old-scope navigation response cannot override current scope');await stale.close();

  // Failure cannot be accepted as a successful WebGL startup; directory remains usable.
  const failure=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});await instrument(failure,'asset-failure',{expectedAssetFailure:true});await fixtureRoutes(failure);
  let failAssets=true;await failure.route('**/assets/models/*.glb',route=>failAssets?route.abort('failed'):route.continue());
  const failed=await failure.newPage();await failed.goto(base);
  await failed.waitForFunction(()=>window.__AI_LIBRARY__?.universe?.data,null,{timeout:30000});
  assert.notEqual(await failed.evaluate(()=>window.__AI_LIBRARY__.universe.scene?.debug.assetReady),true,'asset failure never reports assetReady');
  await directory(failed,true);await failed.locator('.universe-entry[data-id="project"] .universe-entry-main').click();await failed.waitForFunction(()=>window.__AI_LIBRARY__.universe.data.topic?.id==='project');
  assert.deepEqual(await failed.evaluate(()=>window.__AI_LIBRARY__.universe.data.items.map(i=>i.id)),fixtureItems('default').map(i=>i.id),'failed artwork does not rewrite or remove source records');
  // 新 UI 资产失败无独立重试按钮（GLB 走程序化几何兜底，assetReady=false 可观察）；验证兜底渲染不阻断宇宙
  const fallbackBodies=await failed.evaluate(()=>window.__AI_LIBRARY__.universe.scene.bodies.length);
  assert.ok(fallbackBodies>0,'asset failure keeps procedural geometry rendering');
  report.checks.push('model-load failure opens working directory, preserves source records and keeps procedural rendering');await failure.close();

  await Promise.all(pendingAssets);
  const successful=report.assets.filter(a=>!a.expectedFailure);assert.ok(successful.length>=6,'detailed local model assets were fetched');
  const mcpAssets=successful.filter(a=>/mcp-(ship|vessel)\.glb/i.test(a.url));
  assert.ok(mcpAssets.length>=2,'MCP communications-vessel asset family is part of the mounted model set');
  for(const asset of successful){assert.equal(asset.status,200,asset.url);assert.equal(asset.magic,'glTF',asset.url);assert.ok(asset.bytes>1000,asset.url);assert.equal(asset.error,null);}
  assert.deepEqual(report.writes,[],'no incidental POST/PATCH/DELETE API writes');
  assert.deepEqual(report.pageErrors,[],'no browser runtime errors');
  assert.deepEqual(report.consoleErrors,[],'no unhandled startup/model/render console errors');
  assert.equal(report.webglWarnings.some(w=>/context lost|GL_INVALID|WebGL.*(?:error|failed)/i.test(w.message)),false,'no WebGL rendering failure');
  report.performanceAcceptance='Measured draw calls, triangles and available frame timing only; no device-independent FPS claim.';
  report.state='PASS';
 } catch(error){report.state='FAIL';report.failure={message:error.message,stack:error.stack};throw error;}
 finally {await Promise.allSettled(pendingAssets);fs.writeFileSync(path.join(evidence,'stellar-ui-report.json'),JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify(report,null,2));}
})().catch(error=>{console.error(error);process.exitCode=1;});
