/** Human preferences and a one-shot retrieval intent, never a cached evidence body. */
export const controlKey = 'aiLibraryControls';
export const controlProtocol = 'ai-library-controls/1';
export const defaults = Object.freeze({ autoRead: true, maxContextChars: 6000, maxItems: 3, excerptChars: 480 });
export function parseSettings(value) {
  if (!value || typeof value !== 'object' || Object.keys(value).sort().join(',') !== 'autoRead,excerptChars,maxContextChars,maxItems'
    || typeof value.autoRead !== 'boolean') throw new TypeError('Invalid library settings');
  for (const [key, min, max] of [['maxContextChars',2000,40000],['maxItems',1,20],['excerptChars',40,4000]]) {
    if (!Number.isSafeInteger(value[key]) || value[key] < min || value[key] > max) throw new TypeError(`Invalid ${key}: ${min}..${max}`);
  }
  return {autoRead:value.autoRead,maxContextChars:value.maxContextChars,maxItems:value.maxItems,excerptChars:value.excerptChars};
}
function parseNext(value) {
  if (value === null) return null;
  if (!value || typeof value.ticket !== 'string' || !value.ticket || value.ticket.length > 100
    || typeof value.query !== 'string' || !value.query.trim() || value.query.length > 2000
    || (value.project_id !== null && (typeof value.project_id !== 'string' || !value.project_id || value.project_id.length > 200))
    || Object.keys(value).sort().join(',') !== 'project_id,query,ticket') throw new TypeError('Invalid queued retrieval');
  return { ...value };
}
export function createControlProjection(scope, initial = defaults) {
  initial = parseSettings(initial);
  function parse(value) {
    if (!value || Object.keys(value).sort().join(',') !== 'next,pending,settings' || !Array.isArray(value.pending)
      || value.pending.length > 32 || value.pending.some(id=>typeof id!=='string'||id.length>200)) throw new TypeError('Invalid controls state');
    return {settings:parseSettings(value.settings),next:parseNext(value.next),pending:[...value.pending]};
  }
  return { key:controlKey,stateVersion:1,stateSchema:{parse},init:()=>({settings:{...initial},next:null,pending:[]}),
    apply(state,event) {
      const d=event.data;
      if(event.type==='command/run'&&d.name==='library'&&d.source?.kind==='user') {
        try { const c=JSON.parse(d.args);
          if(!['configure','queue_preview','consume_preview','cancel_preview','select_project'].includes(c.operation))return state;
          return {...state,pending:[...state.pending,d.commandId].slice(-32)};
        } catch{return state;}
      }
      if(event.type!=='command/done'||!state.pending.includes(d.commandId))return state;
      const nextState={...state,pending:state.pending.filter(id=>id!==d.commandId)};
      if(d.kind!=='success')return nextState;
      try {
        const r=JSON.parse(d.text);
        if(r.scope!==scope)return nextState;
        if(r.protocol==='ai-library-project/1'&&r.operation==='select_project')return {...nextState,next:null};
        if(r.protocol!==controlProtocol)return nextState;
        if(r.operation==='configure')return {...nextState,settings:parseSettings(r.settings),next:null};
        if(r.operation==='queue_preview')return {...nextState,next:parseNext(r.next)};
        if(r.operation==='cancel_preview'||(r.operation==='consume_preview'&&r.ticket===state.next?.ticket))return {...nextState,next:null};
      }catch{}
      return nextState;
    },wire:{viewSchema:{parse(value){const result=parse({...value,pending:[]});return {settings:result.settings,next:result.next};}},view:state=>({settings:state.settings,next:state.next})},
  };
}
