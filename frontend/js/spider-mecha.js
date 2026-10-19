import * as THREE from '../vendor/three/three.module.js';
import {createSpiderModel} from './spider-models.js?h=20261012c';

const STATES={active:'探索中',idle:'空闲',unknown:'状态未知',disconnected:'连接中断'};
const ACTIONS={navigate:'导航',enter:'进入行星',follow:'沿关联探索',read:'读取资料',catalogue:'查看目录',search:'检索',observe:'资料访问'};
const STATUS={started:'请求中',completed:'已返回',failed:'失败'};
const text=(element,value)=>{const s=String(value??'');if(element.textContent!==s)element.textContent=s;};
const key=(application,session)=>`${application.id}:${session.id}`;

export class SpiderMechaLayer {
 constructor(scene,onFollowChange=()=>{}) {
  this.host=scene;this.onFollowChange=onFollowChange;this.models=new Map();this.applications=[];this.followKey=null;this.selectedKey=null;
  this.group=new THREE.Group();this.group.name='spider-mecha-layer';scene.scene.add(this.group);
  this.labels=document.createElement('div');this.labels.className='spider-mecha-labels';this.labels.setAttribute('aria-hidden','true');scene.container.append(this.labels);
  this.vector=new THREE.Vector3();this.lastTime=0;
 }
 resolve(position) {
  if(!position)return null;const bodies=this.host.bodies;
  let body=position.object_id?bodies.find(b=>!b.decorative&&String(b.data.id)===String(position.object_id)&&(!position.kind||b.data.kind===position.kind)):null;
  body??=bodies.find(b=>!b.decorative&&b.data.kind==='topic'&&String(b.data.id)===String(position.topic_id));
  if(!body&&position.topic_id&&String(this.host.data?.topic?.id)===String(position.topic_id))body=bodies.find(b=>b.data.kind==='notes-hub');
  // A visible child may stand in for its real parent only when the displayed data says so.
  if(!body&&position.topic_id){const child=this.host.data?.topics?.find(t=>t.children?.some(c=>String(c.id)===String(position.topic_id)));if(child)body=bodies.find(b=>String(b.data.id)===String(child.id));}
  return body || null;
 }
 dock(application,index) {
  const order=['codex','antigravity','gemini','doubao','deepseek','qwen','claude'];const slot=order.indexOf(application.id);
  let seed=2166136261;for(const char of String(application.id))seed=Math.imul(seed^char.charCodeAt(0),16777619);
  const a=slot>=0?slot*.6+.5:(seed>>>0)/4294967296*Math.PI*2,radius=slot>=0?12:14;
  return new THREE.Vector3(Math.cos(a)*radius,slot>=0?-7:-8,Math.sin(a)*radius);
 }
 sync(applications) {
  this.applications=applications;const wanted=new Set();
  applications.forEach((app,index)=>{
   const sessions=app.sessions || [],allIdle=sessions.length?sessions.every(s=>s.state==='idle'):app.state==='idle';
   const entries=allIdle?[{id:'__dock',label:app.name,state:'idle',dock:true}]:sessions.filter(s=>s.state!=='idle');
   for(const session of entries) {
    const id=key(app,session);wanted.add(id);let model=this.models.get(id);
    if(!model){const object=createSpiderModel(app,!session.dock);object.scale.setScalar(.85);object.rotation.x=.2;this.group.add(object);const label=document.createElement('span');label.className='spider-mecha-label';label.hidden=true;this.labels.append(label);model={id,object,label,application:app};this.models.set(id,model);}
    model.session=session;model.application=app;model.dockIndex=index;text(model.label,session.dock?`${app.name} · 空闲`:`${app.name} / ${session.label}`);
    const event=(session.events||[]).filter(e=>e.status==='completed').at(-1);const token=event?`${event.seq}:${event.status}`:'';
    if(token!==model.token){model.token=token;model.event=event;model.start=null;model.progress=0;}
   }
  });
  for(const [id,model] of this.models)if(!wanted.has(id)){model.object.userData.dispose();this.group.remove(model.object);model.label.remove();this.models.delete(id);}
  if(this.followKey&&!this.models.has(this.followKey))this.stopFollowing();
  this.host.requestFrame();this.tick(performance.now(),0);
 }
 clear(){this.stopFollowing();this.selectedKey=null;this.sync([]);}
 select(id){this.selectedKey=id;this.host.requestFrame();}
 follow(id){const model=this.models.get(id);if(!this.host.ready||!model||!model.object.visible||model.session.dock)return false;const host=this.host,body=this.resolve(model.session.position),target=model.object.position.clone();const offset=host.camera.position.clone().sub(host.controls.target).normalize().multiplyScalar(Math.max(12,(body?.radius||1)*6));this.followKey=id;host.moveCamera(target,target.clone().add(offset));this.onFollowChange(true);host.requestFrame();return true;}
 stopFollowing(){if(!this.followKey)return;this.followKey=null;this.onFollowChange(false);this.host.requestFrame();}
 tick(time,delta) {
  let moving=false;const host=this.host,reduced=host.reducedMotion,paused=host.paused||host.reading||host.interacting;
  for(const model of this.models.values()) {
   const session=model.session,body=session.dock?null:this.resolve(session.position);
   model.object.visible=Boolean(session.dock||body);if(!model.object.visible){model.label.hidden=true;if(this.followKey===model.id)this.stopFollowing();continue;}
   const target=session.dock?this.dock(model.application,model.dockIndex):body.group.position.clone().add(new THREE.Vector3(body.radius*1.12,body.radius*.42,0));
   if(!model.initialized){model.object.position.copy(session.dock||reduced||session.state!=='active'?target:this.dock(model.application,model.dockIndex));model.initialized=true;}
   const event=model.event,active=session.state==='active',motion=active&&!paused&&!reduced;
   if(model.progress<1&&motion&&event){
    model.start??=model.object.position.clone();model.progress=Math.min(1,model.progress+delta/1.1);
    const t=1-Math.pow(1-model.progress,3);let curve=null,reverse=false;
    if(session.mode==='crawl'&&event.from_topic_id&&event.topic_id){curve=host.tractorCurves?.find(c=>String(c.fromId)===String(event.from_topic_id)&&String(c.toId)===String(event.topic_id));if(!curve){curve=host.tractorCurves?.find(c=>String(c.toId)===String(event.from_topic_id)&&String(c.fromId)===String(event.topic_id));reverse=Boolean(curve);}}
    if(session.mode==='crawl'){if(curve){curve.curve.getPoint(reverse?1-t:t,model.object.position);model.object.position.y+=.35;}else{model.object.position.copy(target);model.progress=1;}}
    else {model.object.position.lerpVectors(model.start,target,t);model.object.position.y+=Math.sin(t*Math.PI)*2.1;}
    moving ||=model.progress<1;
    for(const leg of model.object.userData.legs)leg.object.rotation.z=Math.sin(time*.007+leg.phase)*(session.mode==='crawl'?.12:.035);
   }else if(!paused||reduced){model.object.position.copy(target);if(reduced||!active)model.progress=1;for(const leg of model.object.userData.legs)leg.object.rotation.z=0;}
   if(this.followKey===model.id&&!paused&&!host.transition){const offset=host.camera.position.clone().sub(host.controls.target);host.controls.target.copy(model.object.position);host.camera.position.copy(model.object.position).add(offset);}
   this.vector.copy(model.object.position).project(host.camera);const visible=this.vector.z>-1&&this.vector.z<1&&Math.abs(this.vector.x)<1&&Math.abs(this.vector.y)<1;
   model.label.hidden=!visible;model.label.classList.toggle('selected',model.id===this.selectedKey);model.label.style.transform=`translate(${(this.vector.x*.5+.5)*host.width}px,${(-this.vector.y*.5+.5)*host.height+22}px) translate(-50%,0)`;
  }
  this.animating=moving;return moving;
 }
 get debug(){return {following:this.followKey,selected:this.selectedKey,animating:!!this.animating,models:[...this.models.values()].map(m=>({id:m.id,uuid:m.object.uuid,shape:m.object.userData.shape,legs:m.object.userData.legCount,visible:m.object.visible,state:m.session.state,position:m.object.position.toArray(),progress:m.progress}))};}
 dispose(){this.clear();this.host.scene.remove(this.group);this.labels.remove();}
}

