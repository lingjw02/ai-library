// Pure presentation tests: no DOM, API writes, catalogue replacement, or inferred facts.
import assert from 'node:assert/strict';
import {isPersonalNote, personalNotesView, sharedMembershipRelations, shortCategoryTitle, viewItem} from '../frontend/js/universe-views.js';

const checks=[];
function check(name, fn) { fn(); checks.push(name); }
function frozen(value) { for (const child of Object.values(value)) if (child && typeof child==='object') frozen(child); return Object.freeze(value); }

check('personal author is exact and unknown/imported authors stay out',()=>{
  assert.equal(isPersonalNote({kind:'note',author:'user'}),true);
  for (const author of [undefined,null,'','USER','User','user:agent','mcp-agent:history-import','agent']) assert.equal(isPersonalNote({kind:'note',author}),false);
  assert.equal(isPersonalNote({kind:'memory',author:'user'}),false);
});

check('personal hub derives a view without changing source content or provenance',()=>{
  const data=frozen({total:4,topic:null,topics:[{id:'project',title:'Project'}],counts:{notes:3,memories:1},items:[
    {id:'personal',kind:'note',author:'user',title:'完整原文标题',content:'  original\n',source:{revision:3}},
    {id:'imported',kind:'note',author:'mcp-agent:history-import',content:'imported original',topic_ids:['project']},
    {id:'unknown',kind:'note',content:'unknown original'},
    {id:'memory',kind:'memory',author:'user',content:'memory original'}
  ]});
  const before=JSON.stringify(data),result=personalNotesView(data);
  assert.notEqual(result,data);
  assert.deepEqual(result.items,[data.items[0]]);
  assert.equal(result.items[0],data.items[0]);
  assert.equal(result.total,1);
  assert.equal(result.sourceTotal,4);
  assert.deepEqual(result.topics,[]);
  assert.equal(result.topic.id,'__personal_notes');
  assert.equal(result.topic.virtual,true);
  assert.equal(JSON.stringify(data),before);
});

check('typed identities do not invent note-memory shared membership',()=>{
  assert.deepEqual(sharedMembershipRelations([
    {id:'A',children:[{id:'same-id',kind:'note'}]},
    {id:'B',children:[{id:'same-id',kind:'memory'}]}
  ]),[]);
});

check('duplicate occurrence in one topic counts once and topic previews are not content',()=>{
  const topics=frozen([
    {id:'B',children:[{id:'shared',kind:'note'},{id:'shared',kind:'note'},{id:'second',kind:'memory'},{id:'nested',kind:'topic'}]},
    {id:'A',children:[{id:'shared',kind:'note'},{id:'second',kind:'memory'},{id:'nested',kind:'topic'}]},
    {id:'C',children:[{id:'shared',kind:'note'}]}
  ]);
  const before=JSON.stringify(topics);
  assert.deepEqual(sharedMembershipRelations(topics).sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b))),[
    {from:'A',to:'B',count:2},{from:'A',to:'C',count:1},{from:'B',to:'C',count:1}
  ]);
  assert.equal(JSON.stringify(topics),before);
});

check('similar titles and nearby categories never produce semantic relations',()=>{
  assert.deepEqual(sharedMembershipRelations([
    {id:'A',title:'AI Knowledge Project',children:[{kind:'note',id:'1',title:'shared words'}]},
    {id:'B',title:'AI Knowledge Project 2',children:[{kind:'note',id:'2',title:'shared words'}]}
  ]),[]);
  assert.deepEqual(sharedMembershipRelations([{id:'empty',title:'AI Knowledge Project'}]),[]);
});

check('short labels preserve Unicode code points, full titles remain accessible',()=>{
  assert.equal(shortCategoryTitle('【来源】 folder / 项目标题'),'项目标题');
  assert.equal(shortCategoryTitle('folder\\child'),'child');
  assert.equal(shortCategoryTitle('😀😀😀😀😀',4),'😀😀😀…');
  assert.equal(shortCategoryTitle('中文甲乙丙丁',4),'中文甲…');
  assert.equal(shortCategoryTitle('',18),'未命名');
  const original=frozen({id:'category',kind:'topic',title:'【来源】 folder / 很长的完整分类标题保留给目录访问与辅助识别'});
  const result=viewItem(original);
  assert.equal(result.fullTitle,original.title);
  assert.equal(result.title,original.title);
  assert.ok(Array.from(result.displayTitle).length<=18);
});

check('personal, imported, memory, Skill and MCP have bounded presentation roles',()=>{
  assert.equal(viewItem({kind:'note',author:'user',id:'u'}).clusterKey,'personal-notes');
  const source=viewItem({kind:'note',author:'mcp-agent:history-import',id:'i',topic_ids:['project'],provenance:{source_id:'raw'}});
  assert.equal(source.modelRole,'source');
  assert.equal(source.clusterKey,'project');
  assert.deepEqual(source.provenance,{source_id:'raw'});
  assert.equal(viewItem({kind:'memory',id:'m',node_type:'Fact'}).clusterKey,'memory:Fact');
  for (const kind of ['skill','mcp']) assert.equal(viewItem({kind,id:kind}).clusterKey,kind);
});

console.log(JSON.stringify({state:'PASS',checks},null,2));
