// Use the isolated loopback QA service. Never point this at daily user data.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const assert = require('node:assert/strict');
const fs = require('node:fs');
let browser;
const base = 'http://127.0.0.1:8766';
const evidence = '.agent-tmp/qa-quarantine';
fs.mkdirSync(evidence, { recursive: true });
(async () => {
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const post = async (endpoint, data) => {
    const response = await context.request.post(base + endpoint, { data });
    assert(response.ok(), await response.text());
    return response.json();
  };
  const title = 'Repair QA ' + Date.now();
  const source = await post('/api/notes', { title, content: 'Original source is preserved.' });
  const bad = await post('/api/candidates', { source_id: source.id, source_revision: 1,
    claim: title, suggested_node_type: 'Fact', payload: { confidence: 'broken', title: '<b>original</b>' } });
  assert.equal(bad.success, false);
  await page.goto(base);
  await page.waitForFunction(() => window.__AI_LIBRARY__?.universe.data);
  await page.locator('#universe-maintenance').click();
  await page.locator('#sub-btn-quarantine').click();
  await page.locator(`[data-quarantine-id="${bad.id}"]`).click();
  await page.locator('#quarantine-claim').waitFor();
  const original = await page.locator('#maint-drawer-json').textContent();
  assert(original.includes('broken'));
  await page.locator('#quarantine-reason').fill('Correct malformed numeric attribute');
  await page.locator('#quarantine-save').click();
  await page.getByText(/Repair still fails validation/).waitFor();
  assert.equal(await page.locator('#quarantine-reason').inputValue(), 'Correct malformed numeric attribute');
  await page.locator('#quarantine-attributes').fill('{"confidence": 0.5, "title": "Repaired claim"}');
  await page.screenshot({ path: evidence + '/repair-form.png', fullPage: true });
  await page.locator('#quarantine-save').click();
  await page.getByText('Repair saved to a candidate. This repair did not accept or verify it.', { exact: true }).waitFor();
  const repaired = await (await context.request.get(base + '/api/quarantine/' + bad.id)).json();
  assert(repaired.resolved);
  assert.equal(repaired.payload.confidence, 'broken');
  assert.equal(repaired.submission.payload.confidence, 'broken');
  const candidates = await (await context.request.get(base + '/api/candidates?source_id=' + source.id)).json();
  assert.equal(candidates[0].status, 'VALIDATED');
  assert.equal(await page.locator('#quarantine-save').count(), 0);
  await page.locator('#quarantine-open-source').click();
  await page.locator('#note-edit-content').waitFor({ state: 'visible' });
  assert.equal(await page.locator('#note-edit-content').inputValue(), source.content);
  await page.locator('#note-modal-close').click();
  await page.locator('#universe-maintenance').click();
  const race = await post('/api/candidates', { source_id: source.id, source_revision: 1,
    claim: title + ' concurrent', suggested_node_type: 'Fact', payload: { confidence: 'broken' } });
  await page.getByRole('button', { name: 'Refresh records', exact: true }).click();
  await page.locator(`[data-quarantine-id="${race.id}"]`).click();
  await page.locator('#quarantine-reason').fill('My pending repair');
  await page.locator('#quarantine-attributes').fill('{}');
  await post('/api/quarantine/' + race.id + '/repair', { expected_revision: 1, claim: 'Concurrent repair',
    suggested_node_type: 'Fact', payload: {}, reason: 'A different reviewer repaired this record' });
  await page.locator('#quarantine-save').click();
  await page.getByText(/Quarantine revision changed/).waitFor();
  assert.equal(await page.locator('#quarantine-reason').inputValue(), 'My pending repair');
  await page.locator('#maint-drawer-btn-close').click();
  await page.locator('#quarantine-view').selectOption('resolved');
  await page.locator(`[data-quarantine-id="${bad.id}"]`).waitFor();
  let release;
  const held = new Promise(resolve => { release = resolve; });
  let received;
  const requested = new Promise(resolve => { received = resolve; });
  await page.route('**/api/quarantine/' + bad.id + '?*', async route => {
    const response = await route.fetch();
    received();
    await held;
    await route.fulfill({ response });
  });
  await page.locator(`[data-quarantine-id="${bad.id}"]`).click();
  await requested;
  await page.locator('#quarantine-scope').fill('private-scope');
  await page.locator('#quarantine-scope').press('Tab');
  release();
  await page.waitForTimeout(300);
  assert.equal(await page.locator('#quarantine-editor').textContent(), '');
  assert.equal(await page.locator('#maintenance-detail-drawer').evaluate(node => node.classList.contains('open')), false);
  await page.locator('#quarantine-view').selectOption('legacy');
  await page.getByText(/Legacy records have no verified scope or source revision/).waitFor();
  assert.equal(await page.locator('#quarantine-scope').isDisabled(), true);
  await page.locator('#quarantine-view').selectOption('open');
  await page.locator('#quarantine-scope').fill('private-scope');
  await page.locator('#quarantine-scope').press('Tab');
  await page.getByText('No records in this scope and state.', { exact: true }).waitFor();
  await page.setViewportSize({ width: 800, height: 900 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: evidence + '/repair-800px.png', fullPage: true });
  assert.deepEqual(errors, []);
  fs.writeFileSync(evidence + '/results.json', JSON.stringify({ state: 'QA_PASSED', browser: browser.version(),
    checks: ['failed repair retains draft', 'original input preserved', 'repair returns unaccepted candidate',
      'source navigation', 'recovered list and legacy explanation', 'scope filter and 800px layout',
      'concurrent repair preserves draft', 'late record cannot cross scope switch'], errors }, null, 2));
  console.log('PASS quarantine UI: 8 browser checks, 0 page errors');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => { await browser?.close(); });
