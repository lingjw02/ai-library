const {chromium}=require('D:/02_Development_Tools/live2d-maker/node_modules/playwright-core');const assert=require('node:assert/strict');const fs=require('node:fs');
(async()=>{const b=await chromium.launch({headless:true});const report={checks:[],errors:[],writes:[]};try{for(const width of [1440,612,390]){
const p=await b.newPage({viewport:{width,height:width===1440?1000:844},reducedMotion:'reduce'});p.on('pageerror',e=>report.errors.push(e.message));p.on('request',r=>{if(r.url().includes('/api/')&&r.method()!=='GET')report.writes.push(r.method());});
await p.goto('http://127.0.0.1:8000/');await p.waitForFunction(()=>window.__AI_LIBRARY__?.universe.data?.topics.length&&window.__AI_LIBRARY__.universe.scene?.debug.ready,null,{timeout:60000});
assert.equal(await p.locator('.universe-filters').count(),0);assert.equal(await p.locator('.neural-legend-entry').count(),await p.evaluate(()=>window.__AI_LIBRARY__.universe.data.topics.length));
assert.ok(await p.evaluate(()=>window.__AI_LIBRARY__.universe.scene.bodies.filter(b=>b.data.kind==='topic'&&!b.data.preview).every(b=>b.group.children.length>25)));
await p.screenshot({path:`.agent-tmp/neural-redesign-20261011/final-overview-${width}.png`});
const visible=await p.locator('button.universe-label:visible').evaluateAll(els=>els.map(el=>{const r=el.getBoundingClientRect();return {left:r.left,right:r.right};}));assert.ok(visible.every(r=>r.left>=0&&r.right<=width+1),'no clipped topic labels');
await p.keyboard.press('Control+k');assert.equal(await p.locator('#universe-search').evaluate(e=>e===document.activeElement),true);
await p.locator('#universe-directory-toggle').click();await p.locator('.universe-entry-main').filter({hasText:'AI library'}).first().click();await p.waitForFunction(()=>window.__AI_LIBRARY__.universe.data.topic?.title==='AI library');
assert.ok(await p.locator('#universe-exit').isVisible());await p.locator('.universe-entry[data-kind="note"] .universe-entry-main').first().click();await p.locator('#note-modal-backdrop.open').waitFor();assert.ok(await p.evaluate(()=>window.__AI_LIBRARY__.universe.scene.reading));
await p.screenshot({path:`.agent-tmp/neural-redesign-20261011/final-reader-${width}.png`});await p.locator('#note-modal-close').click();
await p.locator('#universe-exit').click();await p.waitForFunction(()=>window.__AI_LIBRARY__.universe.topicId===null&&window.__AI_LIBRARY__.universe.data.topic===null);
await p.locator('#universe-maintenance').click();assert.ok(await p.locator('#workspace-maintenance').isVisible());await p.locator('#neural-overview').click();assert.ok(await p.locator('#workspace-universe').isVisible());
assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));report.checks.push(`${width}: reference scene, actual legend, label bounds, Ctrl+K, topic and reader, exit, maintenance, no overflow`);await p.close();}
assert.deepEqual(report.errors,[]);assert.deepEqual(report.writes,[]);report.state='PASS';fs.writeFileSync('.agent-tmp/neural-redesign-20261011/report.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));}finally{await b.close();}})().catch(e=>{console.error(e);process.exitCode=1});
