// Presentation derives only from current scope metadata; it never rewrites content.
export const isPersonalNote = item => item.kind === 'note' && item.author === 'user';

export function shortCategoryTitle(title, maximum=18) {
  const full=String(title||'未命名').replace(/^【[^】]+】\s*/,'').trim();
  const label=full.split(/\s*[\\/]\s*/).filter(Boolean).at(-1)||full;
  return Array.from(label).length>maximum?Array.from(label).slice(0,maximum-1).join('')+'…':label;
}

export function sharedMembershipRelations(topics) {
  const owners=new Map(),pairs=new Map();
  for(const topic of topics) {
    for(const child of topic.children||[]) {
      if(child.kind==='topic')continue;
      const key=`${child.kind}:${child.id}`,set=owners.get(key)||new Set();
      set.add(topic.id);owners.set(key,set);
    }
  }
  for(const ids of owners.values()) {
    const values=[...ids].sort();
    for(let a=0;a<values.length;a++)for(let b=a+1;b<values.length;b++) {
      const key=JSON.stringify([values[a],values[b]]);
      pairs.set(key,(pairs.get(key)||0)+1);
    }
  }
  return [...pairs].map(([key,count])=>{const [from,to]=JSON.parse(key);return {from,to,count};});
}

export function personalNotesView(data) {
  const items=data.items.filter(isPersonalNote);
  return {...data,topics:[],items,total:items.length,
    sourceTotal:data.total,topic:{id:'__personal_notes',title:'我的笔记',virtual:true,personal:true},
    breadcrumbs:[{id:'__personal_notes',title:'我的笔记'}]};
}

export function viewItem(item) {
  const personal=isPersonalNote(item);
  return {...item,fullTitle:item.title,displayTitle:item.kind==='topic'?shortCategoryTitle(item.title):item.title,
    personal,modelRole:item.kind==='note'&&!personal?'source':undefined,
    clusterKey:item.kind==='memory'?`memory:${item.node_type||'未分类'}`:
      item.kind==='skill'||item.kind==='mcp'?item.kind:
      personal?'personal-notes':item.topic_ids?.[0]||'source-materials'};
}
