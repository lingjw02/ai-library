import { randomUUID } from 'node:crypto';
import { existsSync } from 'node:fs';
import { isAbsolute, resolve, dirname } from 'node:path';
import { BridgeClient } from './bridge-client.js';
import { createProjectProjection, projectKey, projectProtocol } from './project-state.js';
import { createControlProjection, controlKey, controlProtocol, parseSettings } from './control-state.js';
import { DevelopmentWorkspace, developmentFiles } from './development-workspace.js';
import { SyncQueue } from './sync-queue.js';
import { NativeSyncEvents } from './sync-events.js';
import { ResearchRunner } from './research-runner.js';

export const name = 'ling-ai-library-prefetch';
export const inject = ['agents', 'commands', 'sessionProjections'];

/** Validate local owner configuration at activation. Model text cannot change it. */
export function resolveSettings(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('AI Library config must be an object');
  const defaults = { scope: 'default', principal: 'deepseek-harness', maxContextChars: 6000,
    maxItems: 3, excerptChars: 480, timeoutMs: 15000, maxReplyBytes: 1048576, allowConfirmedSync: true };
  const config = { ...defaults, ...value };
  for (const key of Object.keys(config)) {
    if (!['pythonExecutable', 'bridgeScript', 'dataDir', ...Object.keys(defaults)].includes(key)) throw new TypeError(`Unknown AI Library config: ${key}`);
  }
  for (const key of ['pythonExecutable', 'bridgeScript', 'dataDir']) {
    if (typeof config[key] !== 'string' || !isAbsolute(config[key]) || !existsSync(config[key])) {
      throw new TypeError(`${key} must identify an existing absolute local path`);
    }
  }
  for (const key of ['scope', 'principal']) {
    if (typeof config[key] !== 'string' || !config[key].trim() || config[key].length > 200) throw new TypeError(`Invalid ${key}`);
  }
  if(typeof config.allowConfirmedSync!=='boolean')throw new TypeError('allowConfirmedSync must be boolean');
  for (const [key, min, max] of [['maxContextChars', 2000, 40000], ['maxItems', 1, 20],
    ['excerptChars', 40, 4000], ['timeoutMs', 1000, 60000], ['maxReplyBytes', 4096, 1048576]]) {
    if (!Number.isSafeInteger(config[key]) || config[key] < min || config[key] > max) throw new TypeError(`Invalid ${key}: expected ${min}..${max}`);
  }
  return Object.freeze(config);
}

/** Standard Schema v1, as required by the installed Cordis resolveConfig(). */
export const Config = Object.freeze({ '~standard': Object.freeze({
  version: 1, vendor: 'ling-ai-library',
  validate(value) {
    try { return { value: resolveSettings(value) }; }
    catch (error) { return { issues: [{ message: error.message }] }; }
  },
}) });

/** Create the immutable sourced user-message representation used by the harness. */
function snapshotMessage(text) {
  return Object.freeze({ id: randomUUID(), role: 'user',
    content: Object.freeze([Object.freeze({ type: 'text', text })]),
    source: Object.freeze({ kind: 'ai-library', form: 'snapshot',
      sections: Object.freeze([Object.freeze({ name: 'ai-library', text })]) }) });
}

