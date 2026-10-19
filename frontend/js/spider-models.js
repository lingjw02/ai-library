import * as THREE from '../vendor/three/three.module.js';

// Authored star-map probes share materials, while silhouette identifies each application.
const STYLES={
 codex:{color:0xafe8d7,shape:'codex-aurora-seed',petals:3,length:1.15},
 antigravity:{color:0xc4b5eb,shape:'orbital-manta',petals:2,length:1.05},
 gemini:{color:0xa6c7ec,shape:'twin-core',petals:2,length:1},
 doubao:{color:0xebd1a2,shape:'lotus-capsule',petals:4,length:.95},
 deepseek:{color:0xa4d6e3,shape:'tidal-seed',petals:2,length:1.45},
 qwen:{color:0xe5becf,shape:'hexapetal-probe',petals:6,length:1.05},
 claude:{color:0xe6bc9d,shape:'claude-observatory',petals:5,length:1.05}
};
const seedFor=value=>{let n=2166136261;for(const c of String(value))n=Math.imul(n^c.charCodeAt(0),16777619);return n>>>0;};
const vec=([x,y,z])=>new THREE.Vector3(x,y,z);

export function createSpiderModel(application,projection=false){
 const key=application.style||application.id,seed=seedFor(application.id);
 const style=STYLES[key]||{color:0xc9d8de,shape:`survey-seed-${seed.toString(16)}`,petals:3+seed%4,length:.95+((seed>>>6)%11)/20};
 const group=new THREE.Group();group.name=`spider-${application.id}`;
 const geometries=new Set(),materials=new Set(),legs=[];
 const material=(type,options)=>{const m=new type(options);materials.add(m);return m;};
 const geometry=g=>{geometries.add(g);return g;};
 const shell=material(THREE.MeshBasicMaterial,{color:0xdad9d0,transparent:true,opacity:projection?.34:.49,side:THREE.DoubleSide,depthWrite:false});
 shell.forceSinglePass=true;
 const bone=material(THREE.MeshBasicMaterial,{color:0xaab8b7,transparent:true,opacity:projection?.66:.9});
 const gold=material(THREE.MeshBasicMaterial,{color:0xd6ba83,transparent:true,opacity:projection?.78:.94});
 const light=material(THREE.MeshBasicMaterial,{color:0xffebbf});
 const accent=material(THREE.MeshBasicMaterial,{color:style.color});
 const contour=material(THREE.LineBasicMaterial,{color:0xe5d1a6,transparent:true,opacity:projection?.36:.55,depthWrite:false});
 const aura=material(THREE.MeshBasicMaterial,{color:style.color,transparent:true,opacity:.045,depthWrite:false,blending:THREE.AdditiveBlending});
 const add=(g,m=gold,p=[0,0,0],scale,parent=group)=>{const mesh=new THREE.Mesh(geometry(g),m);mesh.position.fromArray(p);if(scale)mesh.scale.fromArray(scale);parent.add(mesh);return mesh;};
 const curve=(points,radius=.012,parent=group,m=gold)=>add(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(points.map(vec)),20,radius,5,false),m,[0,0,0],null,parent);
 const ring=(radius,y,tilt=0)=>{const mesh=add(new THREE.TorusGeometry(radius,.009,4,48),gold,[0,y,0]);mesh.rotation.set(Math.PI/2,0,tilt);return mesh;};
 const core=(p,scale)=>add(new THREE.SphereGeometry(.24,20,12),light,p,scale);
 const primaryCore=core([0,.15,0],key==='deepseek'?[.63,.64,1.25]:key==='doubao'?[.86,.84,.93]:[.75,.78,style.length]);
 add(new THREE.SphereGeometry(.32,16,10),aura,[0,.15,0],[.9,.9,style.length]);
 ring(.285,-.015);

 const petal=(angle,width=.19,length=.43,lift=.3)=>{
  const points=[],indices=[],rim=[];const steps=10;
  for(let i=0;i<=steps;i++){
   const t=i/steps,z=.05+t*length,y=.05+Math.sin(t*Math.PI)*lift,w=Math.sin(t*Math.PI)*width+.006;
   points.push(-w,y,z,w,y,z);rim.push(new THREE.Vector3(-w,y,z));
   if(i<steps){const k=i*2;indices.push(k,k+1,k+2,k+1,k+3,k+2);}
  }
  for(let i=steps;i>=0;i--)rim.push(new THREE.Vector3(points[i*6+3],points[i*6+4],points[i*6+5]));
  rim.push(rim[0].clone());
  const g=geometry(new THREE.BufferGeometry());g.setAttribute('position',new THREE.Float32BufferAttribute(points,3));g.setIndex(indices);g.computeVertexNormals();
  const lamella=new THREE.Group();lamella.rotation.y=angle;group.add(lamella);
  lamella.add(new THREE.Mesh(g,shell));
  lamella.add(new THREE.Line(geometry(new THREE.BufferGeometry().setFromPoints(rim)),contour));
 };
 for(let i=0;i<style.petals;i++)petal(i/style.petals*Math.PI*2+(key==='codex'?Math.PI/3:0),key==='antigravity'?.24:.16,.40*style.length,key==='qwen'?.24:.31);
 if(key==='antigravity'){
  const orbit=ring(.43,.23,.28);orbit.rotation.z=.25;
  curve([[-.47,.08,-.15],[-.56,.2,0],[-.43,.3,.25]],.011);
  curve([[.47,.08,-.15],[.56,.2,0],[.43,.3,.25]],.011);
 }else if(key==='gemini'){
  group.remove(primaryCore);
  for(const side of [-1,1])core([side*.15,.16,0],[.46,.69,.68]);
  curve([[-.30,.05,0],[-.27,.38,0],[0,.44,0],[.27,.38,0],[.30,.05,0]],.012);
 }else if(key==='deepseek'){
  curve([[0,.08,-.22],[0,.24,-.4],[0,.22,-.61]],.011);
  add(new THREE.SphereGeometry(.055,10,6),accent,[0,.18,.43],[1,.65,1.6]);
 }else if(key==='claude'){
  curve([[-.30,.02,0],[-.30,.3,0],[0,.51,0],[.30,.3,0],[.30,.02,0]],.012);
 }else if(key==='qwen'){
  ring(.34,.19);
 }else if(!STYLES[key]){
  const orbit=ring(.30+(seed>>>16)%8/100,.22);orbit.rotation.z=(seed>>>22)%9/15;
 }
 const eye=geometry(new THREE.SphereGeometry(.033,8,6));
 for(const side of [-1,1])add(eye,accent,[side*.105,.13,.31*style.length]);
 for(const side of [-1,1])for(let i=0;i<4;i++){
  const leg=new THREE.Group();leg.position.set(side*.17,.015,(i-1.5)*.13);group.add(leg);
  const spread=(i-1.5)*.29,reach=key==='antigravity'?1.13:key==='doubao'?.88:.99;
  const points=[[0,0,0],[side*.23,.15,spread*.45],[side*.52,.24,spread*.92],[side*reach,.025,spread*1.35],[side*(reach+.12),-.29,spread*1.55]];
  curve(points,.014,leg,bone);
  curve(points.map(([x,y,z])=>[x,y+.008,z]),.0045,leg,gold);
  legs.push({object:leg,phase:i*Math.PI/2+(side<0?Math.PI:0)});
 }
 group.userData.shape=style.shape;group.userData.legs=legs;group.userData.legCount=8;group.userData.color=style.color;
 group.userData.visualVersion='star-map-native-v1.1';
 group.userData.dispose=()=>{for(const g of geometries)g.dispose();for(const m of materials)m.dispose();};
 return group;
}
