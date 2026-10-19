// Requires an isolated running server: this test publishes rule versions.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'D:/02_Development_Tools/live2d-maker/node_modules/playwright-core');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.USAGE_RULES_QA_URL;
if (!base || new URL(base).port === '8000') throw new Error('Set USAGE_RULES_QA_URL to an isolated service, never daily port 8000.');
const output = process.env.USAGE_RULES_QA_OUTPUT || '.agent-tmp/mcp-usage-rules-20261012/browser';
fs.mkdirSync(output, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true });
  const report = { checks: [], pageErrors: [], screenshots: [] };
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: width === 1440 ? 1000 : 844 }, reducedMotion: 'reduce' });
      page.on('pageerror', error => report.pageErrors.push(error.message));
      await page.goto(base);
      await page.locator('#universe-settings').click();
      await page.locator('#settings-dialog[open]').waitFor();
      await page.locator('[data-settings-tab="rules"]').click();
      await page.locator('#usage-rule-read_on_start').waitFor();
      await page.locator('summary').filter({ hasText: /^主题与摘要$/ }).click();
      const threshold = page.locator('#usage-rule-subtopic_min_items');
      const before = await (await page.request.get(`${base}/api/settings/usage-rules`)).json();
      await threshold.fill(String(before.rules.subtopic_min_items + 1));
      assert.equal(await page.locator('#settings-rules-save').isEnabled(), true);
      assert.equal((await (await page.request.get(`${base}/api/settings/usage-rules`)).json()).revision, before.revision, 'Editing must not auto-publish');
      const response = page.waitForResponse(r => r.url().endsWith('/api/settings/usage-rules') && r.request().method() === 'POST');
      await page.locator('#settings-rules-save').click();
      assert.equal((await response).status(), 200);
      await page.waitForFunction(() => document.getElementById('settings-rules-status').textContent.startsWith('已保存并发布'));
      const published = await (await page.request.get(`${base}/api/settings/usage-rules`)).json();
      assert.equal(published.revision, before.revision + 1);
      assert.equal(published.rules.subtopic_min_items, before.rules.subtopic_min_items + 1);
      assert.ok(published.suggestions.every(s => !s.enabled));

      // Reopen proves persistence and that close restored focus to settings.
      await page.locator('#settings-close').click();
      assert.equal(await page.locator('#universe-settings').evaluate(e => document.activeElement === e), true);
      await page.locator('#universe-settings').click();
      await page.waitForFunction(revision => document.getElementById('settings-rules-version').textContent.startsWith(`v${revision} `), published.revision);
      await page.locator('summary').filter({ hasText: /^主题与摘要$/ }).click();
      assert.equal(await threshold.inputValue(), String(published.rules.subtopic_min_items));
      report.checks.push(`${width}: settings button, grouped editor, explicit save, persistence, focus, optional rules disabled`);
      if (width === 1440) {
        await threshold.fill('15');
        const external = await page.request.post(`${base}/api/settings/usage-rules`, { data: {
          expected_revision: published.revision,
          rules: { ...published.rules, subtopic_min_items: 16 },
        } });
        assert.equal(external.status(), 200);
        const staleResponse = page.waitForResponse(r => r.url().endsWith('/api/settings/usage-rules') && r.request().method() === 'POST');
        await page.locator('#settings-rules-save').click();
        assert.equal((await staleResponse).status(), 409);
        await page.waitForFunction(() => document.getElementById('settings-rules-status').textContent.startsWith('更新失败'));
        assert.equal(await threshold.inputValue(), '15', 'Conflict must preserve draft');
        assert.equal((await (await page.request.get(`${base}/api/settings/usage-rules`)).json()).rules.subtopic_min_items, 16);
        page.once('dialog', dialog => dialog.dismiss());
        await page.locator('#settings-close').click();
        assert.equal(await page.locator('#settings-dialog').evaluate(e => e.open), true, 'Reject discard keeps editor open');
        page.once('dialog', dialog => dialog.accept());
        await page.locator('#settings-rules-refresh').click();
        await page.waitForFunction(() => document.getElementById('settings-rules-status').textContent.startsWith('当前发布版本'));
        await page.locator('summary').filter({ hasText: /^主题与摘要$/ }).click();
        assert.equal(await threshold.inputValue(), '16');
        await threshold.fill('17');
        await page.route('**/api/settings/usage-rules', route => route.request().method() === 'POST'
          ? route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '测试网络失败' }) })
          : route.continue());
        await page.locator('#settings-rules-save').click();
        await page.waitForFunction(() => document.getElementById('settings-rules-status').textContent.includes('测试网络失败'));
        assert.equal(await threshold.inputValue(), '17');
        assert.equal(await page.locator('#settings-rules-save').isEnabled(), true);
        assert.equal((await (await page.request.get(`${base}/api/settings/usage-rules`)).json()).rules.subtopic_min_items, 16);
        await page.unroute('**/api/settings/usage-rules');
        report.checks.push('Stale revision, rejected discard and network failure preserve drafts without overwriting published rules');
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.equal(await page.locator('#settings-pane-rules').evaluate(e => e.scrollWidth <= e.clientWidth + 1), true);
      const screenshot = path.join(output, `settings-rules-${width}.png`);
      await page.screenshot({ path: screenshot });
      report.screenshots.push(screenshot);
      await page.close();
    }
    const pendingRead = await browser.newPage({ viewport: { width: 800, height: 900 }, reducedMotion: 'reduce' });
    pendingRead.on('pageerror', error => report.pageErrors.push(error.message));
    await pendingRead.route('**/api/settings/usage-rules', () => {});
    await pendingRead.goto(base);
    await pendingRead.locator('#universe-settings').click();
    await pendingRead.locator('#settings-dialog[open]').waitFor();
    await pendingRead.locator('#settings-close').click();
    assert.equal(await pendingRead.locator('#settings-dialog').evaluate(e => e.open), false, 'A pending rule GET must not trap the settings dialog');
    await pendingRead.close();
    report.checks.push('A stalled rule read still allows closing settings');
    assert.deepEqual(report.pageErrors, []);
    report.state = 'PASS';
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
