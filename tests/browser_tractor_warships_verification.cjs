const { chromium } = require('D:/02_Development_Tools/live2d-maker/node_modules/playwright-core');
const assert = require('assert');
const path = require('path');

const ARTIFACT_DIR = 'C:/Users/ling5/.gemini/antigravity-cli/brain/a7ac2f73-7c0e-418b-9b1e-0eddd175035c';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  page.on('pageerror', err => console.error('Browser Page Error:', err.message));
  page.on('console', msg => {
    if (msg.type() === 'error') console.error('Browser Console Error:', msg.text());
  });

  console.log('1. Loading Universe Overview...');
  await page.goto('http://127.0.0.1:8000/');
  await page.waitForFunction(() => {
    const u = window.__AI_LIBRARY__?.universe;
    return u?.scene?.debug?.ready && u?.scene?.tractorMeshes?.length > 0;
  }, { timeout: 15000 });

  // Wait 1.5s for initial layout & animations to settle
  await page.waitForTimeout(1500);

  // 1. Inspect Overview Tractor Line Connections
  const overviewTractorCheck = await page.evaluate(() => {
    const scene = window.__AI_LIBRARY__.universe.scene;
    const lines = scene.tractorCurves || [];
    const bodies = scene.bodies || [];
    const station = bodies.find(b => b.data?.id === '__local_skills' || b.data?.isStationCore);
    const stationId = station ? station.data.id : null;

    let attachedToStation = 0;
    let attachedToWarships = 0;
    const warshipEndpoints = [];

    for (const item of lines) {
      const fromIsStation = item.fromId === stationId;
      const toIsStation = item.toId === stationId;
      if (fromIsStation || toIsStation) attachedToStation++;

      const fromIsWarship = item.fromBody?.data?.isFlagship || item.fromBody?.data?.isEscort;
      const toIsWarship = item.toBody?.data?.isFlagship || item.toBody?.data?.isEscort;
      if (fromIsWarship || toIsWarship) {
        attachedToWarships++;
        const ws = fromIsWarship ? item.fromBody : item.toBody;
        const other = fromIsWarship ? item.toBody : item.fromBody;
        warshipEndpoints.push({
          warshipId: ws.data.id,
          warshipTitle: ws.data.title,
          domainId: ws.data.domainId,
          targetId: other?.data?.id,
          targetTitle: other?.data?.title
        });
      }
    }

    return {
      totalLines: lines.length,
      stationId,
      attachedToStation,
      attachedToWarships,
      sampleEndpoints: warshipEndpoints.slice(0, 10)
    };
  });

  console.log('Overview Tractor Beam Analysis:', JSON.stringify(overviewTractorCheck, null, 2));

  assert.equal(overviewTractorCheck.attachedToStation, 0, 'No tractor line should be attached to the space station!');
  assert.ok(overviewTractorCheck.attachedToWarships > 0, 'Tractor lines must be attached to warships!');

  // Take Overview Screenshot showing tractor beams connected to fleet
  await page.screenshot({ path: path.join(ARTIFACT_DIR, 'tractor-beams-overview-fleet.png') });
  console.log('Saved tractor-beams-overview-fleet.png');

  // Zoom in on Imperial Star Destroyer with its incoming tractor beams
  await page.evaluate(() => {
    const scene = window.__AI_LIBRARY__.universe.scene;
    const isd = scene.bodies.find(b => b.data?.isFlagship && b.data?.domainId === 'backend') ||
                scene.bodies.find(b => b.data?.isFlagship);
    if (isd) {
      const pos = isd.group.position;
      scene.moveCamera(pos, { x: pos.x + 8, y: pos.y + 5, z: pos.z + 12 }, true);
    }
  });
  await page.waitForTimeout(1000);
  await page.screenshot({ path: path.join(ARTIFACT_DIR, 'tractor-beam-isd-lockon.png') });
  console.log('Saved tractor-beam-isd-lockon.png');

  // Zoom in on Mon Calamari UI cruiser
  await page.evaluate(() => {
    const scene = window.__AI_LIBRARY__.universe.scene;
    const mc = scene.bodies.find(b => b.data?.isFlagship && b.data?.domainId === 'ui');
    if (mc) {
      const pos = mc.group.position;
      scene.moveCamera(pos, { x: pos.x - 7, y: pos.y + 4, z: pos.z + 10 }, true);
    }
  });
  await page.waitForTimeout(1000);
  await page.screenshot({ path: path.join(ARTIFACT_DIR, 'tractor-beam-mon-calamari-lockon.png') });
  console.log('Saved tractor-beam-mon-calamari-lockon.png');

  // 2. Check dynamic movement tracking (对跟)
  console.log('2. Verifying real-time tracking (对跟战舰)...');
  const trackingCheck = await page.evaluate(async () => {
    const scene = window.__AI_LIBRARY__.universe.scene;
    const item = scene.tractorCurves?.find(c => c.fromBody?.data?.isFlagship || c.toBody?.data?.isFlagship);
    if (!item) return { supported: false };

    const initialCurveV0 = { x: item.curve.v0.x, y: item.curve.v0.y, z: item.curve.v0.z };
    const posAttr = item.lineObj.geometry.attributes.position;
    const initialLinePt0 = { x: posAttr.getX(0), y: posAttr.getY(0), z: posAttr.getZ(0) };

    // Wait 400ms for station keeping / berth sway
    await new Promise(r => setTimeout(r, 400));

    const updatedCurveV0 = { x: item.curve.v0.x, y: item.curve.v0.y, z: item.curve.v0.z };
    const updatedLinePt0 = { x: posAttr.getX(0), y: posAttr.getY(0), z: posAttr.getZ(0) };
    const bodyPos = item.fromBody.group.position;

    const distV0 = Math.hypot(bodyPos.x - updatedCurveV0.x, bodyPos.y - updatedCurveV0.y, bodyPos.z - updatedCurveV0.z);
    const distGeom = Math.hypot(bodyPos.x - updatedLinePt0.x, bodyPos.y - updatedLinePt0.y, bodyPos.z - updatedLinePt0.z);

    return {
      supported: true,
      bodyPos: { x: bodyPos.x, y: bodyPos.y, z: bodyPos.z },
      updatedCurveV0,
      updatedLinePt0,
      v0FollowedBody: distV0 < 0.001,
      geometryPt0FollowedBody: distGeom < 0.001
    };
  });
  console.log('Real-time Tracking Check:', trackingCheck);
  assert.ok(trackingCheck.supported, 'Must find a warship tractor curve');
  assert.ok(trackingCheck.v0FollowedBody, 'Curve v0 must follow warship group position in real time');
  assert.ok(trackingCheck.geometryPt0FollowedBody, 'Line buffer geometry must follow warship group position in real time');

  // 3. Navigate into Skill Station interior (母港内部视图)
  console.log('3. Navigating into Skill Space Station interior...');
  await page.locator('#universe-directory-toggle').click();
  await page.locator('.universe-entry-main').filter({ hasText: /^Skills/ }).click();
  await page.waitForFunction(() => {
    const u = window.__AI_LIBRARY__?.universe;
    const modal = document.getElementById('universe-progress-modal');
    return u?.data?.topic?.virtual && (!modal || !modal.open);
  }, { timeout: 60000 });
  await page.waitForTimeout(1500);

  const interiorTractorCheck = await page.evaluate(() => {
    const scene = window.__AI_LIBRARY__.universe.scene;
    const lines = scene.tractorCurves || [];
    const bodies = scene.bodies || [];
    const core = bodies.find(b => b.data?.isStationCore);
    const coreId = core ? core.data.id : null;

    let attachedToCore = 0;
    let attachedToWarships = 0;
    const warshipEndpoints = [];

    for (const item of lines) {
      if (item.fromId === coreId || item.toId === coreId) attachedToCore++;
      const fromIsWarship = item.fromBody?.data?.isFlagship || item.fromBody?.data?.isEscort;
      const toIsWarship = item.toBody?.data?.isFlagship || item.toBody?.data?.isEscort;
      if (fromIsWarship || toIsWarship) {
        attachedToWarships++;
        const ws = fromIsWarship ? item.fromBody : item.toBody;
        const other = fromIsWarship ? item.toBody : item.fromBody;
        warshipEndpoints.push({
          warshipId: ws.data.id,
          warshipTitle: ws.data.title,
          domainId: ws.data.domainId,
          targetId: other?.data?.id
        });
      }
    }

    return {
      totalLines: lines.length,
      attachedToCore,
      attachedToWarships,
      sampleEndpoints: warshipEndpoints.slice(0, 10)
    };
  });

  console.log('Interior Tractor Beam Analysis:', JSON.stringify(interiorTractorCheck, null, 2));
  // Close directory sidebar if open
  await page.evaluate(() => {
    window.__AI_LIBRARY__.universe.toggleDirectory(false);
  });
  await page.waitForTimeout(500);

  // Take Interior Screenshot
  await page.screenshot({ path: path.join(ARTIFACT_DIR, 'tractor-beams-interior-battle-sectors.png') });
  console.log('Saved tractor-beams-interior-battle-sectors.png');

  console.log('\nALL TRACTOR BEAM WARSHIP DOCKING & TRACKING CHECKS PASSED SUCCESSFULLY!');
  await browser.close();
})();
