const { chromium } = require('D:/02_Development_Tools/live2d-maker/node_modules/playwright-core');
const assert = require('assert');
const path = require('path');

const ARTIFACT_DIR = 'C:/Users/ling5/.gemini/antigravity-cli/brain/aac86e4a-04b4-4452-a13d-2a5b690567ff';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  page.on('pageerror', err => console.error('Browser Page Error:', err.message));
  page.on('console', msg => {
    if (msg.type() === 'error') console.error('Browser Console Error:', msg.text());
  });

  console.log('1. Loading application and testing loading screen...');
  await page.goto('http://127.0.0.1:8000/');

  // Trigger loading state explicitly to inspect and screenshot the new neural core display
  const loadingInspect = await page.evaluate(async () => {
    const u = window.__AI_LIBRARY__?.universe;
    if (!u) return { error: 'Universe not initialized' };

    u.setTelemetry('星图同步', '正在编织神经星图与多维关联…', 45);
    const hud = document.getElementById('universe-warp-loading');
    const hasCore = Boolean(hud.querySelector('.neural-core-display'));
    const hasHalo = Boolean(hud.querySelector('.neural-core-halo'));
    const hasGoldRing = Boolean(hud.querySelector('.neural-orbit-ring.ring--gold'));
    const hasCyanRing = Boolean(hud.querySelector('.neural-orbit-ring.ring--cyan'));
    const hasNucleus = Boolean(hud.querySelector('.neural-core-nucleus'));
    const hasOldGyro = Boolean(hud.querySelector('.tactical-gyro-display'));
    const title = hud.querySelector('.warp-title')?.textContent;
    const subtitle = hud.querySelector('.warp-subtitle')?.textContent;

    return {
      visible: !hud.hidden,
      hasCore,
      hasHalo,
      hasGoldRing,
      hasCyanRing,
      hasNucleus,
      hasOldGyro,
      title,
      subtitle
    };
  });

  console.log('Loading Screen Inspection Result:', JSON.stringify(loadingInspect, null, 2));
  assert(loadingInspect.hasCore, 'neural-core-display should exist');
  assert(!loadingInspect.hasHalo, 'neural-core-halo should be removed');
  assert(loadingInspect.hasGoldRing, 'gold orbit ring should exist');
  assert(loadingInspect.hasCyanRing, 'cyan orbit ring should exist');
  assert(loadingInspect.hasNucleus, 'neural nucleus should exist');
  assert(!loadingInspect.hasOldGyro, 'tactical-gyro-display should be removed');
  assert.strictEqual(loadingInspect.title, '知识宇宙 · 正在编织神经星图');

  // Screenshot the new neural loading screen
  await page.screenshot({ path: path.join(ARTIFACT_DIR, 'neural-loading-screen.png') });
  console.log('Saved neural-loading-screen.png');

  // Now dismiss loading and let universe scene render
  await page.evaluate(() => {
    window.__AI_LIBRARY__.universe.dismissLoading();
  });
  await page.waitForTimeout(600);

  // Wait for 3D tractor lines to be ready
  await page.waitForFunction(() => {
    const scene = window.__AI_LIBRARY__?.universe?.scene;
    return scene?.ready && scene?.tractorMeshes?.length > 0 && scene?.tractorPulsePoints;
  }, { timeout: 10000 });

  console.log('2. Testing tractor lines multi-phase neural breathing & action potentials...');
  
  // Sample opacities across frames
  const breathingSamples = await page.evaluate(async () => {
    const scene = window.__AI_LIBRARY__.universe.scene;
    const meshes = scene.tractorMeshes;
    const curves = scene.tractorCurves;
    const pulses = scene.tractorPulsePoints;

    const linePhases = meshes.slice(0, 5).map(m => ({
      fromId: m.userData.fromId,
      toId: m.userData.toId,
      phase: m.userData.phase,
      breathSpeed: m.userData.breathSpeed,
      baseOpacity: m.userData.baseOpacity
    }));

    // Check opacities at t0
    const opacitiesT0 = meshes.slice(0, 5).map(m => m.material.opacity);

    // Wait 500ms
    await new Promise(r => setTimeout(r, 500));
    const opacitiesT1 = meshes.slice(0, 5).map(m => m.material.opacity);

    // Wait 500ms
    await new Promise(r => setTimeout(r, 500));
    const opacitiesT2 = meshes.slice(0, 5).map(m => m.material.opacity);

    // Check action potential vertex colors on tractorPulsePoints
    const colAttr = pulses.geometry.attributes.color;
    const colArray = colAttr ? Array.from(colAttr.array.slice(0, 24)) : [];

    return {
      lineCount: meshes.length,
      curveCount: curves.length,
      hasPulsePoints: Boolean(pulses),
      samplePhases: linePhases,
      opacitiesT0,
      opacitiesT1,
      opacitiesT2,
      sampleColors: colArray
    };
  });

  console.log('Breathing & Action Potentials Result:', JSON.stringify(breathingSamples, null, 2));

  // Check that lines have non-uniform phases (not synchronized)
  const phases = breathingSamples.samplePhases.map(p => p.phase);
  const uniquePhases = new Set(phases);
  console.log('Unique phases count in sample:', uniquePhases.size);
  assert(uniquePhases.size > 1, 'Tractor lines must have distinct, multi-phase offsets');

  // Check that opacities fluctuate over time (breathing animation active)
  let opacityChanged = false;
  for (let i = 0; i < breathingSamples.opacitiesT0.length; i++) {
    if (Math.abs(breathingSamples.opacitiesT0[i] - breathingSamples.opacitiesT1[i]) > 0.001 ||
        Math.abs(breathingSamples.opacitiesT1[i] - breathingSamples.opacitiesT2[i]) > 0.001) {
      opacityChanged = true;
      break;
    }
  }
  assert(opacityChanged, 'Tractor line opacity must dynamically breathe over time');

  // Screenshot the 3D universe with breathing neural tractor lines
  await page.screenshot({ path: path.join(ARTIFACT_DIR, 'neural-tractor-breathing.png') });
  console.log('Saved neural-tractor-breathing.png');

  console.log('\nALL VERIFICATIONS PASSED SUCCESSFULLY!');
  await browser.close();
})();
