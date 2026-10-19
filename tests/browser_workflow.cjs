// Run only against an isolated local test server; this creates QA records.
// PLAYWRIGHT_MODULE may point to an existing playwright-core installation.
// 维修说明（2026-10-13）：适配知识宇宙 UI——旧顶层 Tab/笔记列表按钮已移除，
// 创建走 #universe-add→#material-new-note，打开既有笔记走 #universe-search，
// 记忆跳转走 jumpToMemory，维护走 #universe-maintenance；测试意图全部保留。
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.QA_BASE_URL || 'http://127.0.0.1:8768';
if (!/^http:\/\/127\.0\.0\.1:8768$/.test(base)) throw new Error('Use the isolated loopback test server on port 8768.');
const evidence = path.resolve('.agent-tmp/qa/evidence');
fs.mkdirSync(evidence, { recursive: true });
const results = [];
const name = `QA-${Date.now()} 记忆来源`;
const content = '  本项目的记忆必须保留原文来源以及版本信息。\n第二段用于保存与检索测试。\n';
let browser;

async function main() {
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const get = async endpoint => (await context.request.get(base + endpoint)).json();
  const record = async (id, description) => {
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(evidence, `${id}.png`), fullPage: true });
    results.push({ id, description, state: 'PASSED', evidence: `${id}.png` });
    fs.writeFileSync(path.join(evidence, 'results.json'), JSON.stringify({ browser: browser.version(), viewport: '1440x1000', results, errors }, null, 2));
    console.log(`PASS ${id}: ${description}`);
  };
  const openNote = async keyword => {
    await page.locator('#universe-search').fill(keyword);
    await page.locator('#universe-search-form').evaluate(f => f.requestSubmit());
    await page.locator('.universe-entry[data-kind="note"] .universe-entry-main').filter({ hasText: keyword }).first().click();
    await page.locator('#note-modal-backdrop.open').waitFor();
  };
  const createNote = async () => {
    await page.locator('#universe-add').click();
    await page.locator('#material-new-note').click();
  };
  await page.goto(base);
  await page.waitForFunction(() => window.__AI_LIBRARY__?.universe.data && window.__AI_LIBRARY__.universe.scene?.debug.ready);
  await createNote();
  await page.locator('#note-edit-title').fill(name);
  await page.locator('#note-edit-content').fill(content);
  await page.locator('#btn-save-note').click();
  await page.waitForFunction(() => window.__AI_LIBRARY__.notesMgr.activeNote?.id && !window.__AI_LIBRARY__.notesMgr.isSaving);
  const noteId = await page.evaluate(() => window.__AI_LIBRARY__.notesMgr.activeNote.id);
  assert.equal((await get(`/api/notes/${noteId}`)).content, content);
  await page.locator('#note-modal-close').click();
  await page.reload();
  await page.waitForFunction(() => window.__AI_LIBRARY__?.universe.data && window.__AI_LIBRARY__.universe.scene?.debug.ready);
  await openNote(name);
  assert.equal(await page.locator('#note-edit-content').inputValue(), content);
  await record('F01', 'Create note, preserve whitespace, reload persisted note');

  await page.locator('#btn-trigger-ai-analysis').click();
  await page.locator('[data-accept-id]').waitFor();
  await page.locator('[data-accept-id]').click();
  await page.waitForFunction(() => document.querySelector('#analysis-claims-list').textContent.includes('ACCEPTED'));
  const candidates = await get(`/api/candidates?source_id=${noteId}`);
  assert.equal(candidates.length, 1);
  const response = await context.request.post(`${base}/api/candidates/${candidates[0].id}/accept`);
  const memory = (await response.json()).promoted_node;
  assert.equal(memory.maturity, 'L2');
  assert.equal(memory.provenance.content_hash, (await get(`/api/notes/${noteId}`)).content_hash);
  assert.equal((await get(`/api/maintenance/logsheet?operation_id=${candidates[0].id}`)).length, 1);
  await record('F02', 'Review local candidate, accept once, retry without duplicates, atomic audit');

  await page.locator('#note-modal-close').click();
  await page.evaluate(id => window.__AI_LIBRARY__.jumpToMemory(id), memory.id);
  await page.locator('#memory-detail-drawer.open').waitFor();
  await page.waitForFunction(() => document.querySelector('#drawer-node-title').textContent.startsWith(name));
  assert.equal(await page.locator('#drawer-node-content').innerText(), content.split('\n')[0].trim());
  assert.equal(await page.evaluate(() => window.__AI_LIBRARY__.memoryNet.selectedNode.provenance.source_id), noteId);
  assert.match(await page.locator('#drawer-relations-list').innerText(), /DERIVED_FROM/);
  await record('F03', 'Jump to memory, actual content, source hash and persisted graph relation');
  await page.locator('[data-drawertab="source"]').click();
  await page.locator('#drawer-btn-source').click();
  await page.locator('#note-edit-content').waitFor({ state: 'visible' });
  assert.equal(await page.locator('#note-edit-content').inputValue(), content);
  await page.locator('#note-modal-close').click();

  await page.locator('#universe-maintenance').click();
  await page.locator('#sub-btn-logsheet').click();
  await page.locator('#maintenance-search-input').fill(memory.id);
  await page.getByRole('button', { name: 'Details', exact: true }).click();
  await page.locator('#maint-drawer-targets button').click();
  await page.waitForFunction(id => document.querySelector('#drawer-node-title').textContent === id, name);
  await record('F04', 'Audit record target opens the exact memory; source revision opens its note');

  await openNote(name);
  await page.locator('#note-edit-content').fill('未保存草稿');
  page.once('dialog', dialog => dialog.dismiss());
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#note-edit-content').inputValue(), '未保存草稿');
  assert(await page.locator('#note-modal-backdrop').evaluate(el => el.classList.contains('open')));
  await context.request.post(`${base}/api/notes/${noteId}/revisions`, { data: { title: name, content: '另一个窗口保存的版本', expected_revision: 1 } });
  await page.locator('#btn-save-note').click();
  await page.getByText(/保存失败：Note changed to v2/).waitFor();
  assert.equal(await page.locator('#note-edit-content').inputValue(), '未保存草稿');
  assert.equal((await get(`/api/notes/${noteId}`)).revision, 2);
  await record('F05', 'Unsaved-close cancellation and concurrent revision conflict preserve draft');
  page.once('dialog', dialog => dialog.accept());
  await page.locator('#note-modal-close').click();

  await openNote(name);
  await page.locator('#note-edit-content').fill('断线后保留的草稿');
  await page.route('**/api/notes/*/revisions', route => route.abort());
  await page.locator('#btn-save-note').click();
  await page.getByText(/保存失败/).waitFor();
  assert.equal(await page.locator('#note-edit-content').inputValue(), '断线后保留的草稿');
  await record('F06', 'Failed save preserves the editable draft');
  await page.unroute('**/api/notes/*/revisions');
  page.once('dialog', dialog => dialog.accept());
  await page.locator('#note-modal-close').click();

  // 原快速 Tab 切换检查不再适用（宇宙 UI 无工作区面板）；验证顶层入口可用并暂停公转
  for (const id of ['universe-flight-toggle', 'universe-tractor-toggle', 'universe-pause', 'universe-reset']) {
    assert.ok(await page.locator(`#${id}`).isVisible(), `${id} visible`);
  }
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.locator('#universe-pause').click();
  await page.waitForTimeout(250);
  assert.equal(await page.evaluate(() => window.__AI_LIBRARY__.universe.scene.paused), true, 'pause stops scene motion');
  await page.locator('#universe-pause').click();
  await record('F07', 'Universe entry controls visible; pause toggles scene motion');

  await page.setViewportSize({ width: 800, height: 900 });
  await createNote();
  await page.locator('#note-edit-title').fill(`${name} rejected`);
  await page.locator('#note-edit-content').fill('这是待核实的另一条信息。');
  await page.locator('#btn-save-note').click();
  await page.waitForFunction(() => !window.__AI_LIBRARY__.notesMgr.isSaving && window.__AI_LIBRARY__.notesMgr.activeNote?.title.endsWith('rejected'));
  await page.locator('#btn-trigger-ai-analysis').click();
  await page.locator('[data-reject-id]').waitFor();
  page.once('dialog', dialog => dialog.accept('需要更多证据'));
  await page.locator('[data-reject-id]').click();
  await page.waitForFunction(() => document.querySelector('#analysis-claims-list').textContent.includes('REJECTED'));
  assert.equal(await page.locator('[data-accept-id]').count(), 0);
  await record('F08', '800px layout supports candidate rejection; terminal candidate has no accept action');
  await page.locator('#note-modal-close').click();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.locator('#universe-maintenance').click();
  await page.locator('#sub-btn-backlog').click();
  const [queuedResponse] = await Promise.all([
    page.waitForResponse(response => response.url().endsWith('/api/maintenance/backlog') && response.request().method() === 'POST'),
    page.locator('#btn-sweep-stale').click(),
  ]);
  const queued = await queuedResponse.json();
  await page.locator('#maintenance-search-input').fill(queued.id);
  await page.getByRole('button', { name: 'Details', exact: true }).click();
  page.once('dialog', dialog => dialog.accept('QA fixture: verified the test source revision and hash in this isolated run.'));
  await page.locator('#maint-act-resolve').click();
  await page.getByText('Backlog item resolved and logged in Logsheet.').waitFor();
  const logs = await get(`/api/maintenance/logsheet?backlog_id=${queued.id}`);
  assert.deepEqual(logs.map(entry => entry.event_type), ['ITEM_RESOLVED', 'ITEM_CREATED']);
  await page.locator('#sub-btn-logsheet').click();
  await page.locator('#maintenance-search-input').fill(queued.id);
  await record('F09', 'Queue a review, resolve with evidence note, record exactly two backend audit events');
  await openNote(name);
  let releaseRevision;
  const revisionGate = new Promise(resolve => { releaseRevision = resolve; });
  const revisionUrl = `**/api/notes/${noteId}?revision=1`;
  await page.route(revisionUrl, async route => { await revisionGate; await route.continue(); });
  await page.locator('#note-revision-select').selectOption('1');
  await page.locator('#note-modal-close').click();
  await createNote();
  await page.locator('#note-edit-title').fill('New draft during slow history load');
  await page.locator('#note-edit-content').fill('Do not overwrite this new draft.');
  releaseRevision();
  await page.waitForTimeout(300);
  assert.equal(await page.locator('#note-edit-title').inputValue(), 'New draft during slow history load');
  assert.equal(await page.locator('#note-edit-content').inputValue(), 'Do not overwrite this new draft.');
  await page.unroute(revisionUrl);
  await record('F10', 'A late historical-revision response cannot overwrite a newly opened draft');
  page.once('dialog', dialog => dialog.accept());
  await page.locator('#note-modal-close').click();
  await openNote(name);
  let releaseExtraction;
  const extractionGate = new Promise(resolve => { releaseExtraction = resolve; });
  const extractionUrl = `**/api/notes/${noteId}?revision=2`;
  await page.route(extractionUrl, async route => { await extractionGate; await route.continue(); });
  await page.locator('#btn-trigger-ai-analysis').click();
  await page.locator('#note-modal-close').click();
  await createNote();
  await page.locator('#note-edit-title').fill(`${name} separate source`);
  await page.locator('#note-edit-content').fill('This source must not receive a claim from another note.');
  await page.locator('#btn-save-note').click();
  await page.waitForFunction(() => !window.__AI_LIBRARY__.notesMgr.isSaving && window.__AI_LIBRARY__.notesMgr.activeNote?.title.endsWith('separate source'));
  const otherNoteId = await page.evaluate(() => window.__AI_LIBRARY__.notesMgr.activeNote.id);
  releaseExtraction();
  await page.waitForFunction(() => !document.querySelector('#btn-trigger-ai-analysis').disabled);
  assert.equal((await get(`/api/candidates?source_id=${otherNoteId}`)).length, 0);
  assert.equal(await page.locator('#note-edit-content').inputValue(), 'This source must not receive a claim from another note.');
  await page.unroute(extractionUrl);
  await record('F11', 'A late candidate extraction cannot attribute source A content to source B');
  assert.deepEqual(errors, []);
  fs.writeFileSync(path.join(evidence, 'created-data.json'), JSON.stringify({ name, noteId, memoryId: memory.id, candidateId: candidates[0].id, location: '.agent-tmp/qa-data', purpose: 'isolated QA only' }, null, 2));
  await browser.close();
}
main().catch(async error => {
  fs.writeFileSync(path.join(evidence, 'failure.txt'), error.stack || String(error));
  console.error(error);
  await browser?.close();
  process.exitCode = 1;
});
