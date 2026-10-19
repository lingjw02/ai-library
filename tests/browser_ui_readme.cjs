// Read-only UI regression against a running library. Never saves test content.
// 维修说明（2026-10-13）：适配知识宇宙 UI——旧便签/顶层 Tab/记忆图面板均已移除；
// 入口改为 universe-add/universe-maintenance/目录+记忆抽屉，断言相应更新。
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'D:/02_Development_Tools/live2d-maker/node_modules/playwright-core');
const fs=require('node:fs');
const assert=require('node:assert/strict');
const out=process.env.UI_EVIDENCE_DIR || '.agent-tmp/ui-readme-20261010';
fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true});
 const report={checks:[],errors:[],writes:[]};
 try {
  for(const [name,width,height,reducedMotion] of [['desktop',1440,1000,'no-preference'],['mobile',390,844,'reduce']]) {
   const context=await browser.newContext({viewport:{width,height},reducedMotion});
   const page=await context.newPage();
   page.on('pageerror',e=>report.errors.push(e.message));
   page.on('request',r=>{if(r.url().includes('/api/') && r.method()!=='GET') report.writes.push(r.method()+' '+r.url());});
   await page.goto('http://127.0.0.1:8000');
   await page.waitForFunction(()=>window.__AI_LIBRARY__?.universe.data && window.__AI_LIBRARY__.universe.scene?.debug.ready);
   assert.equal(await page.locator('.legend-tab-btn').count(),2);
   assert.ok(await page.locator('.universe-canvas').count());
   await page.screenshot({path:`${out}/${name}-notes.png`});
   // 统一添加入口与草稿保护（旧 #btn-add-material 隐藏，入口为 #universe-add）
   await page.locator('#universe-add').click();
   assert.ok(await page.locator('#material-dialog').isVisible());
   await page.locator('#material-new-note').click();
   await page.locator('#note-edit-content').waitFor({state:'visible'});
   await page.locator('#note-edit-content').fill('temporary unsaved UI draft');
   page.once('dialog',d=>d.dismiss());
   await page.locator('#note-modal-close').click();
   assert.equal(await page.locator('#note-edit-content').inputValue(),'temporary unsaved UI draft');
   await page.locator('#note-edit-content').fill('');
   await page.locator('#note-modal-close').click();
   assert.equal(await page.evaluate(()=>document.activeElement.id),'universe-add');
   report.checks.push(`${name}: unified entry, draft protection, focus restored`);
   // 目录进入真实主题，读取记忆来源（旧记忆图面板已移除，走记忆抽屉）
   await page.locator('#universe-directory-toggle').click();
   await page.locator('.universe-entry-main').filter({hasText:'AI library'}).first().click();
   await page.waitForFunction(()=>window.__AI_LIBRARY__.universe.data.topic?.title==='AI library');
   const memRow=page.locator('.universe-entry[data-kind="memory"] .universe-entry-main').first();
   if(await memRow.count()){
    await memRow.click();
    await page.locator('#memory-detail-drawer.open').waitFor();
    assert.match(await page.locator('#drawer-verification-status').innerText(),/核验/);
    const provenance=await page.evaluate(()=>window.__AI_LIBRARY__.memoryNet.selectedNode.provenance);
    if(provenance){
     await page.locator('[data-drawertab="source"]').click();
     await page.locator('#drawer-btn-source').click();
     await page.locator('#note-edit-content').waitFor({state:'visible'});
     assert.ok((await page.locator('#note-edit-content').inputValue()).length>0,'source copy loads');
     await page.locator('#note-modal-close').click();
    }
    if(await page.locator('#memory-detail-drawer.open').count())await page.locator('#drawer-btn-close').click();
   }
   await page.locator('#universe-home').click();
   await page.waitForFunction(()=>window.__AI_LIBRARY__.universe.topicId===null);
   await page.screenshot({path:`${out}/${name}-memory-detail.png`});
   report.checks.push(`${name}: memory provenance via drawer`);
   // 维护面板（旧 #tab-btn-maintenance 隐藏，入口为 #universe-maintenance）
   await page.locator('#universe-maintenance').click();
   await page.locator('#sub-btn-logsheet').click();
   await page.waitForTimeout(450);
   const inspect=page.locator('#maintenance-list .btn-inspect').first();
   await inspect.click();
   await page.locator('#maint-drawer-btn-close').click();
   await page.screenshot({path:`${out}/${name}-maintenance.png`});
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   if(name==='mobile')assert.equal(await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.debug.reducedMotion),true);
   report.checks.push(`${name}: maintenance reachable, no viewport overflow, motion preference`);
   await context.close();
  }
  assert.deepEqual(report.errors,[]); assert.deepEqual(report.writes,[]);
  report.state='PASS';
  fs.writeFileSync(`${out}/browser-report.json`,JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
