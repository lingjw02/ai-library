// Performance and motion benchmark for Universe transitions & returns.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'D:/02_Development_Tools/live2d-maker/node_modules/playwright-core');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ['--enable-webgl', '--ignore-gpu-blocklist', '--use-gl=angle']
  });

  const report = {
    timestamp: new Date().toISOString(),
    browser: 'Chromium ' + browser.version(),
    viewport: { width: 1440, height: 1000 },
    coldStart: {},
    returns: [],
    summary: {}
  };

  try {
    const page = await browser.newPage({
      viewport: report.viewport,
      reducedMotion: 'no-preference'
    });

    const requests = [];
    page.on('request', r => {
      if (r.url().includes('/api/')) {
        requests.push({ url: r.url(), method: r.method(), time: Date.now() });
      }
    });

    // Instrument PerformanceObserver in page
    await page.addInitScript(() => {
      window.__perfMetrics = {
        longTasks: [],
        frameTimes: [],
        lastFrameTime: performance.now()
      };
      const obs = new PerformanceObserver(list => {
        for (const entry of list.getEntries()) {
          window.__perfMetrics.longTasks.push({
            duration: entry.duration,
            startTime: entry.startTime,
            name: entry.name
          });
        }
      });
      try { obs.observe({ entryTypes: ['longtask'] }); } catch (e) {}

      // Measure rAF intervals
      let frameCount = 0;
      function onFrame(now) {
        const delta = now - window.__perfMetrics.lastFrameTime;
        window.__perfMetrics.lastFrameTime = now;
        window.__perfMetrics.frameTimes.push(delta);
        if (window.__perfMetrics.frameTimes.length > 3000) {
          window.__perfMetrics.frameTimes.shift();
        }
        requestAnimationFrame(onFrame);
      }
      requestAnimationFrame(onFrame);
    });

    // 1. Cold start measurement
    const navStart = Date.now();
    await page.goto('http://127.0.0.1:8000/', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => window.__AI_LIBRARY__?.universe.data && window.__AI_LIBRARY__.universe.scene?.debug.ready);
    const coldEnd = Date.now();

    const coldMetrics = await page.evaluate(() => {
      const scene = window.__AI_LIBRARY__.universe.scene;
      const renderer = scene?.renderer;
      return {
        ready: scene?.debug.ready,
        bodies: scene?.bodies.length,
        renderInfo: renderer ? {
          calls: renderer.info.render.calls,
          triangles: renderer.info.render.triangles,
          geometries: renderer.info.memory.geometries,
          textures: renderer.info.memory.textures
        } : null
      };
    });

    report.coldStart = {
      durationMs: coldEnd - navStart,
      requestsCount: requests.length,
      ...coldMetrics
    };

    console.log('Cold Start:', JSON.stringify(report.coldStart));

    // Helper to measure an action and return to universe
    async function measureReturn(iteration, actionType, actionFn) {
      await page.waitForTimeout(100);
      const reqStartIdx = requests.length;

      // Reset window.__perfMetrics for this slice
      await page.evaluate(() => {
        window.__perfMetrics.sliceStart = performance.now();
        window.__perfMetrics.sliceFrames = [];
        window.__perfMetrics.sliceLongTasks = [];
        window.__perfMetrics.recording = true;
      });

      const start = Date.now();
      await actionFn();

      // Wait until return is complete and scene is active
      await page.waitForFunction((action) => {
        const app = window.__AI_LIBRARY__;
        const u = app?.universe;
        if (!u?.scene?.ready) return false;
        if (action === 'return_from_maintenance') {
          return app.activeTab === 'universe';
        }
        if (action === 'return_from_note_modal') {
          return !app.notesMgr.modalBackdrop.classList.contains('open') && !u.scene.reading;
        }
        return !u.topicId && !u.agentId && u.data;
      }, actionType);

      // Capture next 600ms of rendering after return
      await page.waitForTimeout(600);

      const end = Date.now();

      const metrics = await page.evaluate(() => {
        window.__perfMetrics.recording = false;
        const renderer = window.__AI_LIBRARY__.universe.scene?.renderer;
        const frameDeltas = window.__perfMetrics.frameTimes.slice(-35);
        const longTasks = window.__perfMetrics.longTasks.filter(t => t.startTime >= window.__perfMetrics.sliceStart);
        return {
          renderInfo: renderer ? {
            calls: renderer.info.render.calls,
            triangles: renderer.info.render.triangles,
            geometries: renderer.info.memory.geometries,
            textures: renderer.info.memory.textures
          } : null,
          frameDeltas,
          longTasks: longTasks.map(t => ({ duration: t.duration, startTime: t.startTime })),
          activeTab: window.__AI_LIBRARY__.activeTab
        };
      });

      const returnReqs = requests.slice(reqStartIdx);
      const frameDeltas = metrics.frameDeltas.filter(d => d > 0 && d < 1000);
      const sorted = [...frameDeltas].sort((a, b) => a - b);
      const p50 = sorted[Math.floor(sorted.length * 0.5)] || 0;
      const p95 = sorted[Math.floor(sorted.length * 0.95)] || 0;
      const maxDelta = sorted[sorted.length - 1] || 0;
      const jankFrames = frameDeltas.filter(d => d > 33.3).length;

      const record = {
        iteration,
        actionType,
        totalTimeMs: end - start,
        requestsCount: returnReqs.length,
        p50FrameMs: Math.round(p50 * 10) / 10,
        p95FrameMs: Math.round(p95 * 10) / 10,
        maxFrameMs: Math.round(maxDelta * 10) / 10,
        jankFramesCount: jankFrames,
        longTasksCount: metrics.longTasks.length,
        longTasksMaxMs: metrics.longTasks.length ? Math.round(Math.max(...metrics.longTasks.map(t => t.duration))) : 0,
        renderInfo: metrics.renderInfo
      };

      console.log(`[Iter ${iteration} - ${actionType}] total: ${record.totalTimeMs}ms, reqs: ${record.requestsCount}, maxFrame: ${record.maxFrameMs}ms, jankFrames: ${record.jankFramesCount}, longTasks: ${record.longTasksCount}`);
      report.returns.push(record);
      return record;
    }

    // Execute 10 consecutive return scenarios
    for (let i = 1; i <= 10; i++) {
      if (i % 4 === 1) {
        // Return from Maintenance
        await measureReturn(i, 'return_from_maintenance', async () => {
          await page.click('#universe-maintenance');
          await page.waitForFunction(() => window.__AI_LIBRARY__.activeTab === 'maintenance');
          await page.waitForTimeout(150);
          await page.click('#universe-maintenance'); // Return to universe
        });
      } else if (i % 4 === 2) {
        // Return from Note Modal (reading)
        await measureReturn(i, 'return_from_note_modal', async () => {
          if (await page.locator('#universe-directory').isHidden()) {
            await page.click('#universe-directory-toggle');
          }
          const noteEntry = page.locator('.universe-entry[data-kind="note"] .universe-entry-main').first();
          if (await noteEntry.count() > 0 && await noteEntry.isVisible()) {
            await noteEntry.click();
            await page.waitForSelector('#note-modal-backdrop.open');
            await page.waitForTimeout(100);
            await page.click('#note-modal-close');
          } else {
            await page.click('#neural-overview');
          }
        });
      } else if (i % 4 === 3) {
        // Return from Topic drill-down
        await measureReturn(i, 'return_from_topic_drilldown', async () => {
          if (await page.locator('#universe-directory').isHidden()) {
            await page.click('#universe-directory-toggle');
          }
          const topicEntry = page.locator('.universe-entry[data-kind="topic"] .universe-entry-main').first();
          if (await topicEntry.count() > 0 && await topicEntry.isVisible()) {
            await topicEntry.click();
            await page.waitForTimeout(100);
          }
          await page.click('#neural-overview');
        });
      } else {
        // Return from Agent perspective / view
        await measureReturn(i, 'return_from_agent_perspective', async () => {
          await page.click('#universe-notes-hub');
          await page.waitForTimeout(100);
          await page.click('#neural-overview');
        });
      }
    }

    // Compute Summary
    const totalTimes = report.returns.map(r => r.totalTimeMs);
    const maxFrames = report.returns.map(r => r.maxFrameMs);
    const reqCounts = report.returns.map(r => r.requestsCount);
    const janks = report.returns.map(r => r.jankFramesCount);

    report.summary = {
      avgReturnDurationMs: Math.round(totalTimes.reduce((a, b) => a + b, 0) / totalTimes.length),
      maxReturnDurationMs: Math.max(...totalTimes),
      avgMaxFrameDeltaMs: Math.round(maxFrames.reduce((a, b) => a + b, 0) / maxFrames.length),
      peakFrameDeltaMs: Math.max(...maxFrames),
      totalRequestsAcross10Returns: reqCounts.reduce((a, b) => a + b, 0),
      avgRequestsPerReturn: Math.round(reqCounts.reduce((a, b) => a + b, 0) / reqCounts.length),
      totalJankFrames: janks.reduce((a, b) => a + b, 0),
      firstIterGeometries: report.returns[0]?.renderInfo?.geometries,
      lastIterGeometries: report.returns[report.returns.length - 1]?.renderInfo?.geometries,
      geometryGrowth: (report.returns[report.returns.length - 1]?.renderInfo?.geometries || 0) - (report.returns[0]?.renderInfo?.geometries || 0)
    };

    console.log('BENCHMARK SUMMARY:', JSON.stringify(report.summary, null, 2));

    const outDir = path.resolve('.agent-tmp/motion-benchmark-20261012');
    fs.mkdirSync(outDir, { recursive: true });
    fs.writeFileSync(path.join(outDir, 'optimized-report.json'), JSON.stringify(report, null, 2));

    await page.close();
  } catch (err) {
    console.error('Benchmark Error:', err);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
