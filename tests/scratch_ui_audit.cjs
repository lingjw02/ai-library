const { chromium } = require('D:/02_Development_Tools/live2d-maker/node_modules/playwright-core');
const path = require('node:path');
const outDir = 'C:/Users/ling5/.gemini/antigravity-cli/brain/a7ac2f73-7c0e-418b-9b1e-0eddd175035c';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto('http://127.0.0.1:8000/');
  await page.waitForFunction(() => window.__AI_LIBRARY__?.universe?.scene?.ready);
  await page.waitForTimeout(500);

  // 1. Open Directory, find a topic and open it
  await page.locator('#universe-directory-toggle').click();
  await page.waitForTimeout(300);

  // Click on 'AI library' topic to see its entries
  await page.locator('.universe-entry-main').filter({ hasText: /^AI library/ }).click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: path.join(outDir, 'ui-audit-6-inside-topic.png') });

  // Click first note in directory
  const firstNote = page.locator('.universe-entry[data-kind="note"] .universe-entry-main').first();
  if (await firstNote.count() > 0) {
    await firstNote.click();
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(outDir, 'ui-audit-7-note-modal.png') });
    const cancelBtn = page.locator('#note-modal-cancel');
    if (await cancelBtn.isVisible()) await cancelBtn.click();
    await page.waitForTimeout(300);
  }

  // Click first memory in directory
  const firstMem = page.locator('.universe-entry[data-kind="memory"] .universe-entry-main').first();
  if (await firstMem.count() > 0) {
    await firstMem.click();
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(outDir, 'ui-audit-8-memory-drawer.png') });
  }

  console.log('Detail screenshots captured!');
  await browser.close();
})();
