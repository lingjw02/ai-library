const { chromium } = require('D:/02_Development_Tools/live2d-maker/node_modules/playwright-core');
const assert = require('assert');

(async () => {
  let browser;
  try {
    console.log('1. Launching browser to test interaction smoothness and stutter fix...');
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();

    page.on('pageerror', err => console.error('Browser Page Error:', err.message));
    page.on('console', msg => {
      if (msg.type() === 'error') console.error('Browser Console Error:', msg.text());
    });

    await page.goto('http://127.0.0.1:8000/');

    // Dismiss loading and wait for universe scene to load universe data
    await page.waitForFunction(() => window.__AI_LIBRARY__?.universe?.scene?.ready, { timeout: 15000 });
    await page.evaluate(() => {
      window.__AI_LIBRARY__.universe.dismissLoading();
    });

    // Wait for bodies and tractor lines to be populated from backend
    await page.waitForFunction(() => {
      const s = window.__AI_LIBRARY__?.universe?.scene;
      return s && s.ready && s.bodies.length > 5 && s.tractorMeshes.length > 0;
    }, { timeout: 15000 });

    console.log('2. Inspecting initial animation state with populated universe...');
    const initialState = await page.evaluate(() => {
      const u = window.__AI_LIBRARY__.universe.scene;
      return {
        ready: u.ready,
        bodyCount: u.bodies.length,
        animating: u.canAnimate(),
        hovered: Boolean(u.hovered),
        interacting: Boolean(u.interacting),
        frames: u.frames
      };
    });
    console.log('Initial state:', initialState);
    assert.strictEqual(initialState.animating, true, 'Initial state canAnimate() should be true');

    console.log('3. Testing Hover behavior: pointing at a celestial body...');
    const hoverTest = await page.evaluate(async () => {
      const u = window.__AI_LIBRARY__.universe.scene;
      const topic = u.bodies.find(b => b.data?.kind === 'topic' && !b.data.preview);
      const anotherTopic = u.bodies.find(b => b.data?.kind === 'topic' && !b.data.preview && b !== topic);
      
      // Point at topic
      u.setHover(topic);
      const canAnimateWhileHovered = u.canAnimate();
      
      const frameBefore = u.frames;
      // Wait for 3 real browser RAF ticks
      await new Promise(r => {
        let count = 0;
        function tick() {
          count++;
          if (count >= 3) r();
          else requestAnimationFrame(tick);
        }
        requestAnimationFrame(tick);
      });
      const frameAfter = u.frames;

      return {
        canAnimateWhileHovered,
        frameBefore,
        frameAfter,
        framesAdvanced: frameAfter - frameBefore,
        hoveredId: u.hovered?.data?.id
      };
    });
    console.log('Hover test result:', hoverTest);
    assert.strictEqual(hoverTest.canAnimateWhileHovered, true, 'canAnimate() must remain true during hover!');
    assert(hoverTest.framesAdvanced >= 2, `Frames must continue advancing during hover: ${hoverTest.framesAdvanced} frames`);

    console.log('4. Testing Dragging / Interacting behavior...');
    const dragTest = await page.evaluate(async () => {
      const u = window.__AI_LIBRARY__.universe.scene;
      // Simulate OrbitControls start (dragging camera)
      u.interacting = true;
      const canAnimateWhileInteracting = u.canAnimate();

      // Sample a moving body's rotating part
      const movingBody = u.bodies.find(b => b.group.userData?.rotatingParts?.length);
      const part = movingBody.group.userData.rotatingParts[0];
      const rotBefore = part.object.rotation[part.axis];

      const frameBefore = u.frames;
      // Wait for 3 real browser RAF ticks
      await new Promise(r => {
        let count = 0;
        function tick() {
          count++;
          if (count >= 3) r();
          else requestAnimationFrame(tick);
        }
        requestAnimationFrame(tick);
      });
      const frameAfter = u.frames;

      const rotAfter = part.object.rotation[part.axis];

      // Reset interacting
      u.interacting = false;

      return {
        canAnimateWhileInteracting,
        frameBefore,
        frameAfter,
        framesAdvanced: frameAfter - frameBefore,
        rotBefore,
        rotAfter,
        rotationChanged: rotBefore !== rotAfter
      };
    });
    console.log('Drag test result:', dragTest);
    assert.strictEqual(dragTest.canAnimateWhileInteracting, true, 'canAnimate() must remain true during dragging/interaction!');
    assert(dragTest.framesAdvanced >= 2, `Frames must continue advancing during dragging: ${dragTest.framesAdvanced} frames`);
    assert(dragTest.rotationChanged, 'Celestial motions must continue updating while dragging!');

    console.log('5. Testing topic breathing localization...');
    const breathingLocalizationTest = await page.evaluate(async () => {
      const u = window.__AI_LIBRARY__.universe.scene;
      const topic1 = u.bodies.find(b => b.data?.kind === 'topic' && !b.data.preview);
      const topic2 = u.bodies.filter(b => b.data?.kind === 'topic' && !b.data.preview)[1];

      u.setHover(topic1);
      const scale1Before = topic1.group.scale.x;
      const scale2Before = topic2.group.scale.x;

      // Wait 3 RAF ticks
      await new Promise(r => {
        let count = 0;
        function tick() {
          count++;
          if (count >= 3) r();
          else requestAnimationFrame(tick);
        }
        requestAnimationFrame(tick);
      });

      const scale1After = topic1.group.scale.x;
      const scale2After = topic2.group.scale.x;

      u.setHover(null);

      return {
        hoveredTopicScaleChanged: scale1Before !== scale1After,
        unhoveredTopicScaleChanged: scale2Before !== scale2After
      };
    });
    console.log('Breathing localization test result:', breathingLocalizationTest);
    assert.strictEqual(breathingLocalizationTest.hoveredTopicScaleChanged, false, 'Hovered topic breathing scale must be paused');
    assert.strictEqual(breathingLocalizationTest.unhoveredTopicScaleChanged, true, 'Unhovered topic breathing scale must continue oscillating');

    console.log('6. Testing pick() cache optimization...');
    const pickOptimization = await page.evaluate(() => {
      const u = window.__AI_LIBRARY__.universe.scene;
      return {
        pickMeshesCount: u.pickMeshes.length,
        bodiesCount: u.bodies.length
      };
    });
    console.log('Pick optimization result:', pickOptimization);
    assert(pickOptimization.pickMeshesCount > 0, 'pickMeshes should be pre-cached!');

    console.log('\nALL INTERACTION SMOOTHNESS AND STUTTER FIX CHECKS PASSED SUCCESSFULLY!');
  } catch (err) {
    console.error('VERIFICATION FAILED:', err);
    process.exit(1);
  } finally {
    if (browser) await browser.close();
  }
})();
