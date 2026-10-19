const { chromium } = require('D:/02_Development_Tools/live2d-maker/node_modules/playwright-core');
const assert = require('node:assert/strict');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.on('pageerror', e => errors.push(e.message));

    console.log('1. Loading Universe home...');
    await page.goto('http://127.0.0.1:8000/');
    await page.waitForFunction(() => window.__AI_LIBRARY__?.universe?.data?.topics?.length > 0, {}, { timeout: 30000 });
    await page.waitForTimeout(2000);

    const artifactDir = 'C:/Users/ling5/.gemini/antigravity-cli/brain/a7ac2f73-7c0e-418b-9b1e-0eddd175035c';

    // Check overview previews for parent planet with child planets
    const overviewChildTopics = await page.evaluate(() => {
      const u = window.__AI_LIBRARY__.universe;
      const previews = u.scene.bodies.filter(b => b.data.preview && b.data.kind === 'topic');
      return previews.map(b => ({
        id: b.data.id,
        title: b.data.title,
        parentTitle: b.data.parentTitle,
        pos: [b.group.position.x, b.group.position.y, b.group.position.z]
      }));
    });
    console.log(`Found ${overviewChildTopics.length} preview child planets in overview`);
    await page.screenshot({ path: path.join(artifactDir, 'overview-subplanets-preview.png') });
    console.log('Captured overview-subplanets-preview.png');

    console.log('2. Entering parent planet "General / Antigravity" (id: topic-7cd5398921524e8786685c91117e64e7)...');
    await page.locator('#universe-directory-toggle').click();
    await page.locator('.universe-entry-main').filter({ hasText: 'General / Antigravity' }).first().click();
    await page.waitForFunction(() => {
      const u = window.__AI_LIBRARY__?.universe;
      return u?.data?.topic?.id === 'topic-7cd5398921524e8786685c91117e64e7';
    }, {}, { timeout: 30000 });
    await page.waitForTimeout(2500);

    console.log('3. Inspecting sub-planets (子行星) inside parent planet...');
    const subPlanets = await page.evaluate(() => {
      const u = window.__AI_LIBRARY__.universe;
      const childBodies = u.scene.bodies.filter(b => b.data.kind === 'topic' && !b.data.isAnchor && b.data.id !== '__notes_hub');
      return childBodies.map(b => ({
        id: b.data.id,
        title: b.data.title,
        radius: b.radius,
        pos: [b.group.position.x, b.group.position.y, b.group.position.z],
        distance: Math.hypot(b.group.position.x, b.group.position.z),
        y: b.group.position.y,
        orbit: b.orbit ? {
          distance: b.orbit.distance,
          tilt: b.orbit.tilt,
          yOffset: b.orbit.yOffset,
          speed: b.orbit.speed
        } : null
      }));
    });

    console.log('Discovered sub-planets inside parent planet:');
    for (const sp of subPlanets) {
      console.log(` - Planet "${sp.title}": R=${sp.distance.toFixed(2)}, Y=${sp.y.toFixed(2)}, orbitR=${sp.orbit?.distance.toFixed(2)}, tilt=${sp.orbit?.tilt?.toFixed(3)}, yOffset=${sp.orbit?.yOffset?.toFixed(2)}`);
    }

    assert.ok(subPlanets.length >= 3, `Expected at least 3 sub-planets, found ${subPlanets.length}`);

    // Verify non-uniform distances (randomized / staggered orbital distribution)
    const distances = subPlanets.map(sp => sp.distance);
    const minD = Math.min(...distances);
    const maxD = Math.max(...distances);
    console.log(`Sub-planets orbital distance range: ${minD.toFixed(2)} -> ${maxD.toFixed(2)}`);
    assert.ok(maxD - minD > 2.0, 'Sub-planets must not all sit on identical distance; must have orbital distance spread > 2.0');

    // Verify 3D volumetric elevation (Y offset jitter)
    const yValues = subPlanets.map(sp => sp.y);
    const minY = Math.min(...yValues);
    const maxY = Math.max(...yValues);
    console.log(`Sub-planets Y elevation range: ${minY.toFixed(2)} -> ${maxY.toFixed(2)}`);
    assert.ok(maxY - minY > 0.8, 'Sub-planets must have 3D volumetric elevation spread > 0.8');

    // Capture screenshots
    await page.screenshot({ path: path.join(artifactDir, 'subplanets-inside-parent-planet-overview.png') });
    console.log('Captured subplanets-inside-parent-planet-overview.png');

    // Focus on one sub-planet to get a close-up
    console.log('4. Focusing camera on first sub-planet...');
    await page.evaluate((targetId) => {
      window.__AI_LIBRARY__.universe.scene.focus(targetId, 'topic');
    }, subPlanets[0].id);
    await page.waitForTimeout(1500);

    await page.screenshot({ path: path.join(artifactDir, 'subplanets-closeup-celestial-view.png') });
    console.log('Captured subplanets-closeup-celestial-view.png');

    // Check directory list and navigation
    const dirCount = await page.locator('.universe-entry[data-kind="topic"]').count();
    console.log(`Directory list contains ${dirCount} sub-topic entries`);
    assert.ok(dirCount >= 3, 'Directory list should show sub-topics');

    assert.deepEqual(errors, []);
    console.log('ALL SUB-PLANET RANDOMIZED LAYOUT TESTS PASSED 100%!');
  } finally {
    await browser.close();
  }
})().catch(err => {
  console.error('Test failed:', err);
  process.exit(1);
});