export class SpiderMechaController {
 constructor(universe) {
  this.universe=universe;this.scope=universe.scope;this.rows=new Map();this.appRows=new Map();this.apps=[];this.generation=0;this.selected=null;this.timer=null;this.abort=null;
  this.panel=document.getElementById('spider-mecha-panel');this.list=document.getElementById('spider-mecha-list');this.status=document.getElementById('spider-mecha-status');this.detail=document.getElementById('spider-mecha-detail');this.followButton=document.getElementById('spider-mecha-follow');
  this.ensureLayer();
  this.followButton.onclick=()=>{if(this.layer?.followKey)this.layer.stopFollowing();else if(this.selected)this.layer?.follow(this.selected);};
  this.visibility=()=>{if(this.isVisible())this.refresh();else{clearTimeout(this.timer);this.abort?.abort();++this.generation;this.layer?.stopFollowing();}};
  this.onVisibility=()=>this.visibility();document.addEventListener('visibilitychange',this.onVisibility);
  this.observer=new MutationObserver(this.onVisibility);this.observer.observe(document.getElementById('workspace-universe'),{attributes:true,attributeFilter:['hidden']});
  this.refresh();
 }
 ensureLayer(){
  if(!this.layer&&this.universe?.scene?.ready){
   this.layer=new SpiderMechaLayer(this.universe.scene,value=>this.updateFollow(value));
   this.universe.scene.spiders=this.layer;
  }
  return this.layer;
 }
 isVisible(){return !document.hidden&&!document.getElementById('workspace-universe').hidden;}
 setScope(scope){if(scope===this.scope)return;this.scope=scope;++this.generation;this.abort?.abort();clearTimeout(this.timer);this.clear();this.refresh();}
 clear(){this.apps=[];this.rows.clear();this.appRows.clear();this.list.replaceChildren();this.selected=null;this.detail.replaceChildren();delete this.detail.dataset.signature;this.layer?.clear();this.followButton.disabled=true;this.updateFollow(false);}
 updateFollow(value){this.followButton.setAttribute('aria-pressed',String(value));text(this.followButton,value?'退出跟随':'跟随视角');}
 async refresh() {
  clearTimeout(this.timer);if(!this.isVisible())return;this.abort?.abort();const abort=this.abort=new AbortController(),generation=++this.generation;
  const timeout=setTimeout(()=>abort.abort(),6000);
  try {const response=await fetch(`/api/spider-mecha?scope=${encodeURIComponent(this.scope)}`,{method:'GET',signal:abort.signal,cache:'no-store'});if(!response.ok)throw new Error('活动服务不可用');const snapshot=await response.json();if(!Array.isArray(snapshot.applications))throw new Error('快照格式不可用');if(generation!==this.generation)return;this.apps=snapshot.applications;this.ensureLayer()?.sync(this.apps);this.render();}
  catch(error){if(generation!==this.generation)return;this.clear();text(this.status,'无法获取机甲活动；稍后重试。');this.panel.dataset.state='error';}
  finally{clearTimeout(timeout);if(generation===this.generation&&this.isVisible())this.timer=setTimeout(()=>this.refresh(),8000);}
 }
 render() {
  const wanted=new Set(),wantedApps=new Set();this.panel.dataset.state=this.apps.length?'ready':'empty';text(this.status,this.apps.length?'真实 MCP 活动 · 仅观察':'暂无接入机甲活动');
  for(const app of this.apps){
   wantedApps.add(app.id);let group=this.appRows.get(app.id);
   if(!group){const element=document.createElement('section');element.className='spider-application';const title=document.createElement('h3');const content=document.createElement('div');const idle=document.createElement('p');idle.className='spider-empty';element.append(title,content,idle);this.list.append(element);group={element,title,content,idle};this.appRows.set(app.id,group);}
   text(group.title,app.name);const sessions=app.sessions||[];const allIdle=sessions.length?sessions.every(s=>s.state==='idle'):app.state==='idle';group.idle.hidden=!!sessions.length&&!allIdle;text(group.idle,allIdle?'空闲 · 已返回停靠点':STATES[app.state]||STATES.unknown);
   for(const session of sessions){const id=key(app,session);wanted.add(id);let row=this.rows.get(id);
    if(!row){const element=document.createElement('div');element.className='spider-session';element.dataset.spiderSession=session.id;const button=document.createElement('button');button.type='button';const name=document.createElement('span'),state=document.createElement('small'),location=document.createElement('small');button.append(name,state,location);element.append(button);group.content.append(element);row={element,button,name,state,location};this.rows.set(id,row);button.onclick=()=>this.select(id);}
    row.session=session;row.application=app;row.element.hidden=false;text(row.name,session.label||'未命名连接');const latest=(session.events||[]).at(-1);text(row.state,`${STATES[session.state]||STATES.unknown}${latest?` · ${ACTIONS[latest.action]||'资料访问'} ${STATUS[latest.status]||'状态未知'}`:''}${session.identity_kind==='connection'?' · MCP 连接（未登记会话）':''}`);
    const body=this.layer?.resolve(session.position),event=(session.events||[]).filter(e=>e.status==='completed').at(-1);
    const pathVisible=event?.from_topic_id&&this.universe.scene?.tractorCurves?.some(c=>(String(c.fromId)===String(event.from_topic_id)&&String(c.toId)===String(event.topic_id))||(String(c.toId)===String(event.from_topic_id)&&String(c.fromId)===String(event.topic_id)));
    const movement=session.mode==='crawl'?(pathVisible?'沿关联爬行':'关联路径未在当前视图显示'):'目标导航';
    const hasPosition=!!(session.position?.topic_id||session.position?.object_id);
    text(row.location,session.state==='idle'?(allIdle?'停靠点 · 投影已收起':'空闲投影已收起'):body?`${body.data.title||'当前行星'} · ${movement}`:hasPosition?'当前视图外 · 不切换星图':'尚无可定位资料');row.button.setAttribute('aria-pressed',String(this.selected===id));
   }
  }
  for(const [id,row]of this.rows)if(!wanted.has(id)){row.element.remove();this.rows.delete(id);}
  for(const [id,app]of this.appRows)if(!wantedApps.has(id)){app.element.remove();this.appRows.delete(id);}
  if(this.selected&&!this.rows.has(this.selected)){this.selected=null;this.layer?.stopFollowing();}
  this.renderDetail();
 }
 select(id){this.layer?.stopFollowing();this.selected=id;this.layer?.select(id);for(const [key,row]of this.rows)row.button.setAttribute('aria-pressed',String(key===id));this.renderDetail();}
 renderDetail(){
  const row=this.rows.get(this.selected);this.followButton.disabled=!this.universe.scene?.ready||!row||!this.layer?.resolve(row.session.position)||row.session.state==='idle';
  if(!row){text(this.detail,'选择会话投影，查看本次探索记录。');return;}
  const signature=JSON.stringify([this.selected,row.session.events]);if(this.detail.dataset.signature===signature)return;this.detail.dataset.signature=signature;this.detail.replaceChildren();
  const heading=document.createElement('p');text(heading,`${row.application.name} / ${row.session.label}`);this.detail.append(heading);
  const list=document.createElement('ol');list.className='spider-events';for(const event of (row.session.events||[]).slice(-8).reverse()){const item=document.createElement('li');text(item,`${ACTIONS[event.action]||'资料访问'} · ${STATUS[event.status]||'状态未知'}${event.object_id?` · ${event.kind||'资料'} ${String(event.object_id).slice(0,14)}`:''}${event.revision?` · 修订 ${event.revision}`:''}`);list.append(item);}if(!list.children.length){const p=document.createElement('p');text(p,'本次会话暂无探索记录。');this.detail.append(p);}else this.detail.append(list);
 }
 navigationChanged(){this.layer?.stopFollowing();this.render();}
 get debug(){return {scope:this.scope,applications:this.apps.length,selected:this.selected,visible:this.isVisible(),polling:!!this.timer};}
 dispose(){clearTimeout(this.timer);++this.generation;this.abort?.abort();this.observer.disconnect();document.removeEventListener('visibilitychange',this.onVisibility);this.layer?.dispose();}
}
