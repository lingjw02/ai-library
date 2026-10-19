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
    await page.waitForFunction(() => window.__AI_LIBRARY__?.universe?.data?.topics?.some(t => t.virtual), {}, { timeout: 30000 });

    console.log('2. Entering Skills & MCP Space Station...');
    await page.locator('#universe-directory-toggle').click();
    await page.locator('.universe-entry-main').filter({ hasText: /^Skills/ }).click();
    await page.waitForFunction(() => window.__AI_LIBRARY__?.universe?.data?.topic?.virtual, {}, { timeout: 30000 });
    await page.waitForTimeout(2000);

    console.log('3. Verifying MCP items in directory and 3D scene...');
    const mcpCount = await page.locator('.universe-entry[data-kind="mcp"]').count();
    console.log(`Found ${mcpCount} MCP entries in directory list`);
    assert.ok(mcpCount >= 5, `Expected at least 5 MCP servers, found ${mcpCount}`);

    const sceneMcpCount = await page.evaluate(() => {
      const u = window.__AI_LIBRARY__.universe;
      return u.scene.bodies.filter(b => b.data && (b.data.kind === 'mcp' || b.data.isMcp)).length;
    });
    console.log(`Found ${sceneMcpCount} MCP warship bodies in 3D scene`);
    assert.equal(sceneMcpCount, mcpCount, '3D scene MCP body count must match directory list MCP count');

    const mcpTitles = await page.locator('.universe-entry[data-kind="mcp"] strong').allInnerTexts();
    console.log('Discovered MCP servers:', mcpTitles);
    assert.ok(mcpTitles.some(t => t.includes('chrome-devtools')), 'Must include chrome-devtools');
    assert.ok(mcpTitles.some(t => t.includes('supabase')), 'Must include supabase');
    assert.ok(mcpTitles.some(t => t.includes('ai-memory-library')), 'Must include ai-memory-library');

    const artifactDir = 'C:/Users/ling5/.gemini/antigravity-cli/brain/a7ac2f73-7c0e-418b-9b1e-0eddd175035c';
    await page.screenshot({ path: path.join(artifactDir, 'space-station-mcp-fleet-overview.png') });
    console.log('Captured space-station-mcp-fleet-overview.png');

    console.log('4. Inspecting MCP Server details modal...');
    const firstMcp = page.locator('.universe-entry[data-kind="mcp"] .universe-entry-main').first();
    const firstMcpName = await firstMcp.locator('strong').innerText();
    await firstMcp.click();
    await page.locator('#universe-skill-dialog[open]').waitFor({ timeout: 5000 });

    const dialogTitle = await page.locator('#universe-skill-title').innerText();
    const dialogDesc = await page.locator('#universe-skill-description').innerText();
    const dialogLocations = await page.locator('#universe-skill-locations').innerText();

    console.log('Modal title:', dialogTitle);
    console.log('Modal desc preview:', dialogDesc.slice(0, 150));
    console.log('Modal locations:', dialogLocations);

    assert.ok(dialogTitle.includes(firstMcpName), 'Dialog title should match MCP name');
    assert.ok(dialogDesc.includes('MCP') || dialogDesc.includes('Tools') || dialogDesc.includes('工具'), 'Dialog description should mention MCP/tools');
    assert.ok(dialogLocations.includes('mcp') || dialogLocations.includes('MCP') || dialogLocations.includes('.json'), 'Dialog locations should reference MCP config');

    await page.screenshot({ path: path.join(artifactDir, 'space-station-mcp-detail-dialog.png') });
    console.log('Captured space-station-mcp-detail-dialog.png');
    await page.locator('#universe-skill-close').click();

    console.log('5. Testing search filter for MCP servers...');
    await page.locator('#universe-search').fill('supabase');
    await page.waitForTimeout(800);
    const filteredMcpCount = await page.locator('.universe-entry[data-kind="mcp"]').count();
    console.log(`Filtered MCP count for "supabase": ${filteredMcpCount}`);
    assert.ok(filteredMcpCount >= 1, 'Search for supabase should return supabase MCP entry');

    await page.screenshot({ path: path.join(artifactDir, 'space-station-mcp-search-supabase.png') });
    console.log('Captured space-station-mcp-search-supabase.png');

    assert.deepEqual(errors, []);
    console.log('ALL MCP SPACE STATION TESTS PASSED SUCCESSFULLY!');
  } finally {
    await browser.close();
  }
})().catch(err => {
  console.error('Test failed:', err);
  process.exit(1);
});
