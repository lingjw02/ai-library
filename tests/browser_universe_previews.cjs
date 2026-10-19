const {chromium}=require('D:/02_Development_Tools/live2d-maker/node_modules/playwright-core');const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({headless:true});let errors=[],writes=[];try{
for(const width of [1440,390]){const page=await browser.newPage({viewport:{width,height:width===390?844:1000},reducedMotion:'reduce'});page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().includes('/api/')&&r.method()!=='GET')writes.push(r.method());});await page.goto('http://127.0.0.1:8000/');await page.waitForFunction(()=>window.__AI_LIBRARY__?.universe.data?.topics[0]?.children);
const state=await page.evaluate(()=>{const u=window.__AI_LIBRARY__.universe;return {topics:u.data.topics.length,children:u.data.topics.reduce((n,t)=>n+t.children.length,0),previews:u.scene.bodies.filter(b=>b.data.preview).length};});assert.ok(state.children>0);
// universe-scene.js 总览模式对笔记预览限量点缀（非主题笔记最多 14 个，子主题全量展示），
// 因此 previews 数量应 >0 且 <= children（子主题数量），不得断言二者相等。
assert.ok(state.previews>0&&state.previews<=state.children,`previews=${state.previews} children=${state.children}`);
if(process.env.CAPTURE_SCREENSHOTS!=='0')await page.screenshot({path:`.agent-tmp/universe-preview-20261011/overview-${width}.png`});
// Pick a visible real preview via ray tests, then click its projected sphere.
const point=await page.evaluate(()=>{const u=window.__AI_LIBRARY__.universe,s=u.scene,rect=s.container.getBoundingClientRect();for(const b of s.bodies.filter(b=>b.data.preview&&b.data.kind!=='topic')){const p=b.group.position.clone().project(s.camera),x=(p.x*.5+.5)*s.width+rect.left,y=(-p.y*.5+.5)*s.height+rect.top;if(y<rect.top+220||y>rect.bottom-70)continue;if(s.pick({clientX:x,clientY:y})===b)return {x,y,kind:b.data.kind,id:b.data.id};}return null;});assert.ok(point);await page.mouse.click(point.x,point.y);await page.locator(point.kind==='note'?'#note-modal-backdrop.open':'.memory-detail-drawer.open').waitFor();console.log(JSON.stringify({width,...state,clicked:point.kind}));await page.close();}
const page=await browser.newPage();await page.goto('http://127.0.0.1:8000/');
const loaders=await page.evaluate(async()=>{
 const {collectTopicPreviews}=await import('/js/universe-catalogue.js');let active=0,peak=0;const scopes=[];
 const topics=Array.from({length:7},(_,i)=>({id:'t'+i}));
 const rows=await collectTopicPreviews(topics,'restricted',async q=>{active++;peak=Math.max(peak,active);scopes.push(q.get('scope'));await new Promise(r=>setTimeout(r,5));active--;if(q.get('topic_id')==='t3')throw new Error('unavailable');return {topics:[],items:[{id:q.get('topic_id'),kind:'note'}],total:1};},()=>true);
 let staleCalls=0;await collectTopicPreviews(topics,'restricted',async()=>{staleCalls++;},()=>false);
 return {peak,scopes,staleCalls,failed:rows[3].previewError,loaded:rows.filter(r=>r.children.length===1).length};
});
assert.ok(loaders.peak<=3);assert.ok(loaders.scopes.every(s=>s==='restricted'));assert.equal(loaders.staleCalls,0);assert.equal(loaders.failed,true);assert.equal(loaders.loaded,6);await page.close();
assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);console.log('PASS: real child bodies, direct reading, bounded preview requests, scope propagation, isolated failures, stale cancellation, 0 errors, 0 writes');}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
