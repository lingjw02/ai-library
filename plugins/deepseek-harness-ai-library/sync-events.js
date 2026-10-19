const textOf=message=>(message?.content??[]).filter(row=>row.type==='text'&&typeof row.text==='string').map(row=>row.text).join('\n');
const bounded=text=>({text:text.slice(0,8000),text_truncated:text.length>8000});

/** Translate only newly committed native events; captured project ownership cannot drift. */
export class NativeSyncEvents {
  constructor(){this.chats=new Map();}
  forget(sessionId){this.chats.delete(sessionId);}
  capture(sessionId,event,projectId) {
    if(typeof sessionId!=='string'||!sessionId||sessionId.length>200||!Number.isSafeInteger(event?.seq))return [];
    if(!this.chats.has(sessionId)){
      if(!['turn/start','tool/call','tool/result','turn/end'].includes(event.type))return [];
      if(this.chats.size>=512)throw new Error('Sync active chat limit exceeded');
      this.chats.set(sessionId,{turn:null,calls:new Map()});
    }
    const state=this.chats.get(sessionId),data=event.data??{};
    if(event.type==='turn/start') {
      state.turn={id:'turn:'+data.turn,number:data.turn,projectId,changed:false,users:[],reply:null};state.calls.clear();return [];
    }
    const turn=state.turn;
    if(turn&&turn.projectId!==projectId)turn.changed=true;
    const frame=(type,seq=event.seq)=>({operation:'sync_event',session_key:sessionId,event_id:'seq:'+seq,
      event_type:type,turn_id:turn?.id??('turn:'+data.turn),project_id:turn?turn.projectId:projectId});
    if(event.type==='user/message'&&turn&&data.source?.kind==='user'
      &&(event.surfaceOp===undefined||event.surfaceOp==='append')) {
      const text=textOf(data);
      if(text.trim()&&!/^\s*\/library(?:\s|$)/i.test(text)&&turn.users.length<32)turn.users.push({seq:event.seq,...bounded(text)});
    } else if(event.type==='assistant/message'&&turn) {
      turn.reply=null;
      const message=data.message;
      if(!data.interrupted&&(event.surfaceOp===undefined||event.surfaceOp==='append')&&message?.source?.kind!=='ai-library'
        &&!(message?.content??[]).some(row=>row.type==='tool-call')) {
        const text=textOf(message);if(text.trim())turn.reply={seq:event.seq,...bounded(text)};
      }
    } else if(event.type==='tool/call') {
      const name=String(data.name??'tool').slice(0,120);
      if(state.calls.size>=64)state.calls.delete(state.calls.keys().next().value);
      state.calls.set(data.callId,name);return [{...frame('tool_started'),tool_name:name}];
    } else if(event.type==='tool/result') {
      const id=data.message?.toolCallId??data.message?.source?.callId;
      const name=state.calls.get(id)??'tool';state.calls.delete(id);
      return [{...frame(data.message?.isError||data.error?'tool_failed':'tool_completed'),tool_name:name}];
    } else if(event.type==='turn/end') {
      const frames=[];
      if(turn&&data.turn===turn.number&&data.reason?.kind==='completed'&&!turn.changed&&turn.projectId&&turn.users.length&&turn.reply) {
        for(const user of turn.users)frames.push({...frame('user_message',user.seq),text:user.text,text_truncated:user.text_truncated});
        frames.push({...frame('assistant_message',turn.reply.seq),text:turn.reply.text,text_truncated:turn.reply.text_truncated});
        frames.push(frame('turn_completed'));
      } else frames.push(frame('turn_finished'));
      state.turn=null;state.calls.clear();return frames;
    }
    return [];
  }
}
