const { chromium } = require('D:/02_Development_Tools/live2d-maker/node_modules/playwright-core');
const assert = require('node:assert/strict');
const path = require('node:path');

const ARTIFACT_DIR = 'C:/Users/ling5/.gemini/antigravity-cli/brain/a7ac2f73-7c0e-418b-9b1e-0eddd175035c';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const errors = [];

  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    page.on('pageerror', e => errors.push(e.message));

    console.log('[UI Enhancements Test] Opening http://127.0.0.1:8000/ ...');
    await page.goto('http://127.0.0.1:8000/');

    await page.waitForFunction(() => {
      const u = window.__AI_LIBRARY__?.universe;
      return u && u.scene && u.scene.ready && u.scene.bodies.length > 0;
    }, {}, { timeout: 30000 });

    console.log('[UI Enhancements Test] 1. Verifying Item 2-3: Neural Legend Tab Bar...');
    const tabTopics = page.locator('#legend-tab-topics');
    const tabMecha = page.locator('#legend-tab-mecha');
    const paneTopics = page.locator('#legend-pane-topics');
    const paneMecha = page.locator('#legend-pane-mecha');

    assert.ok(await tabTopics.isVisible(), 'Topics tab button must be visible');
    assert.ok(await tabMecha.isVisible(), 'Mecha tab button must be visible');
    assert.ok(await paneTopics.isVisible(), 'Topics pane should be active by default');
    assert.equal(await paneMecha.isVisible(), false, 'Mecha pane should be hidden initially');

    // Switch to Mecha Tab
    console.log('[UI Enhancements Test] Switching to Mecha Tab...');
    await tabMecha.click();
    await page.waitForTimeout(200);
    assert.equal(await paneTopics.isVisible(), false, 'Topics pane should be hidden');
    assert.ok(await paneMecha.isVisible(), 'Mecha pane should be visible');
    assert.ok(await page.locator('#spider-mecha-panel').isVisible(), 'Mecha panel inside pane must be visible');

    // Capture Neural Legend Tabs screenshot
    const legendScreenshot = path.join(ARTIFACT_DIR, 'neural-legend-mecha-tab.png');
    await page.screenshot({ path: legendScreenshot });
    console.log('[UI Enhancements Test] Saved:', legendScreenshot);

    // Switch back to Topics Tab
    await tabTopics.click();
    await page.waitForTimeout(200);
    assert.ok(await paneTopics.isVisible(), 'Topics pane should be visible again');

    // Test Legend Collapse Toggle
    const collapseToggle = page.locator('#neural-legend-toggle');
    assert.ok(await collapseToggle.isVisible(), 'Collapse toggle button must be visible');
    await collapseToggle.click();
    await page.waitForTimeout(200);
    assert.equal(await page.evaluate(() => document.getElementById('neural-legend').getAttribute('data-collapsed')), 'true');
    await collapseToggle.click();
    await page.waitForTimeout(200);
    assert.equal(await page.evaluate(() => document.getElementById('neural-legend').hasAttribute('data-collapsed')), false);

    console.log('[UI Enhancements Test] 2. Verifying Item 2-2: Mutex on < 1366px and Canvas Dismissal...');
    // Set viewport to 1200px (< 1366px)
    await page.setViewportSize({ width: 1200, height: 800 });
    await page.waitForTimeout(200);

    // Open left directory
    const dirToggle = page.locator('#universe-directory-toggle');
    await dirToggle.click();
    await page.waitForTimeout(200);
    assert.ok(await page.locator('#universe-directory').isVisible(), 'Directory should be open');
    // On < 1366px, opening directory must have collapsed right legend
    const legendCollapsedAfterDir = await page.evaluate(() => document.getElementById('neural-legend').getAttribute('data-collapsed'));
    assert.equal(legendCollapsedAfterDir, 'true', 'Right legend should auto-collapse when directory opens on <1366px');

    // Clicking blank canvas dismisses both
    console.log('[UI Enhancements Test] Clicking 3D canvas blank space...');
    const stage = page.locator('#universe-stage canvas');
    await stage.click({ position: { x: 50, y: 750 } });
    await page.waitForTimeout(300);
    assert.equal(await page.locator('#universe-directory').isVisible(), false, 'Directory should close on canvas click');
    assert.equal(await page.evaluate(() => document.getElementById('neural-legend').getAttribute('data-collapsed')), 'true', 'Right sidebar should remain collapsed on canvas click');

    // Restore viewport to 1440px
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.evaluate(() => window.__AI_LIBRARY__.universe.collapseRightSidebar(false));

    console.log('[UI Enhancements Test] 3. Verifying Item 3-1 & 3-2: Bridge Cockpit HUD & Warp Streaks...');
    const flightBtn = page.locator('#universe-flight-toggle');
    await flightBtn.click();
    await page.waitForTimeout(300);

    // Switch to first person view (KeyV)
    await page.keyboard.press('KeyV');
    await page.waitForTimeout(300);

    const cockpitOverlay = page.locator('#bridge-cockpit-overlay');
    assert.ok(await cockpitOverlay.isVisible(), 'Cockpit overlay must be visible in first-person flight mode');
    assert.ok(await page.locator('.cockpit-reticle').isVisible(), 'Cockpit crosshair reticle must be visible');
    assert.ok(await page.locator('#cockpit-telemetry-hdg').isVisible(), 'Heading telemetry must be visible');

    // Accelerate to high speed to trigger Warp Streaks and HUD telemetry
    console.log('[UI Enhancements Test] Accelerating station to high cruise speed...');
    await page.keyboard.down('KeyW');
    await page.waitForTimeout(1000);
    await page.keyboard.up('KeyW');
    await page.waitForTimeout(200);

    const flightStats = await page.evaluate(() => {
      const sf = window.__AI_LIBRARY__.universe.stationFlight;
      const speed = sf.velocity.length();
      const warpVisible = sf.warpStreaks?.visible;
      const warpOpacity = sf.warpStreaks?.material?.opacity;
      const hdgText = document.getElementById('cockpit-telemetry-hdg')?.textContent;
      const spdText = document.getElementById('cockpit-telemetry-spd')?.textContent;
      const warpText = document.getElementById('cockpit-telemetry-warp')?.textContent;
      return { speed, warpVisible, warpOpacity, hdgText, spdText, warpText };
    });

    console.log('[UI Enhancements Test] Flight telemetry:', flightStats);
    assert.ok(flightStats.speed > 0.4, 'Speed should be > 0.4 AU/s after thrust');
    assert.equal(flightStats.warpVisible, true, 'Warp speed streaks line system must be visible at high speed');
    assert.ok(flightStats.warpOpacity > 0, 'Warp streaks opacity must be > 0');
    assert.ok(flightStats.spdText.includes('AU/s'), 'Avionics speed display formatted correctly');

    // Capture Cockpit HUD & Warp Streaks screenshot
    const cockpitScreenshot = path.join(ARTIFACT_DIR, 'bridge-cockpit-hud-with-warp-streaks.png');
    await page.screenshot({ path: cockpitScreenshot });
    console.log('[UI Enhancements Test] Saved:', cockpitScreenshot);

    // Exit flight
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
    assert.equal(await cockpitOverlay.isVisible(), false, 'Cockpit overlay must be hidden after exiting flight');

    console.log('[UI Enhancements Test] 4. Verifying Item 3-3: Settings Dialog Icons & Expiration Presets...');
    const settingsBtn = page.locator('#universe-settings');
    await settingsBtn.click();
    await page.waitForTimeout(300);

    const settingsDialog = page.locator('#settings-dialog');
    assert.ok(await settingsDialog.isVisible(), 'Settings dialog must be visible');

    // Check tab icons
    const tabTexts = await page.locator('.settings-tab').allInnerTexts();
    console.log('[UI Enhancements Test] Settings tabs:', tabTexts);
    assert.ok(tabTexts.some(t => t.includes('🔑') && t.includes('连接权限')), 'Access tab must have 🔑 icon');
    assert.ok(tabTexts.some(t => t.includes('📋') && t.includes('规则')), 'Rules tab must have 📋 icon');
    assert.ok(tabTexts.some(t => t.includes('⚙️') && t.includes('系统')), 'System tab must have ⚙️ icon');
    assert.ok(tabTexts.some(t => t.includes('💾') && t.includes('备份')), 'Data tab must have 💾 icon');
    assert.ok(tabTexts.some(t => t.includes('🔔') && t.includes('自动化')), 'Automation tab must have 🔔 icon');
    assert.ok(tabTexts.some(t => t.includes('🎨') && t.includes('体验')), 'Appearance tab must have 🎨 icon');
    assert.ok(tabTexts.some(t => t.includes('ℹ️') && t.includes('关于')), 'About tab must have ℹ️ icon');

    // Verify grant expiration presets
    const presetPills = page.locator('.grant-preset-pill');
    const pillCount = await presetPills.count();
    console.log('[UI Enhancements Test] Grant preset pills count:', pillCount);
    assert.ok(pillCount >= 5, 'Should have at least 5 preset pills in grant row');

    // Test clicking +7天 pill
    const pill7d = page.locator('.grant-preset-pill').filter({ hasText: '+7天' }).first();
    await pill7d.click();
    await page.waitForTimeout(100);
    assert.ok(await pill7d.evaluate(el => el.classList.contains('active')), '+7天 pill should be active');
    const expiryVal7d = await page.locator('.settings-expiry-input').first().inputValue();
    assert.ok(expiryVal7d.length > 0, 'Expiry input value should be set for +7d');

    // Test clicking 永久有效 pill
    const pillNever = page.locator('.grant-preset-pill').filter({ hasText: '永久有效' }).first();
    await pillNever.click();
    await page.waitForTimeout(100);
    assert.ok(await pillNever.evaluate(el => el.classList.contains('active')), '永久有效 pill should be active');
    const expiryValNever = await page.locator('.settings-expiry-input').first().inputValue();
    assert.equal(expiryValNever, '', 'Expiry input value should be empty for 永久有效');

    // Test clicking 自定义 pill
    const pillCustom = page.locator('.grant-preset-pill').filter({ hasText: '自定义' }).first();
    await pillCustom.click();
    await page.waitForTimeout(100);
    assert.ok(await pillCustom.evaluate(el => el.classList.contains('active')), '自定义 pill should be active');
    assert.ok(await page.locator('.settings-expiry-input').first().isVisible(), 'Custom date input should be visible when custom is active');

    // Capture Settings Dialog screenshot
    const settingsScreenshot = path.join(ARTIFACT_DIR, 'settings-presets-and-tab-icons.png');
    await page.screenshot({ path: settingsScreenshot });
    console.log('[UI Enhancements Test] Saved:', settingsScreenshot);

    // Close settings dialog
    await page.locator('#settings-close').click();

    assert.deepEqual(errors, [], `No page errors should occur: ${JSON.stringify(errors)}`);
    console.log('🎉 ALL UI ENHANCEMENT TESTS (2-2, 2-3, 3-1, 3-2, 3-3) PASSED 100%!');
  } finally {
    await browser.close();
  }
})().catch(e => {
  console.error('❌ Test failed:', e);
  process.exit(1);
});
