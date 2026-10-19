const { chromium } = require('D:/02_Development_Tools/live2d-maker/node_modules/playwright-core');
const assert = require('assert');
const path = require('path');

const ARTIFACT_DIR = 'C:/Users/ling5/.gemini/antigravity-cli/brain/aac86e4a-04b4-4452-a13d-2a5b690567ff';

(async () => {
  let browser;
  try {
    console.log('1. Launching browser to verify Skill Station and softened celestial models...');
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();

    const consoleErrors = [];
    page.on('pageerror', err => consoleErrors.push(err.message));
    page.on('console', msg => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });

    await page.goto('http://127.0.0.1:8000/');
    await page.waitForFunction(() => window.__AI_LIBRARY__?.universe?.scene?.ready, { timeout: 15000 });
    await page.evaluate(() => window.__AI_LIBRARY__.universe.dismissLoading());
    await page.waitForFunction(() => {
      const s = window.__AI_LIBRARY__?.universe?.scene;
      return s && s.ready && s.bodies.length > 5 && s.tractorMeshes.length > 0;
    }, { timeout: 15000 });

    console.log('2. Inspecting Skill Station model and rotating parts...');
    const stationInspect = await page.evaluate(() => {
      const u = window.__AI_LIBRARY__.universe.scene;
      const station = u.bodies.find(b => b.data?.id === '__local_skills' || b.data?.isStationCore || (b.data?.title && b.data.title.includes('技能')));
      if (!station) return { error: 'Skill station body not found' };

      const rotParts = station.group.userData.rotatingParts || [];
      const hasHabitatRing = rotParts.some(p => p.object?.name === 'station-habitat-ring' && p.speed === 0.18);
      const childNames = station.group.children.map(c => c.name || c.type);

      return {
        found: true,
        id: station.data?.id,
        title: station.data?.title,
        position: station.group.position.toArray(),
        radius: station.radius,
        hasHabitatRing,
        childNames,
        rotatingPartsCount: rotParts.length
      };
    });
    console.log('Skill Station Inspection:', JSON.stringify(stationInspect, null, 2));
    assert(stationInspect.found, 'Skill Station body must exist in scene');
    assert(stationInspect.hasHabitatRing, 'Skill Station must have rotating habitat ring (speed 0.18)');

    console.log('3. Focusing camera on Skill Station and capturing screenshot...');
    await page.evaluate(() => {
      const u = window.__AI_LIBRARY__.universe.scene;
      const station = u.bodies.find(b => b.data?.id === '__local_skills' || b.data?.isStationCore || (b.data?.title && b.data.title.includes('技能')));
      if (station) {
        const target = station.group.position.clone();
        u.controls.target.copy(target);
        u.camera.position.set(target.x + 12, target.y + 7, target.z + 18);
        u.controls.update();
        u.requestFrame();
      }
    });
    await page.waitForTimeout(600);
    const stationScreenshotPath = path.join(ARTIFACT_DIR, 'skill-station-modular-habitat.png');
    await page.screenshot({ path: stationScreenshotPath });
    console.log('Saved screenshot:', stationScreenshotPath);

    console.log('4. Inspecting softened celestial models (Pulsar, Ringworld, Accretion Disk, Jellyfish)...');
    const stellarInspect = await page.evaluate(() => {
      const u = window.__AI_LIBRARY__.universe.scene;
      const topics = u.bodies.filter(b => b.data?.kind === 'topic' && !b.data.preview);
      
      const summary = topics.slice(0, 10).map(t => {
        let maxLineOpacity = 0;
        let whiteLineCount = 0;
        t.group.traverse(obj => {
          if (obj.isLine && obj.material) {
            const op = obj.material.opacity;
            if (op > maxLineOpacity) maxLineOpacity = op;
            const hex = obj.material.color?.getHexString?.();
            if (hex === 'ffffff' && op > 0.8) whiteLineCount++;
          }
        });
        return {
          id: t.data.id,
          title: t.data.title,
          maxLineOpacity,
          whiteLineCount
        };
      });

      return {
        topicCount: topics.length,
        sample: summary
      };
    });
    console.log('Stellar Inspection:', JSON.stringify(stellarInspect, null, 2));
    assert(stellarInspect.topicCount > 0, 'Topic bodies must exist');
    // Verify that harsh white 0.95 lines are eliminated
    for (const item of stellarInspect.sample) {
      assert.strictEqual(item.whiteLineCount, 0, `Topic ${item.id} should not have harsh raw white lines (found ${item.whiteLineCount})`);
    }

    console.log('5. Focusing camera on celestial bodies and capturing screenshot...');
    await page.evaluate(() => {
      const u = window.__AI_LIBRARY__.universe.scene;
      u.resetView(false);
    });
    await page.waitForTimeout(600);
    const stellarScreenshotPath = path.join(ARTIFACT_DIR, 'softened-stellar-models.png');
    await page.screenshot({ path: stellarScreenshotPath });
    console.log('Saved screenshot:', stellarScreenshotPath);

    assert.strictEqual(consoleErrors.length, 0, `Console errors detected: ${consoleErrors.join('; ')}`);
    console.log('\nALL CHECKS AND ARTIFACT SCREENSHOTS PASSED SUCCESSFULLY!');
  } catch (err) {
    console.error('VERIFICATION FAILED:', err);
    process.exit(1);
  } finally {
    if (browser) await browser.close();
  }
})();
