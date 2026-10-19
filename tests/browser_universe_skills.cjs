const {chromium}=require('D:/02_Development_Tools/live2d-maker/node_modules/playwright-core');const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({headless:true});const errors=[],writes=[];try{for(const width of [1440,390]){
const page=await browser.newPage({viewport:{width,height:width===390?844:1000},reducedMotion:'reduce'});page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().includes('/api/')&&r.method()!=='GET')writes.push(r.method());});
await page.goto('http://127.0.0.1:8000/');await page.waitForFunction(()=>window.__AI_LIBRARY__?.universe.data?.topics.some(t=>t.virtual),{},{timeout:60000});
if(process.env.CAPTURE_SCREENSHOTS!=='0')await page.screenshot({path:`.agent-tmp/universe-preview-20261011/skills-overview-${width}.png`});
await page.locator('#universe-directory-toggle').click();await page.locator('.universe-entry-main').filter({hasText:/^Skills/}).click();await page.waitForFunction(()=>window.__AI_LIBRARY__.universe.data.topic?.virtual,{},{timeout:60000});
const total=await page.locator('.universe-entry[data-kind="skill"]').count();assert.ok(total>0);
assert.equal(await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.bodies.filter(b=>b.data.kind==='skill').length),total);
assert.equal(await page.locator('.universe-entry-manage').count(),0);assert.equal(await page.locator('#universe-rename').isVisible(),false);
if(process.env.CAPTURE_SCREENSHOTS!=='0')await page.screenshot({path:`.agent-tmp/universe-preview-20261011/skills-inside-${width}.png`});
const name=await page.locator('.universe-entry-main strong').first().innerText();await page.locator('.universe-entry-main').first().click();await page.locator('#universe-skill-dialog[open]').waitFor();assert.equal(await page.locator('#universe-skill-title').innerText(),name);assert.ok((await page.locator('#universe-skill-locations').innerText()).includes('SKILL.md'));await page.locator('#universe-skill-close').click();
await page.locator('#universe-search').fill('brainstorming');await page.waitForFunction(()=>{const u=window.__AI_LIBRARY__.universe;return u.query==='brainstorming'&&u.data.topic?.virtual&&u.data.items.every(i=>(i.title+' '+i.description).toLowerCase().includes('brainstorming'));},{},{timeout:60000});
assert.ok(await page.locator('.universe-entry-main').count()>0);
await page.locator('#universe-skill-related').click();await page.waitForFunction(()=>window.__AI_LIBRARY__.universe.data.topic?.title==='Skills / 工具配置');assert.ok(await page.evaluate(()=>window.__AI_LIBRARY__.universe.data.items.length)>=4);
await page.locator('#universe-exit').click();await page.waitForFunction(()=>window.__AI_LIBRARY__.universe.topicId===null&&window.__AI_LIBRARY__.universe.data.topics.some(t=>t.virtual),{},{timeout:60000});
assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));console.log(JSON.stringify({width,total,name,historyPreserved:true}));await page.close();
}assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);console.log('PASS: names, models, source locations, search, original history, exit, 0 page errors, 0 writes');}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