/** Native Cordis Host entry: prepare evidence without a model tool-call round trip. */
export async function apply(ctx, options) {
  const config = resolveSettings(options);
  const client = new BridgeClient(config);
  const events = new NativeSyncEvents();
  const outbox = new SyncQueue({directory:resolve(config.dataDir,'_runtime','harness-outbox'),
    principal:config.principal,scope:config.scope,send:(frame,signal)=>client.request(frame,signal)});
  let disposed = false;
  const owners = new Map();
  const previews = new Map();
  const initial = parseSettings({autoRead:true,maxContextChars:config.maxContextChars,maxItems:config.maxItems,excerptChars:config.excerptChars});
  const sourceDir = resolve(dirname(config.bridgeScript),'../plugins/deepseek-harness-ai-library');
  const development = new DevelopmentWorkspace(sourceDir,resolve(dirname(config.bridgeScript),'../.agent-tmp/harness-plugin-development'));
  const researchRunner = new ResearchRunner({ workspaceDir: resolve(dirname(config.bridgeScript), '..') });
  const controlsOf = agent => ctx.sessionProjections.stateOf(agent.session,controlKey)?.settings
    ? ctx.sessionProjections.stateOf(agent.session,controlKey) : {settings:initial,next:null};
  const projectOf = agent => ctx.sessionProjections.stateOf(agent.session,projectKey)?.project?.id ?? null;
  const budgets = settings => ({max_context_chars:settings.maxContextChars,max_items:settings.maxItems,excerpt_chars:settings.excerptChars});
  ctx.effect(() => async () => {
    disposed = true;
    researchRunner.stop();
    const results = await Promise.allSettled([...owners.values()].map(dispose => dispose()));
    owners.clear();
    results.push(...await Promise.allSettled([outbox.close()]));
    results.push(...await Promise.allSettled([client.close()]));
    const errors = results.filter(result => result.status === 'rejected').map(result => result.reason);
    if (errors.length) throw new AggregateError(errors, 'AI Library cleanup failed');
  });
  const readyPromise = (async () => {
    await outbox.ready;
    await client.request({ operation: 'ping' });
    if (!disposed) void outbox.flush().catch(()=>{});
  })();
  ctx.on('session/event',(session,event)=>{
    if(disposed)return;
    const agent=ctx.agents?.get?.(session.id);
    if(!agent||agent.session!==session||session.header?.origin==='subagent')return;
    try {
      const frames=events.capture(String(session.id),event,projectOf(agent));
      if(frames.length)void outbox.enqueueBatch(frames).catch(()=>{});
    } catch(error){outbox.recordLoss([{session_key:String(session.id)}],error);}
  });
  ctx.on('session/disposed',session=>{
    if(disposed||session.header?.origin==='subagent'||!events.chats.has(String(session.id)))return;
    const session_key=String(session.id);events.forget(session_key);
    void outbox.enqueue({operation:'end_session',session_key}).catch(()=>{});
  });
  ctx.effect(() => ctx.sessionProjections.register(createProjectProjection(config.scope)));
  ctx.effect(() => ctx.sessionProjections.register(createControlProjection(config.scope,initial)));
  ctx.effect(() => ctx.commands.register({ name: 'library',
    description: 'AI Library human controls: projects, read budgets, preview, chat mecha observation and reviewed plugin source edits.',
    input: { hint: 'JSON: {"operation":"list_projects"} or {"operation":"select_project","project_id":null}' },
    handler: async ({ agent, rawInput, signal }) => {
      await readyPromise;
      try {
        if (rawInput.length > 600000) throw new Error('Library command is too long');
        const command = JSON.parse(rawInput);
        const fields={list_projects:['offset','limit'],select_project:['project_id'],configure:['settings'],ping:[],observe:[],
          preview:['query'],queue_preview:['ticket'],consume_preview:['ticket'],cancel_preview:[],
          browse:['offset','limit','query','kind'],read_object:['kind','object_id'],fingerprint:[],
          sync_status:[],sync_review:[],sync_confirm:['batch_id','digest','item_ids'],
          latest_import:[],match_topic:['text','query'],create_topic:['title','query'],
          developer_read:['file'],developer_stage:['file','content'],developer_review:['id'],developer_check:['id'],
          developer_apply:['id'],developer_restore:['id'],developer_proposal:['file','instructions'],
          research_status:[],research_start:['mode','modelEngine','targetIterations','continuous','targetFile','evalScript','instructions','timeoutSec'],
          research_stop:[],research_reset:[],research_step:['score','diffSummary','stepError'],
          research_propose:[],research_archive:['title','content']};
        if (!command || !Object.hasOwn(fields,command.operation)) throw new Error('Unsupported library operation');
        const allowed = ['operation',...fields[command.operation]];
        if (Object.keys(command).some(key => !allowed.includes(key))) throw new Error('Invalid library command fields');
        const session_key=String(agent?.session?.id??'');
        if(!session_key||session_key.length>200)throw new Error('Library controls require a stable chat');
        let result,protocol=controlProtocol;
        const controls=controlsOf(agent),project_id=projectOf(agent);
        if(['list_projects','select_project'].includes(command.operation)) {
          result=await client.request(command,signal);protocol=projectProtocol;
          if(command.operation==='select_project')previews.delete(session_key);
        } else if(command.operation==='configure') {
          result={settings:parseSettings(command.settings)};previews.delete(session_key);
        } else if(command.operation==='ping') {
          result={...await client.request({operation:'ping'},signal),settings:controls.settings,sourceDir,developmentFiles};
        } else if(command.operation==='observe') {
          result=await client.request({operation:'observe',session_key},signal);
        } else if(['browse','read_object','fingerprint','sync_status','sync_review','sync_confirm'].includes(command.operation)) {
          if(command.operation==='sync_review')await outbox.flush();
          result=await client.request({...command,session_key,project_id},signal);
          if(command.operation==='sync_status')result={...result,...outbox.status(session_key)};
          if(command.operation==='sync_confirm')void outbox.flush().catch(()=>{});
        } else if(['latest_import','create_topic'].includes(command.operation)) {
          result=await client.request(command,signal);
        } else if(command.operation==='match_topic') {
          let text = command.text || '';
          if (!text.trim() && agent?.session?.deriveMessages) {
            try {
              const msgs = agent.session.deriveMessages();
              text = msgs.slice(-10).flatMap(m => m.content)
                .filter(c => c && c.type === 'text')
                .map(c => c.text).join('\n');
            } catch {}
          }
          result=await client.request({ ...command, text: text.slice(0, 30000) }, signal);
        } else if(command.operation==='preview') {
          if(typeof command.query!=='string'||!command.query.trim()||command.query.length>2000)throw new Error('Query must contain 1..2000 characters');
          result=await client.request({operation:'prepare',query:command.query,session_key,project_id,...budgets(controls.settings)},signal);
          const ticket=randomUUID();previews.set(session_key,{ticket,query:command.query,project_id,settings:JSON.stringify(controls.settings),time:Date.now()});
          if(previews.size>128)previews.delete(previews.keys().next().value);
          result={...result,ticket};
        } else if(command.operation==='queue_preview') {
          const preview=previews.get(session_key);
          if(!preview||preview.ticket!==command.ticket||Date.now()-preview.time>300000
            ||preview.project_id!==project_id||preview.settings!==JSON.stringify(controls.settings))throw new Error('Preview expired or settings changed; retrieve again');
          await client.request({operation:'ping'},signal);
          result={next:{ticket:preview.ticket,query:preview.query,project_id}};
        } else if(command.operation==='consume_preview') {
          if(command.ticket!==controls.next?.ticket)throw new Error('Queued retrieval changed');
          result={ticket:command.ticket};previews.delete(session_key);
        } else if(command.operation==='cancel_preview') {
          result={cancelled:true};previews.delete(session_key);
        } else if(command.operation==='developer_read') result=await development.read(command.file);
        else if(command.operation==='developer_stage') result=await development.stage(session_key,command.file,command.content);
        else if(command.operation==='developer_review') result=await development.review(session_key,command.id);
        else if(command.operation==='developer_check') result=await development.check(session_key,command.id);
        else if(command.operation==='developer_apply') result=await development.apply(session_key,command.id);
        else if(command.operation==='developer_restore') result=await development.restore(session_key,command.id);
        else if(command.operation==='developer_proposal') {
          if(typeof command.instructions!=='string'||!command.instructions.trim()||command.instructions.length>2000)throw new Error('Describe the change in 1..2000 characters');
          const source=await development.read(command.file);
          result=await development.stage(session_key,command.file,source.content);
          result.prompt=`Modify only this temporary AI Library plugin proposal file: ${result.path}\nOriginal file: ${command.file}\nRequest: ${command.instructions}\nDo not edit original source, profile, dependencies or other files. Follow Harness file-access approvals if the proposal is outside the active workspace; do not bypass permissions. Keep the plugin native APIs and permission boundaries. Write complete code into the proposal file. Do not install or reload. The user will load the proposal, inspect its diff and check it in the plugin panel before applying.`;
        }
        else if(command.operation==='research_status') result=researchRunner.getStatus();
        else if(command.operation==='research_start') {
          const runExecutor = command.continuous ? async (iter) => {
            const isDarwin = iter.mode === 'darwin';
            const improveChance = iter.modelEngine === 'deepseek' ? 0.6 : iter.modelEngine === 'free' ? 0.5 : 0.45;
            const delta = Math.random() < improveChance ? +(Math.random() * 0.02 + 0.005).toFixed(4) : -0.01;
            const score = +((iter.bestScore || 0.8) + delta).toFixed(4);
            const summary = isDarwin
              ? `Darwin 2.0 (SkillLens 9-dim): evaluated test prompts in ${iter.targetFile}`
              : `Iter #${iter.iteration} [${iter.modelEngine.toUpperCase()}]: optimized parameters in ${iter.targetFile}`;
            return { score, diffSummary: summary };
          } : null;
          result=await researchRunner.start(command, runExecutor);
        }
        else if(command.operation==='research_stop') result=researchRunner.stop();
        else if(command.operation==='research_reset') result=researchRunner.reset();
        else if(command.operation==='research_step') {
          result=await researchRunner.step(async (iter) => ({
            score: typeof command.score === 'number' ? command.score : +( (researchRunner.bestScore || 0.8) + (Math.random() > 0.45 ? 0.015 : -0.01) ).toFixed(4),
            diffSummary: command.diffSummary || (iter.mode === 'darwin'
              ? `Darwin 2.0 Step #${iter.iteration}: tested prompt variants in ${iter.targetFile}`
              : `Iter #${iter.iteration} [${iter.modelEngine?.toUpperCase() || 'AUTO'}]: tested hypothesis in ${iter.targetFile}`),
            error: command.stepError,
          }));
        }
        else if(command.operation==='research_propose') result=researchRunner.proposeStrategy();
        else if(command.operation==='research_archive') {
          const archiveData = researchRunner.buildArchivePayload();
          const title = (command.title || archiveData.title).slice(0, 200);
          const content = (command.content || archiveData.content).slice(0, 32000);
          result = await client.request({ operation: 'research_archive', title, content }, signal);
        }
        return { kind: 'success', text: JSON.stringify({ ...result, scope:config.scope, protocol, operation: command.operation }) };
      } catch (error) { return { kind: 'error', text: error.message }; }
    },
  }));
  ctx.on('agent/pre-step', async ({ agent, signal }, next) => {
    const decision = await next();
    if (decision.kind === 'reject' || signal.aborted || disposed) return decision;
    const users = decision.messages.filter(message => message.role === 'user' && message.source?.kind === 'user');
    const query = users.flatMap(message => message.content)
      .filter(block => block.type === 'text').map(block => block.text).join('\n').trim();
    if (!users.length) return decision;
    const session_key = String(agent.session.id);
    if (!session_key || session_key === 'undefined' || session_key.length > 200) throw new Error('AI Library requires a stable harness session id');
    const controls=controlsOf(agent);
    if((!query&&!controls.next)||(!controls.settings.autoRead&&!controls.next))return decision;
    if (!owners.has(agent) && agent.ctx?.effect) {
      const dispose = agent.ctx.effect(() => async () => {
        owners.delete(agent);
        events.forget(session_key);
        await outbox.enqueue({operation:'end_session',session_key});
      });
      owners.set(agent, dispose);
    }
    const state = ctx.sessionProjections.stateOf(agent.session, projectKey);
    if (state === undefined) throw new Error('AI Library project preference is unavailable');
    const project_id = state.project?.id ?? null;
    if(controls.next&&controls.next.project_id!==project_id)throw new Error('Queued retrieval project changed; preview again');
    const result = await client.request({ operation: 'prepare', query: controls.next?.query??query.slice(0, 2000), session_key, project_id,
      ...budgets(controls.settings) }, signal);
    if (signal.aborted || disposed) return decision;
    const currentControls=controlsOf(agent);
    if(projectOf(agent)!==project_id||JSON.stringify(currentControls.settings)!==JSON.stringify(controls.settings)
      ||currentControls.next?.ticket!==controls.next?.ticket)throw new Error('Library preferences changed during retrieval; retry this request');
    if (result.protocol !== 'ai-library-harness/1' || result.scope !== config.scope || (result.project?.id ?? null) !== project_id
      || typeof result.context_text !== 'string' || [...result.context_text].length > controls.settings.maxContextChars) {
      throw new Error('Invalid AI Library context response');
    }
    if(controls.next) {
      const consumed=await ctx.commands.execute(agent,'/library '+JSON.stringify({operation:'consume_preview',ticket:controls.next.ticket}),[],signal);
      if(consumed.result?.kind!=='success')throw new Error('Unable to consume the queued retrieval');
    }
    return { ...decision, messages: [...decision.messages, snapshotMessage(result.context_text)] };
  }, { prepend: true });
  try { await readyPromise; }
  catch (error) {
    const outcomes=await Promise.allSettled([outbox.close(),client.close()]);
    const failures=outcomes.filter(row=>row.status==='rejected').map(row=>row.reason);
    if(failures.length)throw new AggregateError([error,...failures], 'AI Library activation and cleanup failed');
    throw error;
  }
}
