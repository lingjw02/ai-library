/* Actual native-module loading, cache ownership, bounds and isolated asset contact sheet.
 * PLAYWRIGHT_MODULE may point to an existing playwright-core installation.
 */
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'D:/02_Development_Tools/live2d-maker/node_modules/playwright-core');
const root = path.resolve(__dirname, '../../..');
const output = path.join(root, '.agent-tmp/stellar-models-20261012');
const server = http.createServer((req, res) => {
  if (req.url === '/') { res.setHeader('Content-Type','text/html'); res.end('<html><body style="margin:0;background:#070d16"></body></html>'); return; }
  const file = path.resolve(root, '.' + decodeURIComponent(req.url.split('?')[0]));
  if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) {res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.glb')?'model/gltf-binary':'application/octet-stream');
  res.end(fs.readFileSync(file));
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1500,height:660}, deviceScaleFactor:1});
    const errors=[]; page.on('pageerror', e => errors.push(e.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    const report = await page.evaluate(async () => {
      const THREE = await import('/frontend/vendor/three/three.module.js');
      const models = await import('/frontend/js/universe-models.js');
      const first = models.loadUniverseModels();
      const samePromise = first === models.loadUniverseModels();
      const readiness = await first;
      const cases = [
        [{kind:'notes-hub'},'personal-core','personal'],
        [{kind:'topic',title:'Software engineering'},'project-engineering','project'],
        [{kind:'topic',title:'Art design'},'project-creative','project'],
        [{kind:'topic',title:'Research archive'},'project-knowledge','project'],
        [{kind:'note'},'note-crystal','note'],
        [{kind:'memory'},'memory-core','memory'],
        [{kind:'topic',id:'__local_skills'},'skill-station','skill'],
        [{kind:'topic',id:'__mcp_library'},'mcp-ship','mcp'],
        [{kind:'skill'},'skill-module','skill'],
        [{kind:'mcp'},'mcp-vessel','mcp'],
      ];
      const check = (condition, label) => {if(!condition) throw new Error(label);};
      check(samePromise,'Cache must reuse in-flight readiness promise');
      check(readiness.count===10,'All 10 real GLBs must load');
      let meshes=0;
      const normalized=[];
      const scene=new THREE.Scene();
      const renderer=new THREE.WebGLRenderer({antialias:true, preserveDrawingBuffer:true});
      renderer.setSize(1500,660); renderer.setClearColor(0x070d16);
      renderer.outputColorSpace=THREE.SRGBColorSpace;
      renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.3;
      document.body.append(renderer.domElement);
      scene.add(new THREE.HemisphereLight(0xcbdff1,0x303344,2));
      const key=new THREE.DirectionalLight(0xffe4c0,3.6);key.position.set(-4,6,5);scene.add(key);
      const fill=new THREE.DirectionalLight(0x83c9ff,2);fill.position.set(6,2,-3);scene.add(fill);
      const camera=new THREE.OrthographicCamera(-7.25,7.25,3.19,-3.19,.1,100);camera.position.set(0,0,20);
      cases.forEach(([data,asset,family],index) => {
        const model=models.createUniverseModel(data,1);
        check(model.userData.assetName===asset && model.userData.modelFamily===family,'Wrong family '+asset);
        model.updateMatrixWorld(true);
        let maxRadius=0;
        const v=new THREE.Vector3();
        model.traverse(o=>{if(o.isMesh){meshes++;const p=o.geometry.attributes.position;
          for(let i=0;i<p.count;i++){v.fromBufferAttribute(p,i).applyMatrix4(o.matrixWorld);maxRadius=Math.max(maxRadius,v.length());}}});
        check(Math.abs(maxRadius-1)<.0001,'Incorrect normalized radius '+asset+': '+maxRadius);
        normalized.push({asset,radius:maxRadius,movingParts:model.userData.movingParts.length});
        model.position.set((index%5-2)*2.9,index<5?1.48:-1.48,0);
        model.rotation.set(.3,-.38,-.06);
        scene.add(model);
        const label=document.createElement('div'); label.textContent=asset;
        label.style.cssText=`position:absolute;left:${index%5*300}px;top:${index<5?284:590}px;width:300px;text-align:center;color:#b6c8d4;font:13px sans-serif`;
        document.body.append(label);
      });
      const cloneA=models.createUniverseModel({kind:'memory'},2);
      const cloneB=models.createUniverseModel({kind:'memory'},1);
      let meshA,meshB;cloneA.traverse(o=>{if(o.isMesh&&!meshA)meshA=o;});cloneB.traverse(o=>{if(o.isMesh&&!meshB)meshB=o;});
      check(meshA!==meshB&&meshA.geometry===meshB.geometry&&meshA.material===meshB.material,'Immutable buffers must be reused with private transforms');
      const preview=models.createUniverseModel({kind:'memory'},.2,{preview:true});
      check(preview.children.length===1&&preview.children[0].geometry.attributes.position.count<60,'Preview must be lightweight');
      const hits=[];preview.children[0].raycast(new THREE.Raycaster(),hits);check(hits.length===0,'Preview must never be picked');
      for(const role of ['skill','mcp','memory','note']){
        const model=models.createUniverseModel({kind:'local-core',modelRole:role},1);
        check(model.userData.modelFamily===role,'Wrong local core role '+role);
      }
      check(models.createUniverseModel({kind:'local-core',currentTitle:'Art design'},1).userData.assetName==='project-creative','Local centre must follow current collection');
      let invalidRejected=false;try{models.createUniverseModel({kind:'note'},0);}catch{invalidRejected=true;}
      check(invalidRejected,'Invalid scale must be rejected');
      renderer.render(scene,camera);
      window.testModels=models;window.testScene=scene;
      return {samePromise,readiness,normalized,meshes,drawCalls:renderer.info.render.calls,triangles:renderer.info.render.triangles};
    });
    fs.mkdirSync(output,{recursive:true});
    await page.screenshot({path:path.join(output,'model-contact-sheet.png')});
    const reload = await page.evaluate(async()=>{
      testModels.disposeUniverseModels();
      let rejected=false;try{testModels.createUniverseModel({kind:'note'},1);}catch{rejected=true;}
      const reloaded=await testModels.loadUniverseModels();return {rejected,reloaded};
    });
    assert.equal(reload.rejected,true);assert.equal(reload.reloaded.count,10);assert.deepEqual(errors,[]);
    // Loading failures must remain visible, never report partial readiness; retries recover.
    await page.evaluate(()=>testModels.disposeUniverseModels());
    await page.route('**/note-crystal.glb',route=>route.abort());
    assert.equal(await page.evaluate(async()=>{try{await testModels.loadUniverseModels();return false;}catch{return true;}}),true);
    await page.unroute('**/note-crystal.glb');
    assert.equal(await page.evaluate(async()=> (await testModels.loadUniverseModels()).count),10);
    fs.writeFileSync(path.join(output,'model-browser-verification.json'),JSON.stringify({status:'PASS',...report,reload,loadFailureAndRetry:'PASS'},null,2));
    console.log(JSON.stringify({status:'PASS',...report,reload,loadFailureAndRetry:'PASS'},null,2));
  } finally {await browser.close();server.close();}
})().catch(e=>{console.error(e);server.close();process.exitCode=1;});
