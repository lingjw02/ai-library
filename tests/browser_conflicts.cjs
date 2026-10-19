// Real browser review against an isolated test server only.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = 'http://127.0.0.1:8766';
const evidence = '.agent-tmp/qa-conflicts';
fs.mkdirSync(evidence, { recursive: true });
let browser;
(async () => {
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const post = async (url, data) => {
    const response = await context.request.post(base + url, { data });
    assert(response.ok(), await response.text());
    return response.json();
  };
  const title = 'Conflict QA ' + Date.now();
  const node = await post('/api/memory/nodes', { id: title, node_type: 'Decision', title,
    content: 'Keep SQLite', authority: 'EXPLICIT' });
  const source = await post('/api/notes', { title: title + ' proposal', content: 'Switch to PostgreSQL' });
  const candidate = await post('/api/candidates', { source_id: source.id, source_revision: 1,
    claim: source.content, suggested_node_type: 'Fact', payload: { content_hash: source.content_hash } });
  const report = await post('/api/conflicts', { memory_targets: { [title]: 1 }, candidate_ids: [candidate.id],
    kind: 'SEMANTIC_CONFLICT', description: title + ' <b>unverified report</b>' });
  const reviewNote = await post('/api/notes', { title: title + ' review', content: 'Owner confirms SQLite remains the decision.' });
  await page.goto(base);
  await page.waitForFunction(() => window.__AI_LIBRARY__?.universe.data);
  // 知识宇宙 UI：直接打开目标记忆抽屉（不再使用旧记忆图搜索列表）
  await page.evaluate(id => window.__AI_LIBRARY__.jumpToMemory(id), node.node_id);
  await page.locator('#memory-detail-drawer.open').waitFor();
  await page.locator('[data-conflict-open]').filter({ hasText: title }).click();
  await page.locator('#conflict-status').waitFor();
  assert((await page.locator('#memory-conflicts').innerText()).includes('<b>unverified report</b>'));
  assert.equal(await page.locator('#memory-conflicts b').count(), 0);
  await page.locator('#conflict-status').selectOption('RESOLVED');
  await page.locator('#conflict-reason').fill('Original decision remains valid');
  await page.locator('#conflict-method').fill('Compared proposal with owner clarification');
  await page.locator('#conflict-save').click();
  await page.getByText('Choose a source note before closing this conflict.', { exact: true }).waitFor();
  await page.locator('#conflict-evidence').selectOption(reviewNote.id);
  await page.screenshot({ path: evidence + '/review-form.png', fullPage: true });
  await page.locator('#conflict-save').click();
  await page.getByText('Review saved. Existing claims were preserved.', { exact: true }).waitFor();
  await page.getByText('Last recorded review', { exact: true }).click();
  await page.getByText('Original decision remains valid', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Read review evidence · revision 1', exact: true }).waitFor();
  const saved = await (await context.request.get(base + '/api/conflicts/' + report.id)).json();
  assert.equal(saved.core_props.status, 'RESOLVED');
  assert.equal(saved.verification_props.resolution.evidence_refs[0].content_hash, reviewNote.content_hash);
  assert.equal((await (await context.request.get(base + '/api/memory/nodes/' + encodeURIComponent(title))).json()).version, 1);
  await page.locator('#conflict-status').selectOption('OPEN');
  await page.locator('#conflict-reason').fill('Reopen for another evidence review');
  // Another reviewer changes this report while the form remains open.
  await post('/api/conflicts/' + report.id + '/transition', { expected_revision: saved.version,
    status: 'OPEN', reason: 'Concurrent owner review' });
  await page.locator('#conflict-save').click();
  await page.getByText(/Conflict revision changed/).waitFor();
  assert.equal(await page.locator('#conflict-reason').inputValue(), 'Reopen for another evidence review');
  await page.locator('#drawer-btn-close').click();
  await page.setViewportSize({ width: 800, height: 900 });
  await page.evaluate(id => window.__AI_LIBRARY__.jumpToMemory(id), node.node_id);
  await page.locator('#memory-detail-drawer.open').waitFor();
  await page.locator('[data-conflict-open]').filter({ hasText: title }).click();
  await page.locator('#conflict-status').waitFor();
  assert.equal(await page.locator('#conflict-status').inputValue(), 'OPEN');
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: evidence + '/review-800px.png', fullPage: true });
  await page.locator('#drawer-btn-close').click();
  await page.evaluate(id => window.__AI_LIBRARY__.jumpToMemory(id), node.node_id);
  await page.locator('#memory-detail-drawer.open').waitFor();
  let release;
  const held = new Promise(resolve => { release = resolve; });
  let received;
  const requested = new Promise(resolve => { received = resolve; });
  await page.route('**/api/conflicts/' + report.id + '?*', async route => {
    const response = await route.fetch();
    received();
    await held;
    await route.fulfill({ response });
  });
  await page.locator('[data-conflict-open]').filter({ hasText: title }).click();
  await requested;
  await page.evaluate(() => window.__AI_LIBRARY__.universe.setScope('private-scope'));
  release();
  await page.waitForTimeout(400);
  assert.equal(await page.locator('#memory-conflicts').innerText(), '');
  assert.equal(await page.locator('#conflict-status').count(), 0);
  assert.deepEqual(errors, []);
  fs.writeFileSync(evidence + '/results.json', JSON.stringify({ state: 'QA_PASSED', browser: browser.version(),
    checks: ['literal untrusted text', 'evidence required', 'review persisted without changing claim',
      'stale review preserves draft', '800px layout', 'late report cannot cross scope switch'], errors }, null, 2));
  console.log('PASS conflict review: 6 browser checks, 0 page errors');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => { await browser?.close(); });
