const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'D:/02_Development_Tools/live2d-maker/node_modules/playwright-core');
const assert = require('node:assert/strict');
const fs = require('node:fs');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const report = { checks: [], errors: [], writes: [] };

  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    page.on('pageerror', e => report.errors.push(e.message));
    page.on('request', r => {
      if (r.url().includes('/api/') && r.method() !== 'GET') {
        report.writes.push({ method: r.method(), url: r.url() });
      }
    });

    // 1. Initial Page Load
    await page.goto('http://127.0.0.1:8000/');
    await page.waitForFunction(() => window.__AI_LIBRARY__?.universe.data?.topics.length && window.__AI_LIBRARY__.universe.scene?.debug.ready);
    
    assert.ok(await page.evaluate(() => window.__AI_LIBRARY__.universe.perspective), 'Perspective state initialized in the universe shell');
    assert.equal(await page.locator('#universe-tractor-toggle[aria-pressed="true"]').count(), 1, 'Tractor lines toggle active by default');
    
    report.checks.push('1. Initial topic perspective and tractor lines active');

    // 2. Tractor Lines Scene Verification and Toggle
    const tractorState = await page.evaluate(() => {
      const u = window.__AI_LIBRARY__.universe;
      return {
        hasGroup: Boolean(u.scene?.tractorGroup),
        linesCount: u.scene?.tractorGroup?.children.length || 0,
        visible: u.scene?.tractorGroup?.visible
      };
    });
    console.log('Tractor scene status:', tractorState);
    assert.ok(tractorState.hasGroup, 'Tractor group must exist in scene');
    assert.ok(tractorState.linesCount > 0, 'Tractor lines must be rendered');
    assert.equal(tractorState.visible, true, 'Tractor lines must be visible');

    // Toggle off
    await page.locator('#universe-tractor-toggle').click();
    assert.equal(await page.locator('#universe-tractor-toggle[aria-pressed="false"]').count(), 1);
    assert.equal(await page.evaluate(() => window.__AI_LIBRARY__.universe.scene.tractorGroup.visible), false);

    // Toggle back on
    await page.locator('#universe-tractor-toggle').click();
    assert.equal(await page.locator('#universe-tractor-toggle[aria-pressed="true"]').count(), 1);
    assert.equal(await page.evaluate(() => window.__AI_LIBRARY__.universe.scene.tractorGroup.visible), true);

    report.checks.push('2. Tractor lines toggle on/off verified');

    // 3. 直接通过目录进入真实主题分区（旧版 agent/topic 视角切换按钮已从当前 UI 移除）
    await page.locator('#universe-directory-toggle').click();
    await page.locator('.universe-entry-main strong').filter({ hasText: /盘古|Pangu/i }).first().click();
    await page.waitForFunction(()=>{const u=window.__AI_LIBRARY__.universe;return u.topicId?.startsWith('topic-')&&u.data?.topic?.id===u.topicId&&/盘古|Pangu/i.test(u.data.topic?.title||'');});
    const entered = await page.evaluate(() => {
      const u = window.__AI_LIBRARY__.universe;
      return { title: document.getElementById('universe-title').textContent, breadcrumbs: u.data.breadcrumbs?.map(b => b.title) };
    });
    console.log('Inside Pangu topic:', entered);
    assert.ok(entered.title.includes('盘古') || entered.title.includes('Pangu'));
    assert.ok(entered.breadcrumbs.length >= 1, 'Breadcrumbs must contain the entered topic');

    // 退出回总览
    await page.locator('#universe-exit').click();
    await page.waitForFunction(() => window.__AI_LIBRARY__.universe.topicId === null && window.__AI_LIBRARY__.universe.data?.topic === null);
    report.checks.push('3. Entered real topic partition and returned cleanly');

    // 4. Cluster Suggestions (> 5 items) —— 再次进入 Pangu 主题触发聚类
    // Open directory and enter Pangu topic
    await page.locator('#universe-directory-toggle').click();
    await page.locator('.universe-entry-main strong').filter({ hasText: /盘古|Pangu/i }).first().click();
    await page.waitForFunction(() => window.__AI_LIBRARY__.universe.topicId?.startsWith('topic-'));

    // Wait for cluster suggestions detection (suggestions assigned and banner rendered)
    await page.waitForFunction(() => window.__AI_LIBRARY__.universe.clusterSuggestions?.length > 0 && !document.getElementById('universe-cluster-banner')?.hidden, null, { timeout: 10000 });

    const clusterData = await page.evaluate(() => {
      const u = window.__AI_LIBRARY__.universe;
      const banner = document.getElementById('universe-cluster-banner');
      const msg = document.getElementById('universe-cluster-msg').textContent;
      return {
        bannerVisible: !banner.hidden,
        bannerMessage: msg,
        suggestionsCount: u.clusterSuggestions.length,
        topCluster: u.clusterSuggestions[0]
      };
    });
    console.log('Cluster detection inside topic:', clusterData);
    assert.ok(clusterData.bannerVisible, 'Cluster suggestion banner must be visible');
    assert.ok(clusterData.suggestionsCount > 0, 'Must have cluster suggestions for > 5 items');
    assert.ok(clusterData.topCluster.count > 5, 'Cluster count must be > 5');
    assert.ok(clusterData.bannerMessage.includes('> 5'), 'Banner must indicate > 5 items threshold');

    // Open Cluster Dialog
    await page.locator('#universe-cluster-open').click();
    assert.ok(await page.locator('#universe-cluster-dialog').evaluate(d => d.open), 'Cluster dialog must open');
    const cardsCount = await page.locator('.universe-cluster-card').count();
    assert.ok(cardsCount >= clusterData.suggestionsCount, 'Must render cluster cards in dialog');

    // Close Dialog
    await page.locator('#universe-cluster-close').click();
    assert.ok(await page.locator('#universe-cluster-dialog').evaluate(d => !d.open), 'Cluster dialog must close');

    // Dismiss Banner
    await page.locator('#universe-cluster-dismiss').click();
    assert.equal(await page.locator('#universe-cluster-banner').evaluate(b => b.hidden), true, 'Banner hidden on dismiss');

    // Exit back to Topic Overview
    await page.locator('#universe-exit').click();
    await page.waitForFunction(() => window.__AI_LIBRARY__.universe.topicId === null && window.__AI_LIBRARY__.universe.data?.topic === null);
    assert.equal(await page.locator('#universe-title').innerText(), '知识宇宙');

    report.checks.push('5. Cluster suggestions (>5 items) banner and dialog verified');

    // 6. Assertions for zero errors and zero writes
    assert.deepEqual(report.errors, [], `Unexpected page errors: ${JSON.stringify(report.errors)}`);
    assert.deepEqual(report.writes, [], `Browsing must be read-only without unauthorized writes: ${JSON.stringify(report.writes)}`);

    report.state = 'PASS';
    fs.mkdirSync('.agent-tmp/acceptance-20261012', { recursive: true });
    fs.writeFileSync('.agent-tmp/acceptance-20261012/agent_perspective_and_clustering_report.json', JSON.stringify(report, null, 2));
    console.log('ALL PERSPECTIVE & CLUSTERING TESTS PASSED!');
    console.log(JSON.stringify(report, null, 2));
  } finally {
    await browser.close();
  }
})().catch(e => {
  console.error('Test failed:', e);
  process.exitCode = 1;
});
