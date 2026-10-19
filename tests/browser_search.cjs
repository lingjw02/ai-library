// Isolated QA only. Existing Playwright installation required.
// 维修说明（2026-10-13）：适配知识宇宙 UI——旧 notes/memory 搜索面板已移除，
// 搜索走 #universe-search（/api/universe?query=），scope 用 universe.setScope/#universe-scope，
// 记忆跳转用 jumpToMemory；测试意图（旧数据可搜、scope 隔离、延迟响应竞态）保持不变。
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.SEARCH_TEST_URL || 'http://127.0.0.1:8768';
const evidence = path.resolve('.agent-tmp/qa-phase2-evidence');
fs.mkdirSync(evidence, { recursive: true });
let browser;
async function main() {
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const errors = [], results = [];
  page.on('pageerror', error => errors.push(error.message));
  const post = async (endpoint, json) => {
    const r = await context.request.post(base + endpoint, { data: json });
    assert(r.ok(), await r.text());
    return r.json();
  };
  const record = async (id, description) => {
    await page.screenshot({ path: path.join(evidence, `${id}.png`), fullPage: true });
    results.push({ id, description, state: 'PASSED' });
    console.log(`PASS ${id}: ${description}`);
  };
  const setScope = async scope => {
    await page.evaluate(s => window.__AI_LIBRARY__.universe.setScope(s), scope);
    await page.waitForFunction(s => window.__AI_LIBRARY__.universe.scope === s && window.__AI_LIBRARY__.universe.data, scope);
  };
  const search = async keyword => {
    await page.locator('#universe-search').fill(keyword);
    await page.locator('#universe-search-form').evaluate(f => f.requestSubmit());
  };
  const resultRows = () => page.locator('.universe-entry[data-kind="note"] .universe-entry-main, .universe-entry[data-kind="memory"] .universe-entry-main');
  const scope = `search-qa-${Date.now()}`;
  const old = await post('/api/notes', { title: 'Old source', content: 'rarekey 原文依据', scope });
  const memoryId = `${scope}-old`;
  await post('/api/memory/nodes', { id: memoryId, node_type: 'Fact', title: 'Old memory', content: 'rarekey 远处记忆', scope });
  for (let i = 0; i < 125; ++i) {
    await post('/api/memory/nodes', { id: `${scope}-${i}`, node_type: 'Fact', title: `Filler ${i}`, content: 'unrelated', scope });
    if (i < 105) await post('/api/notes', { title: `Filler ${i}`, content: 'unrelated', scope });
  }
  await page.goto(base);
  await page.waitForFunction(() => window.__AI_LIBRARY__?.universe.data && window.__AI_LIBRARY__.universe.scene?.debug.ready);
  await setScope(scope);
  await search('rarekey');
  await page.locator('.universe-entry[data-kind="note"] .universe-entry-main').filter({ hasText: 'Old source' }).first().click();
  await page.locator('#note-modal-backdrop.open').waitFor();
  assert.equal(await page.locator('#note-edit-content').inputValue(), 'rarekey 原文依据');
  await record('S01', 'Backend search finds a source older than the 100-note loaded window');
  await page.locator('#note-modal-close').click();
  await page.evaluate(id => window.__AI_LIBRARY__.jumpToMemory(id), memoryId);
  await page.locator('#memory-detail-drawer.open').waitFor();
  await page.waitForFunction(() => document.querySelector('#drawer-node-content').textContent === 'rarekey 远处记忆');
  await record('S02', 'Content keyword finds memory beyond 120 graph nodes and opens actual detail');
  await setScope(scope + '-empty');
  await page.waitForFunction(() => !document.querySelector('#memory-detail-drawer').classList.contains('open'));
  await search('rarekey');
  await page.waitForTimeout(700);
  assert.equal(await resultRows().count(), 0);
  await record('S03', 'Scope switch clears the old selection and returns no foreign-scope search results');

  // 记忆跳转跨 scope：目标节点所在 scope 自动成为当前 scope 并打开抽屉
  // （旧版的"延迟节点查找竞态防护"已由宇宙数据加载防护承担，见 S05）
  await setScope(scope + '-empty');
  await page.evaluate(id => { void window.__AI_LIBRARY__.jumpToMemory(id); }, memoryId);
  await page.waitForFunction(s => window.__AI_LIBRARY__.universe.scope === s && document.querySelector('#memory-detail-drawer')?.classList.contains('open'), scope);
  assert.equal(await page.locator('#drawer-node-content').innerText(), 'rarekey 远处记忆');
  await page.locator('#drawer-btn-close').click();
  await record('S04', 'Memory jump across scope synchronizes the active scope and opens the drawer');

  // Delay one universe load; newer empty-scope results must win.
  let releaseSearch, searchObserved;
  const searchGate = new Promise(resolve => { releaseSearch = resolve; });
  const searchRequested = new Promise(resolve => { searchObserved = resolve; });
  await page.route('**/api/universe?*', async route => {
    const q = new URL(route.request().url()).searchParams;
    const response = await route.fetch();
    if (q.get('scope') === scope && q.get('query') === 'rarekey') {
      searchObserved(); await searchGate;
    }
    await route.fulfill({ response });
  });
  await setScope(scope);
  await search('rarekey');
  await searchRequested;
  await setScope(scope + '-empty');
  await page.waitForTimeout(300);
  releaseSearch();
  await page.waitForTimeout(600);
  assert.equal(await page.evaluate(() => window.__AI_LIBRARY__.universe.scope), scope + '-empty');
  assert.equal(await resultRows().count(), 0);
  await record('S05', 'Late universe load cannot overwrite newer-scope results');
  await page.unroute('**/api/universe?*');

  await setScope(scope);
  await page.locator('#universe-add').click();
  await page.locator('#material-new-note').click();
  await page.locator('#note-edit-title').fill('Scoped draft');
  await page.locator('#note-edit-content').fill('Created under selected scope');
  await page.locator('#btn-save-note').click();
  await page.waitForFunction(() => window.__AI_LIBRARY__.notesMgr.activeNote?.title === 'Scoped draft' && !window.__AI_LIBRARY__.notesMgr.isSaving);
  assert.equal(await page.evaluate(() => window.__AI_LIBRARY__.notesMgr.activeNote.scope), scope);
  await record('S06', 'New note uses the selected scope');
  await page.locator('#note-modal-close').click();
  await page.setViewportSize({ width: 800, height: 900 });
  await record('S07', 'Scoped search controls render in the existing narrow layout');
  assert.deepEqual(errors, []);
  fs.writeFileSync(path.join(evidence, 'results.json'), JSON.stringify({ results, errors, browser: browser.version(), scope, oldNoteId: old.id }, null, 2));
  await browser.close();
}
main().catch(async error => {
  fs.writeFileSync(path.join(evidence, 'failure.txt'), error.stack || String(error));
  console.error(error);
  await browser?.close();
  process.exitCode = 1;
});
