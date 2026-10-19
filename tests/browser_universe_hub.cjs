// Read-only acceptance of the fixed Notes hub and continuous, unpaged catalogue.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'D:/02_Development_Tools/live2d-maker/node_modules/playwright-core');
const assert=require('node:assert/strict'),fs=require('node:fs');
(async()=>{const browser=await chromium.launch({headless:true});const report={checks:[],errors:[],writes:[]};
try{
for(const [device,width,height] of [['desktop',1440,1000],['mobile',390,844]]){
 const page=await browser.newPage({viewport:{width,height},reducedMotion:'reduce'});
 page.on('pageerror',e=>report.errors.push(e.message));page.on('request',r=>{if(r.url().includes('/api/')&&r.method()!=='GET')report.writes.push(r.method());});
 await page.goto('http://127.0.0.1:8000/');await page.waitForFunction(()=>window.__AI_LIBRARY__?.universe.data&&window.__AI_LIBRARY__.universe.scene?.debug.ready);
 assert.ok(await page.evaluate(()=>{const b=window.__AI_LIBRARY__.universe.scene.bodies;const hub=b.find(x=>x.data.kind==='notes-hub');return hub&&hub.radius>=4&&b.every(x=>x===hub||x.radius<hub.radius)&&hub.group.position.length()===0;}),'enlarged centred notes-hub core exists in the 3D scene');
 assert.equal(await page.locator('#universe-next,#universe-prev,#universe-page').count(),0,'no pagination controls');
 await page.waitForTimeout(150);await page.screenshot({path:`.agent-tmp/universe-hub-20261011/${device}-overview.png`});
 await page.locator('#universe-directory-toggle').click();await page.locator('.universe-entry-main').filter({hasText:'AI library'}).first().click();
 await page.waitForFunction(()=>window.__AI_LIBRARY__.universe.data.topic?.title==='AI library');
 const count=await page.evaluate(()=>{const u=window.__AI_LIBRARY__.universe;return {total:u.data.total,rendered:u.scene.bodies.filter(b=>b.data.kind!=='notes-hub').length,loaded:u.data.topics.length+u.data.items.length};});
 assert.ok(count.total>48);assert.equal(count.loaded,count.total);assert.equal(count.rendered,count.total);
 if(await page.locator('#universe-directory').isVisible())await page.locator('#universe-directory-close').click();
 await page.screenshot({path:`.agent-tmp/universe-hub-20261011/${device}-topic.png`});
 // 主题内读取一条笔记并返回（保持只读）
 await page.locator('#universe-directory-toggle').click();
 const noteRow=page.locator('.universe-entry[data-kind="note"] .universe-entry-main').first();
 if(await noteRow.count()){await noteRow.click();await page.locator('#note-modal-backdrop.open').waitFor();await page.locator('#note-modal-close').click();}
 await page.locator('#universe-home').click();await page.waitForFunction(()=>window.__AI_LIBRARY__.universe.topicId===null&&window.__AI_LIBRARY__.universe.data&&!window.__AI_LIBRARY__.universe.data.topic);
 if(await page.locator('#universe-directory').isVisible())await page.locator('#universe-directory-close').click();
 // 总览点击 notes-hub 主核：当前目标态是进入"我的空间"视图（scene 稳定后投影点击，miss 则重试）
 await page.waitForFunction(()=>window.__AI_LIBRARY__.universe.scene?.bodies?.find(b=>b.data.kind==='notes-hub'));
 await page.waitForTimeout(1000);
 const hubPoint=await page.evaluate(()=>{const u=window.__AI_LIBRARY__.universe,s=u.scene;const hub=s.bodies.find(b=>b.data.kind==='notes-hub');if(!hub)return null;const rect=s.renderer.domElement.getBoundingClientRect();const p=hub.group.position.clone();s.camera.updateMatrixWorld();p.project(s.camera);return {x:rect.left+(p.x*.5+.5)*rect.width,y:rect.top+(-p.y*.5+.5)*rect.height};});
 assert.ok(hubPoint,'notes-hub sphere has a real projected screen position');
 let landed=false;
 for(let i=0;i<3&&!landed;i++){await page.mouse.click(hubPoint.x,hubPoint.y);await page.waitForTimeout(250);landed=await page.evaluate(()=>window.__AI_LIBRARY__.universe.perspective==='agent');}
 assert.ok(landed,'click lands on notes-hub and opens personal space');
 await page.waitForFunction(()=>document.getElementById('universe-title')?.textContent==='我的空间');
 assert.equal(await page.locator('#universe-title').innerText(),'我的空间');
 report.checks.push(`${device}: enlarged fixed hub, real sphere click enters personal space, >48 objects coexist, no pagination, note reading and return`);
 await page.close();
}
const p=await browser.newPage();await p.goto('http://127.0.0.1:8000/');await p.waitForFunction(()=>window.__AI_LIBRARY__?.universe.data);
const batchResult=await p.evaluate(async()=>{
 const {collectUniverse}=await import('/js/universe-catalogue.js');let requests=0;
 const fake=async params=>{requests++;const offset=Number(params.get('offset'));const limit=Number(params.get('limit'));return {topics:[],items:Array.from({length:Math.min(limit,510-offset)},(_,i)=>({id:'fake-'+(offset+i),kind:'note'})),total:510,counts:{notes:510}};};
 const data=await collectUniverse(new URLSearchParams({scope:'default'}),fake,()=>true);
 let stopped=0;const cancelled=await collectUniverse(new URLSearchParams(),async q=>{stopped++;return fake(q);},()=>false);
 let failed=false;try{await collectUniverse(new URLSearchParams(),async q=>Number(q.get('offset'))?{topics:[],items:[],total:510}:fake(q),()=>true);}catch{failed=true;}
 return {loaded:data.items.length,requests,cancelled:cancelled===null,stopped,failed};
});
assert.equal(batchResult.loaded,510);assert.equal(batchResult.cancelled,true);assert.equal(batchResult.stopped,0);assert.equal(batchResult.failed,true);
report.checks.push('automatic multi-batch loading beyond API limit; stale requests stop; incomplete batch reports error');await p.close();
assert.deepEqual(report.errors,[]);assert.deepEqual(report.writes,[]);report.state='PASS';fs.writeFileSync('.agent-tmp/universe-hub-20261011/report.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
