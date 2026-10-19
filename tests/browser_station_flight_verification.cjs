const { chromium } = require('D:/02_Development_Tools/live2d-maker/node_modules/playwright-core');
const assert = require('node:assert/strict');
const path = require('node:path');

const ARTIFACT_DIR = 'C:/Users/ling5/.gemini/antigravity-cli/brain/a7ac2f73-7c0e-418b-9b1e-0eddd175035c';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 900 }
    });
    page.on('pageerror', e => errors.push(e.message));

    console.log('[Flight Test] Navigating to http://127.0.0.1:8000/ ...');
    await page.goto('http://127.0.0.1:8000/');

    await page.waitForFunction(() => {
      const u = window.__AI_LIBRARY__?.universe;
      return u && u.scene && u.scene.ready && u.scene.bodies.length > 0;
    }, {}, { timeout: 30000 });

    console.log('[Flight Test] Universe scene loaded with bodies.');

    // 1. Verify flight toggle button exists
    const toggleBtn = page.locator('#universe-flight-toggle');
    await assert.ok(await toggleBtn.isVisible(), 'Flight toggle button should be visible');
    const hud = page.locator('#station-flight-hud');
    assert.equal(await hud.isVisible(), false, 'Flight HUD should be hidden initially');

    // 2. Click button to activate flight
    console.log('[Flight Test] Activating flight via button...');
    await toggleBtn.click();
    await page.waitForTimeout(300);

    const flightState = await page.evaluate(() => {
      const sf = window.__AI_LIBRARY__.universe.stationFlight;
      return {
        active: sf.active,
        viewMode: sf.viewMode,
        freeView: sf.freeView,
        stationPos: sf.stationBody ? sf.stationBody.group.position.toArray() : null,
        isControlled: sf.stationBody?.isControlled
      };
    });

    assert.equal(flightState.active, true, 'Station flight should be active');
    assert.equal(flightState.isControlled, true, 'Station body should be controlled');
    assert.ok(flightState.stationPos, 'Station body must exist');
    assert.equal(await hud.isVisible(), true, 'HUD should be visible during flight');
    assert.ok(await page.locator('#universe-flight-toggle.active').isVisible(), 'Toggle button should have active class');

    console.log('[Flight Test] Station piloted at initial pos:', flightState.stationPos);

    // 3. Simulate WASD forward thrust (hold W for 600ms)
    console.log('[Flight Test] Engaging forward thrust (W)...');
    await page.keyboard.down('KeyW');
    await page.waitForTimeout(600);
    await page.keyboard.up('KeyW');
    await page.waitForTimeout(200);

    const afterThrust = await page.evaluate(() => {
      const sf = window.__AI_LIBRARY__.universe.stationFlight;
      const speed = sf.velocity.length();
      const pos = sf.stationBody.group.position.toArray();
      const speedText = document.getElementById('flight-hud-speed')?.innerText;
      return { speed, pos, speedText };
    });

    console.log('[Flight Test] Pos after thrust:', afterThrust.pos, 'speed:', afterThrust.speed, 'hud:', afterThrust.speedText);
    const movedDist = Math.hypot(
      afterThrust.pos[0] - flightState.stationPos[0],
      afterThrust.pos[1] - flightState.stationPos[1],
      afterThrust.pos[2] - flightState.stationPos[2]
    );
    assert.ok(movedDist > 0.05, `Station should have moved after thrust, moved: ${movedDist}`);

    // 3b. Test A (steer left) and D (steer right)
    console.log('[Flight Test] Testing A (steer left) and D (steer right)...');
    const h0 = await page.evaluate(() => window.__AI_LIBRARY__.universe.stationFlight.heading);
    await page.keyboard.down('KeyA');
    await page.waitForTimeout(400);
    await page.keyboard.up('KeyA');
    await page.waitForTimeout(100);
    const hLeft = await page.evaluate(() => window.__AI_LIBRARY__.universe.stationFlight.heading);
    assert.ok(hLeft > h0, 'Pressing A should steer to screen left (increasing heading)');

    await page.keyboard.down('KeyD');
    await page.waitForTimeout(800);
    await page.keyboard.up('KeyD');
    await page.waitForTimeout(100);
    const hRight = await page.evaluate(() => window.__AI_LIBRARY__.universe.stationFlight.heading);
    assert.ok(hRight < hLeft, 'Pressing D should steer to screen right (decreasing heading)');

    // 4. Capture 3rd person chase view screenshot
    console.log('[Flight Test] Capturing 3rd person screenshot...');
    await page.waitForTimeout(400);
    const thirdPersonPath = path.join(ARTIFACT_DIR, 'station-flight-third-person.png');
    await page.screenshot({ path: thirdPersonPath });
    console.log('[Flight Test] Saved:', thirdPersonPath);

    // 5. Test switching to 1st person bridge view (KeyV)
    console.log('[Flight Test] Switching to 1st person bridge view (KeyV)...');
    await page.keyboard.press('KeyV');
    await page.waitForTimeout(300);

    const firstPersonState = await page.evaluate(() => {
      const sf = window.__AI_LIBRARY__.universe.stationFlight;
      const viewLabel = document.getElementById('flight-hud-view-label')?.innerText;
      return { viewMode: sf.viewMode, viewLabel };
    });
    assert.equal(firstPersonState.viewMode, 'first', 'View mode should be first');
    assert.ok(firstPersonState.viewLabel.includes('第一人称'), 'HUD should reflect 1st person');

    await page.waitForTimeout(400);
    const firstPersonPath = path.join(ARTIFACT_DIR, 'station-flight-first-person-bridge.png');
    await page.screenshot({ path: firstPersonPath });
    console.log('[Flight Test] Saved:', firstPersonPath);

    // 6. Test Shift free view (press & release)
    console.log('[Flight Test] Testing Shift free view...');
    await page.keyboard.down('ShiftLeft');
    await page.waitForTimeout(200);
    const shiftDownState = await page.evaluate(() => {
      const sf = window.__AI_LIBRARY__.universe.stationFlight;
      const shiftLabel = document.getElementById('flight-hud-shift-label')?.innerText;
      return { freeView: sf.freeView, shiftLabel };
    });
    assert.equal(shiftDownState.freeView, true, 'Shift should activate free view');
    assert.ok(shiftDownState.shiftLabel.includes('自由观察中'), 'HUD shift label should indicate free view');

    await page.keyboard.up('ShiftLeft');
    await page.waitForTimeout(200);
    const shiftUpState = await page.evaluate(() => window.__AI_LIBRARY__.universe.stationFlight.freeView);
    assert.equal(shiftUpState, false, 'Releasing Shift should deactivate free view');

    // 7. Test Esc to exit flight mode
    console.log('[Flight Test] Exiting flight via Escape...');
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);

    const exitState = await page.evaluate(() => {
      const sf = window.__AI_LIBRARY__.universe.stationFlight;
      return {
        active: sf.active,
        stationBody: sf.stationBody,
        hudHidden: document.getElementById('station-flight-hud')?.hidden
      };
    });
    assert.equal(exitState.active, false, 'Flight should be inactive after Esc');
    assert.equal(exitState.hudHidden, true, 'HUD should be hidden after exit');

    // 8. Test F key to re-enter and exit
    console.log('[Flight Test] Testing F key re-entry...');
    await page.keyboard.press('KeyF');
    await page.waitForTimeout(300);
    assert.equal(await page.evaluate(() => window.__AI_LIBRARY__.universe.stationFlight.active), true, 'F should toggle flight active');
    await page.keyboard.press('KeyF');
    await page.waitForTimeout(300);
    assert.equal(await page.evaluate(() => window.__AI_LIBRARY__.universe.stationFlight.active), false, 'F should toggle flight off');

    assert.deepEqual(errors, [], `Page errors encountered: ${JSON.stringify(errors)}`);
    console.log('✅ ALL STATION FLIGHT E2E TESTS PASSED 100%!');
  } finally {
    await browser.close();
  }
})().catch(e => {
  console.error('❌ Test failed:', e);
  process.exit(1);
});
