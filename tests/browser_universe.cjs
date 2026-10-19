// Run against an isolated restored library; the test performs topic writes.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'D:/02_Development_Tools/live2d-maker/node_modules/playwright-core');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = process.env.UNIVERSE_TEST_URL || 'http://127.0.0.1:8768';
if (new URL(base).port === '8000') throw new Error('Use an isolated test service, never the daily library.');
const out = '.agent-tmp/universe-20261011';
(async () => {
  fs.mkdirSync(out, {recursive:true});
  const browser = await chromium.launch({headless:true});
  try {
    const report={checks:[],errors:[]};
    for(const [device,width,height,reducedMotion] of [['desktop',1440,1000,'no-preference'],['mobile',390,844,'reduce']]){
      const page=await browser.newPage({viewport:{width,height},reducedMotion});
      page.on('pageerror',e=>report.errors.push(e.message));
      await page.goto(base);
      await page.waitForFunction(()=>window.__AI_LIBRARY__?.universe.data && window.__AI_LIBRARY__.universe.scene?.debug.ready);
      const counts=await page.evaluate(()=>window.__AI_LIBRARY__.universe.data.counts);
      assert.ok(counts.notes>0&&counts.memories>0&&counts.topics>0);
      await page.waitForTimeout(800);
      await page.screenshot({path:`${out}/${device}-overview.png`});
      const camera=await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.debug.camera);
      await page.mouse.move(width*.52,height*.57);await page.mouse.down();await page.mouse.move(width*.67,height*.62,{steps:8});await page.mouse.up();
      await page.waitForTimeout(400);
      assert.notDeepEqual(await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.debug.camera),camera);
      await page.locator('#universe-reset').click();
      // 测试主题动态定位：优先复用历史 QA 主题（UI QA 前缀），兜底取内容最多的主题
      if(await page.locator('#universe-directory').isHidden())await page.locator('#universe-directory-toggle').click();
      const entryTitle=await page.evaluate(()=>window.__AI_LIBRARY__.universe.data.topics.find(t=>t.title.startsWith('UI QA'))?.title || window.__AI_LIBRARY__.universe.data.topics.slice().sort((a,b)=>(b.count||0)-(a.count||0))[0]?.title);
      await page.locator('.universe-entry-main').filter({hasText:entryTitle}).first().click();
      await page.waitForFunction(t=>window.__AI_LIBRARY__.universe.data.topic?.title===t,entryTitle);
      await page.waitForTimeout(700);
      await page.screenshot({path:`${out}/${device}-solar-system.png`});
      report.checks.push(`${device}: real WebGL, free rotation, topic navigation`);
      // 新知识宇宙 UI 已无类型过滤器；直接在主题目录中选取一条笔记
      if(await page.locator('#universe-directory').isHidden())await page.locator('#universe-directory-toggle').click();
      await page.locator('.universe-entry[data-kind="note"] .universe-entry-main').first().click();
      await page.locator('#note-modal-backdrop.open').waitFor();
      const original=await page.locator('#note-edit-content').inputValue();
      await page.locator('#note-edit-content').fill(original+'\nUNSAVED TEST');
      page.once('dialog',d=>d.dismiss());await page.locator('#note-modal-close').click();
      assert.ok((await page.locator('#note-edit-content').inputValue()).endsWith('UNSAVED TEST'));
      await page.locator('#note-edit-content').fill(original);
      assert.equal(await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.debug.reading),true);
      await page.screenshot({path:`${out}/${device}-note.png`});
      await page.locator('#note-modal-close').click();
      if(await page.locator('#universe-directory').isHidden())await page.locator('#universe-directory-toggle').click();
      const memoryRow=page.locator('.universe-entry[data-kind="memory"] .universe-entry-main').first();
      if(await memoryRow.count()){
        await memoryRow.click();
        await page.locator('#memory-detail-drawer.open').waitFor();
        assert.match(await page.locator('#drawer-verification-status').innerText(),/核验/);
        const provenance=await page.evaluate(()=>window.__AI_LIBRARY__.memoryNet.selectedNode.provenance);
        if(provenance){await page.locator('[data-drawertab="source"]').click();await page.locator('#drawer-btn-source').click();await page.locator('#note-modal-backdrop.open').waitFor();assert.equal(await page.evaluate(()=>window.__AI_LIBRARY__.notesMgr.activeNote.id),provenance.source_id);await page.locator('#note-modal-close').click();}
        else await page.locator('#drawer-btn-close').click();
      }
      report.checks.push(`${device}: note draft protection, memory provenance, reading pause`);
      await page.locator('#universe-home').click();
      await page.waitForFunction(()=>window.__AI_LIBRARY__.universe.data.topic===null);
      const title=`UI QA ${device} ${Date.now()}`;
      // 新建主题入口已随 UI 演进移除（2026-10-13）；主题管理段复用历史 QA 主题（UI QA 前缀），全新环境经 API 创建
      await page.waitForFunction(()=>window.__AI_LIBRARY__.universe.data.topics.length>0);
      let manageEntryTitle=await page.evaluate(()=>window.__AI_LIBRARY__.universe.data.topics.find(t=>t.title.startsWith('UI QA'))?.title);
      if(!entryTitle){
        const created=await page.evaluate(async t=>{const r=await fetch('/api/topics',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scope:window.__AI_LIBRARY__.universe.scope,title:t,parent_id:null})});return r.json();},title);
        assert.ok(created.id,'topic created via API for UI verification');
        await page.reload();await page.waitForFunction(()=>window.__AI_LIBRARY__?.universe?.data?.topics?.some(t=>t.title.startsWith('UI QA')));
        entryTitle=title;
      }
      if(await page.locator('#universe-directory').isHidden())await page.locator('#universe-directory-toggle').click();
      await page.locator('.universe-entry-main').filter({hasText:entryTitle}).first().click();
      await page.waitForFunction(t=>window.__AI_LIBRARY__.universe.data.topic?.title===t,entryTitle);
      const topicId=await page.evaluate(()=>window.__AI_LIBRARY__.universe.currentTopic.id);
      const renamed=title+' renamed';
      await page.route('**/api/topics/*',route=>route.request().method()==='PATCH'?route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'模拟保存失败'})}):route.continue());
      await page.locator('#universe-rename').click();await page.locator('#universe-topic-name').fill(renamed);await page.locator('#universe-topic-save').click();
      await page.locator('#universe-topic-error').filter({hasText:'模拟保存失败'}).waitFor();
      assert.equal(await page.locator('#universe-topic-name').inputValue(),renamed);
      await page.unroute('**/api/topics/*');await page.locator('#universe-topic-save').click();
      await page.waitForFunction(t=>window.__AI_LIBRARY__.universe.data.topic?.title===t,renamed);
      assert.ok((await page.locator('#universe-path').innerText()).includes(renamed));
      // 成员管理：目标条目以实时数据为准；移出后条目离开主题，需经全局搜索在「全部内容」里找回并加回
      const targetId=await page.evaluate(()=>window.__AI_LIBRARY__.universe.data.items.find(x=>x.kind==='note'||x.kind==='memory')?.id);
      assert.ok(targetId,'topic provides a manageable item');
      const itemTitle=await page.evaluate(id=>window.__AI_LIBRARY__.universe.data.items.find(x=>x.id===id)?.title,targetId);
      await page.waitForFunction(id=>[...document.querySelectorAll('#universe-list .universe-entry')].some(e=>e.dataset.id===id&&e.querySelector('.universe-entry-manage')),targetId,{timeout:15000});
      await page.locator(`.universe-entry[data-id="${targetId}"] .universe-entry-manage`).first().click();
      const membership=page.locator(`#universe-membership-list button[data-topic-id="${topicId}"]`);
      await membership.waitFor();await membership.click();await membership.filter({hasText:'移出'}).waitFor();
      await page.locator('#universe-membership-close').click();
      // reload 验证移出持久：全局搜索该条目（在全部内容）→ 重新加入主题
      await page.reload();
      await page.waitForFunction(()=>window.__AI_LIBRARY__?.universe?.data);
      await page.locator('#universe-search').fill(itemTitle);await page.locator('#universe-search-form').evaluate(f=>f.requestSubmit());
      await page.locator(`.universe-entry[data-id="${targetId}"]`).waitFor();
      await page.locator(`.universe-entry[data-id="${targetId}"] .universe-entry-manage`).first().click();
      await membership.waitFor();await membership.click();await membership.filter({hasText:'加入'}).waitFor();
      await page.locator('#universe-membership-close').click();
      // 加回后：主题仍可经搜索进入，条目回到主题内
      await page.locator('#universe-search').fill(renamed);await page.locator('#universe-search-form').evaluate(f=>f.requestSubmit());
      await page.locator('.universe-entry-main').filter({hasText:renamed}).click();
      await page.waitForFunction(t=>window.__AI_LIBRARY__.universe.data.topic?.id===t,topicId);
      await page.waitForFunction(id=>[...document.querySelectorAll('#universe-list .universe-entry')].some(e=>e.dataset.id===id&&e.querySelector('.universe-entry-manage')),targetId,{timeout:15000});
      await page.locator('#universe-search').fill(itemTitle);await page.locator('#universe-search-form').evaluate(f=>f.requestSubmit());
      await page.locator(`.universe-entry[data-id="${targetId}"]`).waitFor();
      report.checks.push(`${device}: rename with failed-save recovery, membership persists after reload, removal retains original, global search`);
      await page.locator('#universe-maintenance').click();await page.locator('#sub-btn-logsheet').click();await page.locator('#maintenance-list .btn-inspect').first().waitFor();
      await page.screenshot({path:`${out}/${device}-maintenance.png`});await page.locator('#universe-maintenance').click();
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      if(device==='mobile')assert.equal(await page.evaluate(()=>window.__AI_LIBRARY__.universe.scene.debug.reducedMotion),true);
      report.checks.push(`${device}: maintenance retained, no viewport overflow, reduced-motion preference`);
      await page.close();
    }
    const fallback=await browser.newPage();
    await fallback.addInitScript(()=>{const orig=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(kind,...args){if(String(kind).includes('webgl'))return null;return orig.call(this,kind,...args);};});
    fallback.on('pageerror',e=>report.errors.push(e.message));
    await fallback.goto(base);await fallback.locator('#universe-directory').waitFor();await fallback.locator('.universe-entry-main').first().waitFor();
    assert.equal(await fallback.evaluate(()=>window.__AI_LIBRARY__.universe.no3d),true);await fallback.close();report.checks.push('WebGL unavailable: functional directory fallback');
    assert.deepEqual(report.errors,[]);report.state='PASS';fs.writeFileSync(`${out}/browser-report.json`,JSON.stringify(report,null,2));console.log(JSON.stringify(report));
  } finally { await browser.close(); }
})().catch(e => {console.error(e);process.exitCode=1});
