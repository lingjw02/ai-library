// Read-only UI contract checks with explicit synthetic telemetry fixtures; never writes library data.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'D:/02_Development_Tools/live2d-maker/node_modules/playwright-core');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const base=process.env.SPIDER_TEST_URL || 'http://127.0.0.1:8877';
const out='.agent-tmp/spider-mecha-20261012/browser';
(async()=>{
 fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({headless:true});const report={checks:[],errors:[]};
 try {
  for(const [name,width,height,motion] of [['desktop',1440,1000,'no-preference'],['mobile',390,844,'reduce']]) {
   const page=await browser.newPage({viewport:{width,height},reducedMotion:motion});
   let payload={applications:[],server_time:Date.now()/1000},fail=false,requests=0;const writes=[];
   page.on('pageerror',e=>report.errors.push(e.message));
   page.on('request',r=>{if(r.url().includes('/api/spider-mecha')) {requests++; if(r.method()!=='GET')writes.push(r.method());}});
   await page.route('**/api/spider-mecha?**',r=>r.fulfill({status:fail?503:200,contentType:'application/json',body:JSON.stringify(fail?{detail:'Unavailable'}:payload)}));
   await page.goto(base);
   await page.waitForFunction(()=>window.__AI_LIBRARY__?.universe.data && window.__AI_LIBRARY__.universe.scene?.debug.ready);
   // 机甲面板位于"🤖 AI 机甲 Observers"legend tab 内，默认不可见；先切换到该 tab 再断言状态
   await page.locator('.legend-tab-btn').filter({hasText:/机甲|Observers/i}).click();
   await page.locator('#spider-mecha-status').filter({hasText:'暂无'}).waitFor();
   await page.locator('#universe-warp-loading').waitFor({state:'hidden'});
   const fixture=(id,topic,label='会话')=>({id,label,identity_kind:'chat',state:'active',position:{topic_id:topic},mode:'fly',events:[{seq:1,tool:'drive_spider',action:'enter',status:'completed',topic_id:topic,timestamp:Date.now()/1000}]});
   await page.evaluate(()=>{const u=window.__AI_LIBRARY__.universe;u.scene.setData({overview:true,topics:[{id:'qa-topic-a',title:'Topic A'},{id:'qa-topic-b',title:'Topic B'}],items:[]},true);});
   payload={applications:[{id:'codex',name:'Codex',style:'codex',state:'unknown',sessions:[fixture('chat-a','qa-topic-a','<img src=x onerror=alert(1)>'),fixture('chat-b','qa-topic-b','会话 B')]},{id:'antigravity',name:'Antigravity',style:'antigravity',state:'idle',sessions:[]}],server_time:Date.now()/1000};
   await page.evaluate(()=>window.__AI_LIBRARY__.universe.spiders.refresh());
   await page.locator('[data-spider-session="chat-b"]').waitFor();
   assert.equal(await page.locator('#spider-mecha-list img').count(),0);
   assert.match(await page.locator('#spider-mecha-list').innerText(),/<img src=x/);
   assert.equal(await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.spiders.debug.models.length),3);
   assert.ok(await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.spiders.debug.models.every(x=>x.legs===8)));
   const uuid=await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.spiders.debug.models[0].uuid);
   await page.evaluate(()=>{const scene=window.__AI_LIBRARY__.universe.scene;scene.setData(scene.data,true);});
   assert.equal(await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.spiders.debug.models[0].uuid),uuid);
   const style=await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.spiders.debug.models.map(x=>x.shape));assert.ok(new Set(style).size>=2);
   await page.locator('[data-spider-session="chat-b"] button').click();
   assert.equal(await page.locator('#spider-mecha-follow').getAttribute('aria-pressed'),'false');
   await page.locator('#spider-mecha-follow').click();assert.equal(await page.locator('#spider-mecha-follow').getAttribute('aria-pressed'),'true');
   const focused=await page.evaluate(()=>document.activeElement.id);await page.evaluate(()=>window.__AI_LIBRARY__.universe.spiders.refresh());assert.equal(await page.evaluate(()=>document.activeElement.id),focused);
   await page.waitForTimeout(600);await page.screenshot({path:`${out}/${name}-follow.png`});await page.locator('#universe-reset').click();assert.equal(await page.locator('#spider-mecha-follow').getAttribute('aria-pressed'),'false');
   if(name==='desktop'){
    await page.locator('#spider-mecha-follow').click();await page.mouse.move(600,420);await page.mouse.down();await page.mouse.move(680,440,{steps:4});await page.mouse.up();assert.equal(await page.locator('#spider-mecha-follow').getAttribute('aria-pressed'),'false');
   }
   // A real canonical follow event may crawl only where that actual edge is visible.
   await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.setTractorLines([{from_id:'qa-topic-a',to_id:'qa-topic-b',type:'CITATION'}]));
   payload.applications[0].sessions[1].mode='crawl';payload.applications[0].sessions[1].events.push({seq:2,tool:'drive_spider',action:'follow',status:'completed',from_topic_id:'qa-topic-a',topic_id:'qa-topic-b',timestamp:Date.now()/1000});
   await page.evaluate(()=>window.__AI_LIBRARY__.universe.spiders.refresh());assert.match(await page.locator('[data-spider-session="chat-b"]').innerText(),/沿关联爬行/);
   await page.evaluate(()=>{const s=window.__AI_LIBRARY__.universe.scene;s.setPaused(true);});
   const progress=await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.spiders.debug.models.find(x=>x.id==='codex:chat-b').progress);await page.waitForTimeout(300);assert.equal(await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.spiders.debug.models.find(x=>x.id==='codex:chat-b').progress),progress);
   await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.setPaused(false));
   await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.setTractorLines([]));await page.evaluate(()=>window.__AI_LIBRARY__.universe.spiders.refresh());assert.match(await page.locator('[data-spider-session="chat-b"]').innerText(),/路径未在当前视图/);
   payload.applications[0].sessions[0].position={topic_id:'outside-visible-view'};
   await page.evaluate(()=>window.__AI_LIBRARY__.universe.spiders.refresh());
   assert.match(await page.locator('[data-spider-session="chat-a"]').innerText(),/当前视图外/);
   payload.applications[0].sessions[0].state='unknown';await page.evaluate(()=>window.__AI_LIBRARY__.universe.spiders.refresh());assert.match(await page.locator('[data-spider-session="chat-a"]').innerText(),/未知/);
   payload.applications[0].sessions[0].position={};await page.evaluate(()=>window.__AI_LIBRARY__.universe.spiders.refresh());assert.match(await page.locator('[data-spider-session="chat-a"]').innerText(),/尚无可定位资料/);
   await page.screenshot({path:`${out}/${name}.png`});
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   payload.applications[0].sessions.forEach(s=>s.state='idle');await page.evaluate(()=>window.__AI_LIBRARY__.universe.spiders.refresh());
   assert.deepEqual(await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.spiders.debug.models.map(m=>m.id).sort()),['antigravity:__dock','codex:__dock']);
   assert.equal(await page.locator('[data-spider-session="chat-b"]').isVisible(),true);
   await page.locator('[data-spider-session="chat-b"] button').click();assert.match(await page.locator('#spider-mecha-detail').innerText(),/沿关联探索/);assert.equal(await page.locator('#spider-mecha-follow').isDisabled(),true);
   payload.applications[0].sessions=[];await page.evaluate(()=>window.__AI_LIBRARY__.universe.spiders.refresh());assert.equal(await page.locator('[data-spider-session="chat-b"]').count(),0);
   assert.match(await page.locator('#spider-mecha-detail').innerText(),/选择会话/);
   payload.applications[0].state='disconnected';await page.evaluate(()=>window.__AI_LIBRARY__.universe.spiders.refresh());assert.deepEqual(await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.spiders.debug.models.map(m=>m.id)),['antigravity:__dock']);assert.match(await page.locator('#spider-mecha-list').innerText(),/连接中断/);
   payload={applications:['claude','custom-alpha','custom-beta'].map(id=>({id,name:id,style:id,state:'idle',sessions:[]})),server_time:Date.now()/1000};await page.evaluate(()=>window.__AI_LIBRARY__.universe.spiders.refresh());
   const models=await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.spiders.debug.models);assert.equal(new Set(models.map(m=>m.shape)).size,3);assert.ok(models.every(m=>m.legs===8));assert.match(models.find(m=>m.id==='claude:__dock').shape,/claude/);
   const structures=await page.evaluate(()=>[...window.__AI_LIBRARY__.universe.scene.spiders.models.values()].map(m=>{const shape=[];m.object.traverse(o=>{if(o.geometry)shape.push([o.geometry.type,o.geometry.parameters,o.position.toArray(),o.scale.toArray()]);});return JSON.stringify(shape);}));assert.equal(new Set(structures).size,3);
   const docks=await page.evaluate(()=>Object.fromEntries(window.__AI_LIBRARY__.universe.scene.spiders.debug.models.map(m=>[m.id,m.position])));payload.applications.reverse();await page.evaluate(()=>window.__AI_LIBRARY__.universe.spiders.refresh());assert.deepEqual(await page.evaluate(()=>Object.fromEntries(window.__AI_LIBRARY__.universe.scene.spiders.debug.models.map(m=>[m.id,m.position]))),docks);
   const before=requests;await page.locator('#universe-maintenance').click();await page.waitForTimeout(2600);assert.equal(requests,before);await page.locator('#universe-maintenance').click();
   fail=true;await page.evaluate(()=>window.__AI_LIBRARY__.universe.spiders.refresh());await page.locator('#spider-mecha-status').filter({hasText:'无法'}).waitFor();assert.equal(await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.spiders.debug.models.length),0);
   fail=false;payload={applications:[],server_time:Date.now()/1000};await page.evaluate(()=>window.__AI_LIBRARY__.universe.spiders.refresh());assert.match(await page.locator('#spider-mecha-status').innerText(),/暂无/);
   // The old scope's delayed snapshot must not repopulate projections after scope change.
   await page.unroute('**/api/spider-mecha?**');
   await page.route('**/api/spider-mecha?**',async route=>{const scope=new URL(route.request().url()).searchParams.get('scope');if(scope==='default')await new Promise(r=>setTimeout(r,350));await route.fulfill({contentType:'application/json',body:JSON.stringify({applications:scope==='default'?[{id:'old-scope-app',name:'Old scope',style:'codex',sessions:[]}]:[],server_time:Date.now()/1000})}).catch(()=>{});});
   await page.evaluate(()=>{const spiders=window.__AI_LIBRARY__.universe.spiders;spiders.refresh();spiders.setScope('qa-second-scope');});
   await page.waitForTimeout(650);assert.equal(await page.evaluate(()=>window.__AI_LIBRARY__.universe.spiders.debug.applications),0);assert.equal(await page.locator('#spider-mecha-list').innerText(),'');
   assert.deepEqual(writes,[]);
   if(motion==='reduce')assert.equal(await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.debug.reducedMotion),true);
   report.checks.push(`${name}: scope race; empty/error telemetry; distinct 8-leg persistent models; parallel escaped labels; follow opt-in/reset/manual cancel; focus stability; real link crawl/missing path; paused progress; idle docks/end cleanup; hidden polling pause; read-only; viewport; reduced motion`);
   await page.close();
  }
  assert.deepEqual(report.errors,[]);fs.writeFileSync(`${out}/report.json`,JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
