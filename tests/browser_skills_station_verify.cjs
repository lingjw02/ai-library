const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'D:/02_Development_Tools/live2d-maker/node_modules/playwright-core');
const assert=require('node:assert/strict');

(async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
    await page.goto('http://127.0.0.1:8000/');
    await page.waitForFunction(()=>window.__AI_LIBRARY__?.universe.data&&window.__AI_LIBRARY__.universe.scene?.debug.ready);
    
    // Check that skill station body exists and has modelInstance
    const station=await page.evaluate(()=>{
      const b=window.__AI_LIBRARY__.universe.scene.bodies.find(x=>x.data.id==='__local_skills'||/^skills?/i.test(x.data.title));
      return {
        found: Boolean(b),
        title: b?.data.title,
        kind: b?.data.kind,
        hasModelInstance: Boolean(b?.modelInstance),
        modelName: b?.modelInstance?.name,
        groupChildren: b?.group.children.length
      };
    });
    console.log('Skill Station overview verification:', JSON.stringify(station));
    assert.ok(station.found, 'Skill topic must exist in overview');
    assert.ok(station.hasModelInstance, 'Skill topic must have modelInstance');
    assert.equal(station.modelName, 'skill-station');
    assert.ok(station.groupChildren > 25, 'Skill station group must have dock rings & service conduits');

    // Click into Skills station
    await page.locator('#universe-directory-toggle').click();
    await page.locator('.universe-entry-main').filter({hasText:'Skills'}).first().click();
    await page.waitForFunction(()=>window.__AI_LIBRARY__.universe.topicId==='__local_skills');
    
    const skillsData=await page.evaluate(()=>{
      const u=window.__AI_LIBRARY__.universe;
      const b=u.scene.bodies.filter(x=>x.data.kind==='skill');
      return {
        topicTitle: u.data.topic?.title,
        skillCount: b.length,
        modelsCount: b.filter(x=>x.modelInstance?.name==='skill-module').length
      };
    });
    console.log('Inside Skill Station verification:', JSON.stringify(skillsData));
    assert.ok(skillsData.skillCount > 0, 'Must have skill items rendered');
    assert.ok(skillsData.modelsCount > 0, 'Must have skill-module models instantiated');

    console.log('ALL SKILL STATION TESTS PASSED!');
  } finally {
    await browser.close();
  }
})().catch(e=>{console.error(e); process.exitCode=1;});
