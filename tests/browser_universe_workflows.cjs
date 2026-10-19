// Functional regression only, no new visual iteration. Uses isolated QA data.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'D:/02_Development_Tools/live2d-maker/node_modules/playwright-core');
const assert=require('node:assert/strict');const fs=require('node:fs');
const base='http://127.0.0.1:8768';
(async()=>{
 const browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
 const page=await context.newPage(),errors=[],checks=[];page.on('pageerror',e=>errors.push(e.message));
 const get=async p=>(await context.request.get(base+p)).json();
 const post=async(p,data)=>{const r=await context.request.post(base+p,{data});assert.ok(r.ok(),await r.text());return r.json();};
 try{
  await page.goto(base);await page.waitForFunction(()=>window.__AI_LIBRARY__?.universe.data);
  await page.locator('#universe-directory-toggle').click();
  const scope='universe-regression-'+Date.now();
  await page.locator('#universe-scope').fill(scope);await page.locator('#universe-scope').press('Tab');
  await page.waitForFunction(s=>window.__AI_LIBRARY__.universe.scope===s&&window.__AI_LIBRARY__.universe.data?.counts.notes===0,scope);
  assert.equal(await page.locator('.universe-entry[data-kind="note"],.universe-entry[data-kind="memory"]').count(),0);
  await page.locator('#universe-add').click();await page.locator('#material-new-note').click();
  const title='UI preservation '+Date.now(),content='  原文空白和来源版本必须保留。\n第二段。\n';
  await page.locator('#note-edit-title').fill(title);await page.locator('#note-edit-content').fill(content);await page.locator('#btn-save-note').click();
  await page.waitForFunction(()=>window.__AI_LIBRARY__.notesMgr.activeNote?.id&&!window.__AI_LIBRARY__.notesMgr.isSaving);
  const note=await page.evaluate(()=>window.__AI_LIBRARY__.notesMgr.activeNote);
  assert.equal(note.scope,scope);assert.equal((await get('/api/notes/'+note.id)).content,content);
  await page.locator('#btn-trigger-ai-analysis').click();await page.locator('[data-accept-id]').waitFor();await page.locator('[data-accept-id]').click();
  await page.waitForFunction(()=>document.querySelector('#analysis-claims-list').textContent.includes('ACCEPTED'));
  await page.waitForFunction(()=>window.__AI_LIBRARY__.universe.data.counts.memories===2);
  await page.locator('#note-modal-close').click();
  await page.locator('#universe-search').fill(title);await page.locator('#universe-search-form').evaluate(f=>f.requestSubmit());
  await page.locator('.universe-entry[data-kind="memory"] .universe-entry-main').first().click();
  await page.locator('#memory-detail-drawer.open').waitFor();
  const mem=await page.evaluate(()=>window.__AI_LIBRARY__.memoryNet.selectedNode);
  assert.match(await page.locator('#drawer-relations-list').innerText(),/DERIVED_FROM/);
  assert.equal(mem.provenance.source_id,note.id);
  // 记忆抽屉默认显示"内容"pane；打开原文来源需先切换到"来源"tab
  await page.locator('[data-drawertab="source"]').click();
  await page.locator('#drawer-btn-source').click();await page.locator('#note-modal-backdrop.open').waitFor();
  assert.equal(await page.locator('#note-edit-content').inputValue(),content);
  await page.locator('#note-edit-content').fill(content+'unsaved');
  page.once('dialog',d=>d.dismiss());
  assert.equal(await page.evaluate(()=>window.__AI_LIBRARY__.universe.setScope('default')),false);
  assert.equal(await page.locator('#note-edit-content').inputValue(),content+'unsaved');
  await page.locator('#note-edit-content').fill(content);
  // Server-side revision advances while this form has v1: save must retain draft.
  await post(`/api/notes/${note.id}/revisions`,{title,content:content+'concurrent',expected_revision:1});
  await page.locator('#note-edit-content').fill(content+'my draft');await page.locator('#btn-save-note').click();
  await page.getByText(/保存失败/).waitFor();assert.equal(await page.locator('#note-edit-content').inputValue(),content+'my draft');
  page.once('dialog',d=>d.accept());await page.locator('#note-modal-close').click();
  checks.push('scope selection, create scoped note, whitespace, accept and refresh Memory, actual relationships, provenance, draft and revision guards');
  // Delayed read cannot reopen a reader after scope changes.
  let release,received;const held=new Promise(r=>release=r),requested=new Promise(r=>received=r);
  await page.route(`**/api/notes/${note.id}`,async route=>{const response=await route.fetch();received();await held;await route.fulfill({response});});
  await page.evaluate(id=>{window.__AI_LIBRARY__.jumpToNote(id);},note.id);await requested;
  await page.evaluate(()=>window.__AI_LIBRARY__.universe.setScope('default'));release();await page.waitForTimeout(300);
  assert.equal(await page.locator('#note-modal-backdrop').evaluate(e=>e.classList.contains('open')),false);await page.unroute(`**/api/notes/${note.id}`);
  // Maintenance navigation into another existing scope remains available.
  await page.evaluate(id=>window.__AI_LIBRARY__.jumpToMemory(id),mem.id);await page.locator('#memory-detail-drawer.open').waitFor();
  assert.equal(await page.evaluate(()=>window.__AI_LIBRARY__.universe.scope),scope);await page.locator('#drawer-btn-close').click();
  checks.push('late reader blocked after scope change; cross-scope explicit navigation synchronizes workspace');
  // Existing conflict evidence review remains reachable in the new shell.
  const proposal=await post('/api/notes',{title:'Proposal '+title,content:'另一个待审核建议',scope});
  const candidate=await post('/api/candidates',{source_id:proposal.id,source_revision:1,claim:proposal.content,suggested_node_type:'Fact',payload:{content_hash:proposal.content_hash},scope});
  const conflict=await post('/api/conflicts',{scope,memory_targets:{[mem.id]:mem.version},candidate_ids:[candidate.id],kind:'SEMANTIC_CONFLICT',description:'Regression report <b>literal</b>'});
  await page.evaluate(id=>window.__AI_LIBRARY__.jumpToMemory(id),mem.id);
  await page.locator('[data-conflict-open]').first().click();await page.locator('#conflict-status').selectOption('RESOLVED');await page.locator('#conflict-reason').fill('Source checked');await page.locator('#conflict-method').fill('Manual evidence comparison');await page.locator('#conflict-save').click();
  await page.getByText('Choose a source note before closing this conflict.',{exact:true}).waitFor();
  await page.locator('#conflict-evidence').selectOption(proposal.id);await page.locator('#conflict-save').click();await page.getByText('Review saved. Existing claims were preserved.',{exact:true}).waitFor();
  assert.equal((await get(`/api/conflicts/${conflict.id}?scope=${scope}`)).core_props.status,'RESOLVED');await page.locator('#drawer-btn-close').click();
  checks.push('conflict review requires evidence and preserves original claim');
  const bad=await post('/api/candidates',{source_id:proposal.id,source_revision:1,claim:'Broken candidate',suggested_node_type:'Fact',payload:{confidence:'broken'},scope});assert.equal(bad.success,false);
  await page.locator('#universe-maintenance').click();await page.locator('#sub-btn-quarantine').click();await page.locator('#quarantine-scope').fill(scope);await page.locator('#quarantine-scope').press('Tab');await page.locator(`[data-quarantine-id="${bad.id}"]`).click();
  await page.locator('#quarantine-reason').fill('Repair numeric field');await page.locator('#quarantine-attributes').fill('{"confidence":0.5}');await page.locator('#quarantine-save').click();await page.getByText('Repair saved to a candidate. This repair did not accept or verify it.',{exact:true}).waitFor();
  assert.equal((await get(`/api/quarantine/${bad.id}?scope=${scope}`)).resolved,true);
  checks.push('quarantine repair remains accessible through maintenance and does not accept candidate');
  assert.deepEqual(errors,[]);const report={state:'PASS',checks,errors};fs.writeFileSync('.agent-tmp/universe-20261011/workflow-report.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
