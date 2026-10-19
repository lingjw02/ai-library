window.__ModuleLoader__.load({
  id: '@ling/ai-library-harness',
  factory(require) {
    const React = require('react');
    const h = React.createElement;
    const namespace = 'aiLibrary';
    // This stylesheet belongs to the registered slot and leaves with it on unload.
    const islandStyles = `
      .ail-island { --ail-top:max(var(--dsh-frame-overlay-top, 12px), env(safe-area-inset-top));
        --ail-spring:cubic-bezier(0.32, 0.72, 0, 1);
        --ail-ease:cubic-bezier(0.23, 1, 0.32, 1);
        position:fixed; top:var(--ail-top); left:50%;
        transform:translateX(-50%); width:min(680px, calc(100vw - 24px)); pointer-events:none;
        font:13px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        color:#eef1f5; color-scheme:dark;
        --dsw-alias-label-primary:#eef1f5; --dsw-alias-border-l:#2a3441;
        --dsw-alias-interactive-bg-hover-solid:#151e29; }
      .ail-island * { box-sizing:border-box; }
      .ail-island__surface { width:min(392px, 100%); margin:auto; pointer-events:auto;
        display:flex; flex-direction:column; max-height:calc(100dvh - var(--ail-top) - 12px);
        background:rgba(11, 16, 23, 0.88);
        backdrop-filter:blur(28px) saturate(190%); -webkit-backdrop-filter:blur(28px) saturate(190%);
        border:1px solid rgba(255, 255, 255, 0.08); border-radius:32px;
        box-shadow:0 20px 48px -8px rgba(0, 0, 0, 0.65), 0 0 0 1px rgba(255, 255, 255, 0.05) inset;
        overflow:hidden; -webkit-app-region:no-drag;
        transition:width 260ms var(--ail-spring), border-radius 260ms var(--ail-spring), box-shadow 240ms ease; }
      .ail-island[data-expanded="true"] .ail-island__surface { width:100%; border-radius:24px;
        box-shadow:0 28px 64px -12px rgba(0, 0, 0, 0.8), 0 0 0 1px rgba(149, 220, 217, 0.15) inset; }
      .ail-island__toggle { display:flex; align-items:center; gap:12px; width:100%; min-height:58px;
        flex-shrink:0; padding:10px 18px; border:0; border-radius:inherit; background:transparent;
        color:inherit; text-align:left; cursor:pointer; font:inherit;
        transition:background-color 140ms ease; }
      .ail-island__toggle:hover { background-color:rgba(255, 255, 255, 0.03); }
      .ail-island__toggle:active { transform:scale(0.985); }
      .ail-island__title { min-width:0; flex:1; display:grid; gap:2px; }
      .ail-island__title strong { font-size:12px; letter-spacing:.08em; font-weight:700; color:#fff; }
      .ail-island__project { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#a6b2c1; font-size:12px; }
      .ail-island__status { text-align:right; font-size:11px; color:#8e9cae; white-space:nowrap; line-height:1.4; }
      .ail-island__status [data-online="true"] { color:#95dcd9; font-weight:600; text-shadow:0 0 8px rgba(149, 220, 217, 0.4); }
      .ail-island__chevron { width:14px; height:14px; flex:none; opacity:0.7; transition:transform 220ms var(--ail-spring), opacity 140ms ease; }
      .ail-island__toggle:hover .ail-island__chevron { opacity:1; }
      .ail-island[data-expanded="true"] .ail-island__chevron { transform:rotate(180deg); }
      .ail-island__content { min-height:0; max-height:min(74dvh, 740px); overflow:auto; overscroll-behavior:contain;
        border-top:1px solid rgba(255, 255, 255, 0.06); padding:18px;
        animation:ail-island-enter 220ms var(--ail-spring); }
      .ail-island [hidden] { display:none !important; }
      .ail-island__project-controls { display:flex; flex-wrap:wrap; gap:8px; align-items:center;
        padding-bottom:14px; margin-bottom:14px; border-bottom:1px solid rgba(255, 255, 255, 0.06); }
      .ail-island button, .ail-island select, .ail-island input:not([type="checkbox"]) { min-height:40px; border-radius:8px; }
      .ail-island button { cursor:pointer; font-weight:500; transition:transform 120ms var(--ail-ease), background-color 140ms ease, border-color 140ms ease, box-shadow 140ms ease; }
      .ail-island button:hover:not(:disabled) { transform:translateY(-1px); }
      .ail-island button:active:not(:disabled) { transform:scale(0.97) translateY(0); }
      .ail-island button:disabled { cursor:default; opacity:.45; transform:none !important; }
      .ail-island :is(button, input, select, textarea):focus-visible { outline:2px solid #95dcd9; outline-offset:2px; }
      .ail-island input[type="checkbox"] { width:18px; height:18px; accent-color:#95dcd9; cursor:pointer; }
      .ail-island label { min-height:40px; }
      .ail-island select, .ail-island textarea, .ail-island input { max-width:100%; border-radius:8px; }
      .ail-island small { color:#8e9cae; }
      .ail-island article { border-radius:12px; background:rgba(17, 25, 35, 0.7); border:1px solid rgba(255, 255, 255, 0.05); }
      .ail-island nav { display:flex; gap:6px; flex-wrap:wrap; }
      .ail-island nav button { border:1px solid rgba(255, 255, 255, 0.08) !important; background:rgba(21, 30, 41, 0.6) !important; border-radius:20px !important; padding:4px 14px !important; min-height:34px !important; }
      .ail-island nav button[aria-pressed="true"] { border-color:#95dcd9 !important; background:rgba(25, 48, 51, 0.85) !important; color:#95dcd9 !important; box-shadow:0 0 12px rgba(149, 220, 217, 0.2); }
      .ail-island [role="alert"] { color:#ffb7b0; overflow-wrap:anywhere; }
      .ail-island__badge { font-size:11px; font-weight:500; color:#95dcd9; background:rgba(22, 42, 45, 0.75); padding:3px 8px; border-radius:20px; border:1px solid rgba(149, 220, 217, 0.35); display:inline-flex; align-items:center; gap:4px; margin-top:3px; max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; text-shadow:0 0 6px rgba(149, 220, 217, 0.3); }
      .ail-island__rec-box { background:linear-gradient(145deg, rgba(17, 25, 35, 0.85) 0%, rgba(13, 20, 29, 0.95) 100%); border:1px solid rgba(149, 220, 217, 0.18); border-radius:14px; padding:14px; margin-bottom:14px; font-size:12px; box-shadow:0 6px 20px rgba(0, 0, 0, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.06); }
      .ail-island__rec-title { font-weight:600; color:#95dcd9; display:flex; align-items:center; justify-content:space-between; margin-bottom:8px; font-size:12.5px; letter-spacing:0.02em; }
      .ail-island__pulse { transform-origin:center; animation:ail-pulse 2.6s ease-in-out infinite; }
      @keyframes ail-pulse { 0%, 100% { opacity:0.95; transform:scale(1); } 50% { opacity:0.4; transform:scale(0.85); } }
      @keyframes ail-island-enter { from { opacity:0; transform:translateY(-8px) scale(0.98); } to { opacity:1; transform:translateY(0) scale(1); } }
      @media (max-width:480px) { .ail-island__content { padding:12px; } .ail-island__toggle { gap:8px; padding:9px 12px; }
        .ail-island__project-controls select { flex:1; min-width:0; } }
      @media (prefers-reduced-motion:reduce) { .ail-island *, .ail-island__content { animation:none !important; transition:none !important; } }
    `;
    const dictionaries = {
      en: { project: 'Library project', all: 'Entire AI Library', refresh: 'Refresh projects',
        more: 'Load more projects', loading: 'Loading…', saving: 'Saving…',
        missing: 'Selected project unavailable', next: 'Used for the next retrieval', unavailable: 'Library plugin unavailable' },
      zh: { project: '资料项目', all: '整个 AI Library', refresh: '刷新项目',
        more: '加载更多项目', loading: '加载中…', saving: '保存中…',
        missing: '所选项目不可用', next: '下次读取使用此范围', unavailable: '资料库插件不可用' },
    };
    Object.assign(dictionaries.zh,{panel:'控制面板',library:'资料',mecha:'机甲观察',developer:'插件开发',auto:'自动检索',
      chars:'上下文上限（字符）',items:'证据条数',excerpt:'每条摘要上限（字符）',save:'保存读取设置',query:'检索内容',
      preview:'手动预览',use:'用于下一次回答',cancel:'取消单次使用',queued:'下一次回答将重新核对并读取此检索',
      test:'连接测试',connected:'资料库连接正常 · 只读',budgetNote:'字符不等于模型 token；预览不调用模型。完整使用规则占用部分预算。',
      previewNote:'预览仅供查看；点击用于下一次回答后会重新读取，避免使用过期或撤权内容。',
      emptyMecha:'尚未登记机甲；首次读取资料后显示',location:'当前位置',state:'状态',action:'最近动作',
      idle:'空闲',active:'读取中',disconnected:'连接中断',read:'读取',search:'检索',completed:'完成',started:'开始',failed:'失败',
      observeNote:'只观察当前聊天，不控制机甲。隐藏此区域后停止轮询。',probe:'当前聊天机甲示意图',
      file:'插件文件',source:'源码',readFile:'读取文件',check:'检查并预览差异',apply:'应用源码修改',restore:'恢复修改前源码',
      request:'开发需求',propose:'生成开发提案并放入输入框',loadProposal:'读取 AI 修改后的提案',install:'安装 / 更新新版',
      syntax:'语法检查通过；尚未运行修改后的代码',syntaxFailed:'语法检查失败',sourceSaved:'源码已保存，宿主尚未加载新版',
      restored:'源码已恢复，宿主尚未加载恢复版',draftReady:'开发请求已放入输入框，请检查后发送；AI 仅修改临时副本',
      draftChanged:'输入框已变化，未覆盖草稿；请重新生成请求',confirmApply:'确认保存源码',confirmRestore:'确认恢复源码',
      confirmInstall:'确认安装新版',dismiss:'取消',applyNote:'仅保存本插件源码；保留修改前备份。不会自动安装。',
      installNote:'安装会更新当前 profile，影响其中全部聊天。修改后的 Host 代码将以宿主权限运行。',
      restart:'源码包已更新，需要重启 Harness 才能加载新版',applied:'宿主报告已应用，请核对加载版本',
      pending:'宿主尚未应用；请查看返回的诊断',developerNote:'只编辑本插件白名单文件。检查为语法检查；应用后仍需行为验证。',
      noInput:'原生输入框操作不可用',noManager:'原生插件管理服务不可用',noEvidence:'没有找到相关证据',
      saveOkay:'读取设置已保存',cancelOkay:'已取消单次使用',notReady:'插件尚未就绪',partial:'结果包含摘要或不完整内容',
      full:'当前结果未标记为不完整',characters:'字符',evidence:'条证据',backup:'修改前备份',truncated:'差异过长，当前显示部分内容',open:'显示控制面板',close:'隐藏控制面板',
      research:'自主研究',modeLibrary:'知识库自调优',modeDarwin:'Darwin 2.0 技能演化',modeLocal:'本地代码实验',
      startResearch:'启动全自动研究',stopResearch:'停止研究',resetResearch:'重置状态',stepResearch:'单步迭代',
      modelEngine:'研究 AI 引擎',modelFree:'Our Free Model (免额度)',modelDeepSeek:'DeepSeek 深度推理',modelLocal:'本地离线模型 (Ollama)',modelCurrent:'跟随当前会话',
      targetIters:'连续研究轮次',archiveToTopic:'存入【自主研究成果】天体',archivedSuccess:'已成功将成果沉淀至【自主研究成果】天体',
      targetFile:'修改靶点',evalScript:'评测考卷',iteration:'当前轮次',baseline:'初始基线',best:'历史最佳',ratchetHistory:'棘轮演化轨迹',proposeStrategy:'提交为演化策略',
      researchRunning:'全自动自主研究连续迭代中…',researchIdle:'空闲待命',researchPaused:'已暂停',researchNote:'单向棘轮机制：仅在指标严格提升时保留修改，退步自动撤销回滚。',programGoal:'研究目标与假设 (program.md)'});
    Object.assign(dictionaries.en,{panel:'Control panel',library:'Library',mecha:'Mecha observer',developer:'Plugin development',auto:'Automatic retrieval',
      chars:'Context limit (characters)',items:'Evidence items',excerpt:'Excerpt limit (characters)',save:'Save retrieval settings',query:'Retrieval query',
      preview:'Preview evidence',use:'Use for next answer',cancel:'Cancel queued retrieval',queued:'Next answer will revalidate and retrieve this query',
      test:'Test connection',connected:'Library connected · read-only',budgetNote:'Characters are not model tokens. Preview makes no model request; full owner rules use part of the budget.',
      previewNote:'Preview is for inspection. Queued evidence is retrieved again before the next answer.',emptyMecha:'No registered mecha yet; read library evidence first',
      location:'Current location',state:'State',action:'Latest action',idle:'Idle',active:'Reading',disconnected:'Disconnected',read:'Read',search:'Search',completed:'Completed',started:'Started',failed:'Failed',
      observeNote:'Observe this chat only. Hiding this area stops polling.',probe:'Current chat mecha diagram',file:'Plugin file',source:'Source',readFile:'Read file',
      check:'Check and preview diff',apply:'Apply source change',restore:'Restore previous source',request:'Development request',propose:'Create proposal and insert request',
      loadProposal:'Load AI-edited proposal',install:'Install / update bundle',syntax:'Syntax passed; edited code has not been executed',syntaxFailed:'Syntax failed',
      sourceSaved:'Source saved; updated code is not loaded',restored:'Source restored; restored code is not loaded',
      draftReady:'Request inserted. Inspect and send it; the AI edits only a temporary proposal.',draftChanged:'Draft changed; it was not overwritten. Generate the request again.',
      confirmApply:'Confirm source save',confirmRestore:'Confirm source restore',confirmInstall:'Confirm bundle installation',dismiss:'Cancel',
      applyNote:'Save plugin source only, retaining its original backup. No automatic installation.',
      installNote:'This changes the current profile and affects every chat. Edited Host code runs with host permissions.',
      restart:'Bundle updated; restart Harness to load the new code',applied:'Host reports applied; verify the loaded version',pending:'Not applied; inspect diagnostics',
      developerNote:'Only allowlisted plugin files. Checks validate syntax; behavior still requires verification after applying.',noInput:'Native input actions unavailable',
      noManager:'Native plugin manager unavailable',noEvidence:'No matching evidence',saveOkay:'Retrieval settings saved',cancelOkay:'Queued retrieval cancelled',
      notReady:'Plugin not ready',partial:'Result contains excerpts or incomplete evidence',full:'Result is not marked incomplete',backup:'Original backup',
      truncated:'Diff is too long; only part is displayed',characters:'characters',evidence:'evidence items',open:'Show control panel',close:'Hide control panel',
      research:'Research',modeLibrary:'Library Auto-Tuning',modeDarwin:'Darwin 2.0 Evolution',modeLocal:'Local Code Lab',
      startResearch:'Start Auto-Research',stopResearch:'Stop Research',resetResearch:'Reset',stepResearch:'Step Iteration',
      modelEngine:'Research AI Engine',modelFree:'Our Free Model (Zero Cost)',modelDeepSeek:'DeepSeek Deep Thinking',modelLocal:'Local Offline (Ollama)',modelCurrent:'Follow Current Chat',
      targetIters:'Continuous Iterations',archiveToTopic:'Archive to 【自主研究成果】 Topic',archivedSuccess:'Successfully archived to 【自主研究成果】 topic',
      targetFile:'Target File',evalScript:'Evaluation Script',iteration:'Iteration',baseline:'Baseline',best:'Best',ratchetHistory:'Ratchet History',proposeStrategy:'Propose Strategy',
      researchRunning:'Auto-research loop running…',researchIdle:'Idle',researchPaused:'Paused',researchNote:'One-way ratchet: changes kept only if metrics strictly improve, regressions revert automatically.',programGoal:'Research Goal & Hypothesis (program.md)'});
    Object.assign(dictionaries.zh,{online:'已连接',offline:'连接异常',checking:'连接检查中',automatic:'自动读取',manual:'手动读取',lastSeen:'上次观察',island:'AI Library 灵动岛',
      latestImport:'最新导入',autoSwitched:'已自动更新为上传内容',matchTopic:'探测会话星图主题',matchingTopic:'正在分析星图匹配…',
      assignTopic:'放入此主题',createAndAssign:'开辟新主题并放入',suggestedTopic:'建议新主题名称',matchedTopicNotice:'星图匹配主题',
      noMatchNotice:'星图未匹配，建议新建',createdTopicSuccess:'已在星图开辟新主题并归入本会话',assignedTopicSuccess:'已归入星图主题'});
    Object.assign(dictionaries.en,{online:'Connected',offline:'Connection error',checking:'Checking connection',automatic:'Auto read',manual:'Manual read',lastSeen:'Last observed',island:'AI Library island',
      latestImport:'Latest import',autoSwitched:'Auto-updated to import',matchTopic:'Detect Star Map topic',matchingTopic:'Analyzing match…',
      assignTopic:'Assign to this topic',createAndAssign:'Create new topic & assign',suggestedTopic:'Suggested topic title',matchedTopicNotice:'Matched Star Map topic',
      noMatchNotice:'No exact match, suggest new topic',createdTopicSuccess:'New topic created on Star Map and assigned',assignedTopicSuccess:'Assigned to Star Map topic'});
    Object.assign(dictionaries.zh,{openBrowser:'打开资料侧栏',directory:'资料目录',reviewSync:'待审同步',activity:'动作记录',browserTitle:'AI Library 资料',
      refreshLibrary:'刷新资料',moreLibrary:'加载更多资料',searchLibrary:'搜索当前项目资料',kind:'资料类型',allKinds:'全部资料',note:'来源记录',memory:'正式记忆',
      noObjects:'当前范围没有资料',body:'资料正文',backToDirectory:'返回目录',bodyTruncated:'正文已截断：当前显示部分原文',
      syncEnabled:'动作同步已启用',pendingCount:'待审记忆',lastSync:'上次入库',neverSynced:'尚未入库',queueCount:'待同步动作',
      sourceText:'原始来源（未核实）',sourcePartial:'来源仅保存了部分摘录，不是完整聊天记录。',claim:'待审摘录',keep:'保留：',saveSync:'保存选定记忆',confirmSync:'确认同步入库',
      syncConfirmation:'将选定摘录作为 UNVERIFIED 记忆与原始来源一起入库。确认不代表内容已核实。',syncSaved:'已确认同步入库',
      discardSync:'排除本批全部条目',confirmDiscardSync:'确认排除本批',discardConfirmation:'本批条目全部标记为排除，不保存来源或正式记忆。',syncDiscarded:'本批已全部排除',
      noPending:'当前任务没有待审记忆',chooseProject:'请先选择项目，再审核当前任务记忆',noActions:'当前聊天没有动作记录',
      revision:'修订',verification:'核实状态',reviewChanged:'待审包已更新，请重新核对',sessionChanged:'当前聊天已切换，请重新打开资料侧栏',syncUnavailable:'同步状态不可用',
      browserNote:'当前项目包含子目录；仅可见时刷新。阅读与搜索不调用模型。',observe:'动作',tool_started:'工具开始',tool_completed:'工具完成',tool_failed:'工具失败',turn_completed:'回合完成',
      pollingStopped:'自动刷新已停止（同一错误连续出现 3 次）',retry:'重试',retryTitle:'重新尝试'});
    Object.assign(dictionaries.en,{openBrowser:'Open library sidebar',directory:'Library directory',reviewSync:'Review sync',activity:'Activity',browserTitle:'AI Library browser',
      pollingStopped:'Auto-refresh stopped (same error 3 times in a row)',retry:'Retry',retryTitle:'Retry',
      refreshLibrary:'Refresh library',moreLibrary:'Load more objects',searchLibrary:'Search current project',kind:'Object type',allKinds:'All objects',note:'Source note',memory:'Memory',
      noObjects:'No objects in this scope',body:'Source content',backToDirectory:'Back to directory',bodyTruncated:'Content truncated: only part of the original is displayed',
      syncEnabled:'Action sync enabled',pendingCount:'Pending memories',lastSync:'Last saved',neverSynced:'Not saved yet',queueCount:'Queued actions',
      sourceText:'Original source (unverified)',sourcePartial:'Only partial source excerpts were saved; this is not the complete conversation.',claim:'Pending excerpt',keep:'Include: ',saveSync:'Save selected memories',confirmSync:'Confirm sync save',
      syncConfirmation:'Save selected excerpts as UNVERIFIED memories with their original sources. Confirmation does not verify their claims.',syncSaved:'Sync save confirmed',
      discardSync:'Exclude every item in this batch',confirmDiscardSync:'Confirm batch exclusion',discardConfirmation:'Mark every item excluded without saving sources or formal memories.',syncDiscarded:'All items excluded',
      noPending:'No pending memories for this task',chooseProject:'Select a project to review task memories',noActions:'No activity for this chat',
      revision:'Revision',verification:'Verification',reviewChanged:'Review package changed; inspect it again',sessionChanged:'The chat changed. Reopen the library sidebar.',syncUnavailable:'Sync status unavailable',
      browserNote:'Includes project descendants. Refreshes while visible. Reading and search make no model request.',observe:'Activity',tool_started:'Tool started',tool_completed:'Tool completed',tool_failed:'Tool failed',turn_completed:'Turn completed'});
    const browserStyles=`
      .ail-browser { height:100%; min-height:0; overflow:auto; overscroll-behavior:contain; padding:16px;
        font:13px/1.5 system-ui,sans-serif; color:var(--dsw-alias-label-primary); overflow-wrap:anywhere; }
      .ail-browser * { box-sizing:border-box; }
      .ail-browser button,.ail-browser input:not([type="checkbox"]),.ail-browser select,.ail-browser label { min-height:44px; }
      .ail-browser button { cursor:pointer; font:inherit; border:1px solid var(--dsw-alias-border-l); border-radius:8px;
        padding:8px 12px; background:var(--dsw-alias-interactive-bg-hover-solid); color:inherit; }
      .ail-browser button:disabled { cursor:default; opacity:.5; }
      .ail-browser :is(button,input,select):focus-visible { outline:2px solid #78a9c1; outline-offset:3px; }
      .ail-browser input,.ail-browser select { min-width:0; max-width:100%; font:inherit; color:inherit;
        border:1px solid var(--dsw-alias-border-l); border-radius:8px; padding:8px; background:var(--dsw-alias-interactive-bg-hover-solid); }
      .ail-browser nav,.ail-browser__tools { display:flex; gap:8px; flex-wrap:wrap; margin:12px 0; }
      .ail-browser nav button[aria-pressed="true"] { border-color:#78a9c1; }
      .ail-browser__status { margin:0; font-size:12px; opacity:.8; }
      .ail-browser__list { padding:0; list-style:none; display:grid; gap:8px; }
      .ail-browser__list button { width:100%; text-align:left; display:grid; gap:4px; }
      .ail-browser small { font-size:11px; opacity:.8; }
      .ail-browser pre { white-space:pre-wrap; overflow-wrap:anywhere; font:inherit; margin:8px 0; }
      .ail-browser article { border:1px solid var(--dsw-alias-border-l); border-radius:10px; padding:12px; margin:12px 0; }
      .ail-browser article label { display:flex; align-items:center; gap:8px; }
      .ail-browser input[type="checkbox"] { width:18px; height:18px; accent-color:#78a9c1; }
      .ail-browser [role="alert"] { color:var(--dsw-alias-label-error,#db6464); }
      @media(max-width:480px) { .ail-browser { padding:12px; } .ail-browser__tools input { width:100%; } }
      @media(prefers-reduced-motion:reduce) { .ail-browser * { animation:none!important; transition:none!important; } }
    `;
    return {
      inject: ['slots', 'locale', 'remote', 'remote.commands', 'remote.pluginManager', 'sidebarRightTabs', 'sidebarRight'],
      apply(ctx) {
        ctx.effect(() => ctx.locale.register(namespace, dictionaries));
        async function execute(sessionId, command, retries = 3) {
          if (!sessionId) throw new Error(dictionaries.zh.notReady);
          try {
            const response = await ctx.remote.commands.execute(sessionId, '/library ' + JSON.stringify(command), []);
            if (!response.ok) throw new Error(response.error.message);
            const result = response.value?.result;
            if (result?.kind !== 'success') {
              if (result?.text) throw new Error(result.text);
              if (retries > 0) {
                await new Promise(resolve => setTimeout(resolve, 400));
                return await execute(sessionId, command, retries - 1);
              }
              throw new Error(dictionaries.zh.unavailable);
            }
            const packet = JSON.parse(result.text);
            const protocol=['list_projects','select_project'].includes(command.operation)?'ai-library-project/1':'ai-library-controls/1';
            if (packet.protocol !== protocol || packet.operation !== command.operation) throw new Error('Invalid library response');
            return packet;
          } catch (err) {
            if (retries > 0 && (String(err?.message).includes('unavailable') || String(err?.message).includes('not ready'))) {
              await new Promise(resolve => setTimeout(resolve, 400));
              return await execute(sessionId, command, retries - 1);
            }
            throw err;
          }
        }
        const browserId='ai-library-browser';
        // A native page address is sidebar://ai-library-browser, composed by openTab.
        ctx.effect(()=>ctx.sidebarRightTabs.register({id:browserId,kind:browserId,keepMounted:true,
          title:()=>ctx.locale.bind(namespace)('browserTitle')}));
        function BrowserTitle({t}) { return h('span',null,t('browserTitle')); }
        function LibraryBrowser({sessionId,useProjection,useTabInfo,t}) {
          const project=useProjection('aiLibraryProject'),{tab}=useTabInfo();
          // A new authority scope gets a new component before any old content can render.
          return h(BrowserBody,{key:sessionId+':'+(project?.id??''),sessionId,project,t,visible:tab.visible,signal:tab.signal});
        }
        function BrowserBody({sessionId,project,t,visible,signal}) {
          const [view,setView]=React.useState('directory'),[query,setQuery]=React.useState(''),[kind,setKind]=React.useState('all');
          const [page,setPage]=React.useState(null),[object,setObject]=React.useState(null),[selected,setSelected]=React.useState(null);
          const [sync,setSync]=React.useState(null),[batch,setBatch]=React.useState(null),[included,setIncluded]=React.useState([]);
          const [activity,setActivity]=React.useState(null),[error,setError]=React.useState(''),[notice,setNotice]=React.useState('');
          const [busy,setBusy]=React.useState(false),[confirmation,setConfirmation]=React.useState(null);
          const [retry,setRetry]=React.useState(0),[halted,setHalted]=React.useState('');
          const lifetime=React.useRef(0),requests=React.useRef({}),selection=React.useRef(null),revision=React.useRef(null),reviewRef=React.useRef(null);
          const failures=React.useRef({}),circuit=React.useRef({}),circuits=React.useRef(0);
          const visibleRef=React.useRef(visible),queryRef=React.useRef(query),kindRef=React.useRef(kind),busyRef=React.useRef(false);
          visibleRef.current=visible;queryRef.current=query;kindRef.current=kind;
          const enabled=()=>visibleRef.current&&!signal?.aborted&&(typeof document==='undefined'||document.visibilityState!=='hidden');
          async function request(operation,fields={}) {
            if(!enabled())return null;
            if(circuit.current[operation])return null;
            const epoch=lifetime.current,sequence=(requests.current[operation]??0)+1;requests.current[operation]=sequence;
            try {
              delete failures.current[operation];
              const result=await execute(sessionId,{operation,...fields});
              return epoch===lifetime.current&&sequence===requests.current[operation]&&enabled()?result:null;
            } catch(failure) {
              // A deterministic rejection never succeeds on retry; stop instead of
              // hammering the Host every 3s forever with the same failing command.
              const message=String(failure?.message??failure);
              const strikes=(failures.current[operation]??0)+1;failures.current[operation]=strikes;
              if(strikes>=3){circuit.current[operation]=message;++circuits.current;
                if(epoch===lifetime.current)setHalted(`${operation}: ${message}`);}
              if(epoch===lifetime.current&&sequence===requests.current[operation]&&enabled())setError(message);
              return null;
            }
          }
          function resetCircuits(){failures.current={};circuit.current={};++circuits.current;}
          async function browse(more=false) {
            setError('');const q=queryRef.current,k=kindRef.current;
            const result=await request('browse',{offset:more?page?.items.length??0:0,limit:40,query:q,kind:k});
            if(!result||q!==queryRef.current||k!==kindRef.current)return;
            if(!Array.isArray(result.items)||result.items.length>100){setError('Invalid library directory');return;}
            setPage(previous=>({...result,items:more?[...(previous?.items??[]),...result.items]:result.items}));revision.current=result.fingerprint;
          }
          async function read(row) {
            const result=await request('read_object',{kind:row.kind,object_id:row.id});
            if(result&&selection.current?.id===row.id&&selection.current?.kind===row.kind){setObject(result.object);revision.current=result.fingerprint;}
          }
          async function status() { const result=await request('sync_status');if(result)setSync(result); }
          async function review() {
            if(!project?.id){setBatch(null);return;}
            const result=await request('sync_review');if(!result)return;
            const next=result.batch;
            if(next&&next.project_id!==project.id){setError(t('reviewChanged'));setBatch(null);setConfirmation(null);return;}
            if(next?.id!==reviewRef.current?.id||next?.digest!==reviewRef.current?.digest){
              setIncluded(next?.items.map(item=>item.id)??[]);setConfirmation(null);
            }
            reviewRef.current=next;setBatch(next);
          }
          async function observe() {const result=await request('observe');if(result)setActivity(result.session);}
          React.useEffect(()=>{
            if(!visible||signal?.aborted)return;
            let stopped=false,timer;
            const abort=()=>{stopped=true;clearTimeout(timer);++lifetime.current;};
            const poll=async()=>{
              if(stopped)return;
              if(enabled()&&!busyRef.current){
                await status();
                if(stopped)return;
                if(view==='directory') {
                  const result=await request('fingerprint');
                  if(result&&result.fingerprint!==revision.current){await browse();if(selection.current)await read(selection.current);}
                } else if(view==='reviewSync')await review();
                else await observe();
              }
              if(!stopped&&!Object.keys(circuit.current).length)timer=setTimeout(poll,3000);
            };
            const start=async()=>{
              resetCircuits();setHalted('');
              if(enabled()) {
                if(view==='directory'){await browse();if(selection.current)await read(selection.current);}
                else if(view==='reviewSync')await review();else await observe();
              }
              if(!stopped)void poll();
            };
            signal?.addEventListener('abort',abort,{once:true});void start();
            return()=>{abort();signal?.removeEventListener('abort',abort);};
          },[sessionId,project?.id,visible,signal,view,kind,retry]);
          React.useEffect(()=>()=>{++lifetime.current;},[]);
          async function confirmSave() {
            const chosen=confirmation;setConfirmation(null);
            if(!chosen||!project?.id||chosen.batch_id!==reviewRef.current?.id||chosen.digest!==reviewRef.current?.digest){setError(t('reviewChanged'));return;}
            busyRef.current=true;setBusy(true);setError('');setNotice('');
            try {
              const result=await request('sync_confirm',chosen);
              if(result){setNotice(t(chosen.item_ids.length?'syncSaved':'syncDiscarded'));await status();await review();}
            } finally {busyRef.current=false;setBusy(false);}
          }
          const button=(label,onClick,disabled=false)=>h('button',{type:'button',disabled:busy||disabled,onClick:()=>void onClick()},t(label));
          const meta=row=>`${t(row.kind)} · ${row.verification??'UNVERIFIED'} · ${t('revision')} ${row.revision??''}`;
          let body;
          if(view==='directory')body=h('div',null,
            h('div',{className:'ail-browser__tools'},h('input',{type:'search','aria-label':t('searchLibrary'),placeholder:t('searchLibrary'),value:query,maxLength:2000,
              onChange:event=>{queryRef.current=event.target.value;setQuery(event.target.value);setPage(null);setObject(null);setSelected(null);selection.current=null;++requests.current.browse;},
              onKeyDown:event=>{if(event.key==='Enter'){event.preventDefault();void browse();}}}),
              h('select',{'aria-label':t('kind'),value:kind,onChange:event=>{kindRef.current=event.target.value;setKind(event.target.value);setPage(null);setObject(null);setSelected(null);selection.current=null;}},
                ...['all','note','memory'].map(value=>h('option',{key:value,value},t(value==='all'?'allKinds':value)))),button('refreshLibrary',()=>browse())),
            selected&&button('backToDirectory',()=>{selection.current=null;setSelected(null);setObject(null);++requests.current.read_object;}),
            selected?h('article',null,h('h3',null,object?.title??selected.title),object?h(React.Fragment,null,h('small',null,meta(object)),h('pre',{'aria-label':t('body')},object.content??''),
              (object.partial||object.truncated||object.content_truncated)&&h('p',{role:'status'},t('bodyTruncated')),object.source&&h('small',null,JSON.stringify(object.source))):h('p',{role:'status'},t('loading'))):
              h(React.Fragment,null,page?h('ul',{className:'ail-browser__list'},...page.items.map(row=>h('li',{key:row.kind+':'+row.id},h('button',{type:'button',onClick:()=>{selection.current=row;setSelected(row);setObject(null);void read(row);}},h('strong',null,row.title),h('small',null,meta(row)))))):h('p',{role:'status'},t('loading')),
                page?.items.length===0&&h('p',null,t('noObjects')),page?.has_more&&button('moreLibrary',()=>browse(true))),h('small',null,t('browserNote')));
          else if(view==='reviewSync')body=!project?.id?h('p',null,t('chooseProject')):batch?h('div',null,
            ...batch.items.map(item=>h('article',{key:item.id},h('label',null,h('input',{type:'checkbox','aria-label':t('keep')+item.title,checked:included.includes(item.id),disabled:busy,
              onChange:event=>{setIncluded(current=>event.target.checked?[...current,item.id]:current.filter(id=>id!==item.id));setConfirmation(null);}}),h('strong',null,item.title)),
              h('small',null,'UNVERIFIED'),h('p',null,t('claim')),h('pre',null,item.claim),h('p',null,t('sourceText')),h('pre',null,item.source_text),
              item.source_partial&&h('p',{role:'status'},t('sourcePartial')))),
            button(included.length?'saveSync':'discardSync',()=>setConfirmation({batch_id:batch.id,digest:batch.digest,item_ids:[...included]}))):
              h('p',null,t('noPending'));
          else body=h('div',null,activity?.events?.length?h('ol',null,...activity.events.slice(-40).map((event,index)=>h('li',{key:event.id??index},
            h('strong',null,event.tool??event.tool_name??event.toolName??t(event.action??event.event_type??event.type??'action')),h('span',null,' · '+t(event.status??event.event_type??event.type??'completed')),
            event.timestamp&&h('small',null,' · '+event.timestamp)))):h('p',null,t('noActions')));
          return h('section',{className:'ail-browser','aria-label':t('browserTitle')},h('style',null,browserStyles),h('h2',null,project?.title??t('all')),
            h('p',{className:'ail-browser__status',role:'status'},sync?`${t('syncEnabled')} · ${t('pendingCount')}: ${sync.pending_count??0} · ${t('lastSync')}: ${sync.last_synced_at??t('neverSynced')} · ${t('queueCount')}: ${sync.queued_events??0}`:t('syncUnavailable')),
            (sync?.error||sync?.sync_error)&&h('p',{role:'alert'},sync.error||sync.sync_error),
            h('nav',{'aria-label':t('browserTitle')},...['directory','reviewSync','activity'].map(value=>h('button',{key:value,type:'button','aria-pressed':view===value,disabled:busy,onClick:()=>{setView(value);setError('');setNotice('');setConfirmation(null);}},t(value)))),
            halted&&h('div',{role:'group','aria-label':t('retryTitle')},h('p',{role:'alert'},`${t('pollingStopped')}: ${halted}`),
              h('button',{type:'button',onClick:()=>{resetCircuits();setError('');setNotice('');setRetry(retry=>retry+1);}},t('retry'))),
            body,confirmation&&h('div',{role:'group','aria-label':t('confirmSync')},h('p',null,t(confirmation.item_ids.length?'syncConfirmation':'discardConfirmation')),button(confirmation.item_ids.length?'confirmSync':'confirmDiscardSync',confirmSave),button('dismiss',()=>setConfirmation(null))),
            busy&&h('p',{role:'status'},t('saving')),notice&&h('p',{role:'status'},notice),error&&h('p',{role:'alert'},error));
        }
        ctx.effect(()=>ctx.slots.inject('sidebar.right.pane.tab',()=>ctx.slots.register({name:'sidebar.right.pane.tab',key:browserId,locale:namespace},LibraryBrowser)));
        ctx.effect(()=>ctx.slots.inject('sidebar.right.pane.tab.title',()=>ctx.slots.register({name:'sidebar.right.pane.tab.title',key:browserId,locale:namespace},BrowserTitle)));
        function ControlPanel({sessionId,useProjection,t,inputActions,projectId,active=true,onConnection,onObserved}) {
          const controls=useProjection('aiLibraryControls');
          const [tab,setTab]=React.useState('library'),[settings,setSettings]=React.useState(controls?.settings??{autoRead:true,maxContextChars:6000,maxItems:3,excerptChars:480});
          const [busy,setBusy]=React.useState(false),[error,setError]=React.useState(''),[notice,setNotice]=React.useState('');
          const [connection,setConnection]=React.useState(null),[query,setQuery]=React.useState(''),[preview,setPreview]=React.useState(null);
          const [mecha,setMecha]=React.useState(null),[mechaError,setMechaError]=React.useState('');
          const [file,setFile]=React.useState('client.js'),[code,setCode]=React.useState(''),[review,setReview]=React.useState(null);
          const [request,setRequest]=React.useState(''),[proposal,setProposal]=React.useState(null),[saved,setSaved]=React.useState(null),[confirm,setConfirm]=React.useState('');
          const [researchMode,setResearchMode]=React.useState('library'),[researchStatus,setResearchStatus]=React.useState({status:'idle',iteration:0,baselineScore:null,bestScore:null,history:[]});
          const [researchEngine,setResearchEngine]=React.useState('free'),[researchIters,setResearchIters]=React.useState(5);
          const [researchTarget,setResearchTarget]=React.useState('retrieval_config.json'),[researchEval,setResearchEval]=React.useState('eval_retrieval.py'),[researchGoal,setResearchGoal]=React.useState('');
          const generation=React.useRef(0);
          const style={font:'inherit',color:'var(--dsw-alias-label-primary)',background:'var(--dsw-alias-interactive-bg-hover-solid)',
            border:'1px solid var(--dsw-alias-border-l)',borderRadius:'6px',padding:'5px 8px'};
          const button=(key,fn,disabled=false)=>h('button',{type:'button',style,disabled:busy||disabled,onClick:()=>void fn()},t(key));
          const line=(label,child)=>h('label',{style:{display:'flex',alignItems:'center',gap:'8px',flexWrap:'wrap'}},t(label),child);
          async function run(operation,fields={}) {
            const stamp=++generation.current;setBusy(true);setError('');setNotice('');
            try {
              const result=await execute(sessionId,{operation,...fields});
              return stamp===generation.current?result:null;
            }catch(failure){if(stamp===generation.current)setError(failure.message);return null;}
            finally{if(stamp===generation.current)setBusy(false);}
          }
          React.useEffect(()=>{setSettings(controls?.settings??settings);setPreview(null);},[controls?.settings]);
          React.useEffect(()=>{setPreview(null);},[projectId]);
          React.useEffect(()=>{
            let stopped=false;
            execute(sessionId,{operation:'ping'}).then(result=>{if(!stopped){setConnection(result);onConnection?.('online');}},failure=>{if(!stopped){setError(failure.message);onConnection?.('offline');}});
            return()=>{stopped=true;++generation.current;};
          },[sessionId]);
          React.useEffect(()=>{
            if(!active||tab!=='mecha')return;
            let stopped=false,timer;
            const poll=async()=>{
              if(stopped)return;
              if(typeof document==='undefined'||document.visibilityState!=='hidden') {
                try{const result=await execute(sessionId,{operation:'observe'});if(!stopped){setMecha(result.session);onObserved?.(result.session?.state??null);setMechaError('');}}
                catch(failure){if(!stopped){setMecha(null);setMechaError(failure.message);}}
              }
              if(!stopped)timer=setTimeout(poll,1000);
            };
            void poll();return()=>{stopped=true;clearTimeout(timer);};
          },[sessionId,tab,active]);
          React.useEffect(()=>{
            if(!active||tab!=='research')return;
            let stopped=false,timer;
            const poll=async()=>{
              if(stopped)return;
              try{const res=await execute(sessionId,{operation:'research_status'});if(!stopped&&res)setResearchStatus(res);}catch{}
              if(!stopped)timer=setTimeout(poll,2000);
            };
            void poll();return()=>{stopped=true;clearTimeout(timer);};
          },[sessionId,tab,active]);
          const saveSettings=async()=>{const result=await run('configure',{settings});if(result){setPreview(null);setNotice(t('saveOkay'));}};
          const search=async()=>{setPreview(null);const result=await run('preview',{query});if(result)setPreview(result);};
          const startResearch=async()=>{
            const res=await run('research_start',{
              mode:researchMode,
              modelEngine:researchEngine,
              targetIterations:researchIters,
              continuous:true,
              targetFile:researchTarget,
              evalScript:researchEval,
              instructions:researchGoal
            });
            if(res){setResearchStatus(res);setNotice(t('researchRunning'));}
          };
          const archiveResearch=async()=>{
            const res=await run('research_archive',{});
            if(res){setNotice(t('archivedSuccess')+': 【'+(res.title||'')+'】');}
          };
          const stopResearch=async()=>{const res=await run('research_stop');if(res){setResearchStatus(res);setNotice(t('researchPaused'));}};
          const resetResearch=async()=>{const res=await run('research_reset');if(res){setResearchStatus(res);setNotice(t('resetResearch'));}};
          const stepResearch=async()=>{const res=await run('research_step',{});if(res){setResearchStatus(res);}};
          const proposeResearch=async()=>{const res=await run('research_propose');if(res){setNotice(t('proposeStrategy')+':\n'+JSON.stringify(res,null,2));}};
          const check=async()=>{
            setSaved(null);setReview(null);
            const staged=await run('developer_stage',{file,content:code});if(!staged)return;
            const result=await run('developer_check',{id:staged.id});
            if(result){setReview({...result.review,checked:result.ok});setNotice(t(result.ok?'syntax':'syntaxFailed')+(result.diagnostic?'\n'+result.diagnostic:''));}
          };
          const propose=async()=>{
            if(!inputActions){setError(t('noInput'));return;}
            const span=inputActions.captureInsertion();
            const result=await run('developer_proposal',{file,instructions:request});if(!result)return;
            setProposal(result);setReview(null);
            setNotice(t(inputActions.insertText(result.prompt,span)?'draftReady':'draftChanged'));
          };
          const perform=async()=>{
            const mode=confirm;setConfirm('');
            if(mode==='apply') {const result=await run('developer_apply',{id:review.id});if(result){setSaved({id:review.id,...result});setReview(null);setNotice(t('sourceSaved'));}}
            if(mode==='restore') {const result=await run('developer_restore',{id:saved.id});if(result){setSaved(null);setReview(null);setCode('');setNotice(t('restored'));}}
            if(mode==='install') {
              const stamp=++generation.current;setBusy(true);setError('');setNotice('');
              try {
                const manager=ctx.remote.pluginManager;if(!manager||!connection?.sourceDir)throw new Error(t('noManager'));
                const inventory=await manager.listBundles();if(!inventory.ok)throw new Error(inventory.error.message);
                const response=await manager.installBundle(connection.sourceDir,{enabled:true});
                if(!response.ok)throw new Error(response.error.message);
                if(stamp===generation.current) {
                  const result=response.value;
                  setNotice(t(result.application==='restart-required'?'restart':result.application==='applied'?'applied':'pending')+'\n'+JSON.stringify(result,null,2));
                }
              }catch(failure){if(stamp===generation.current)setError(failure.message);}
              finally{if(stamp===generation.current)setBusy(false);}
            }
          };
          let body;
          if(tab==='library') {
            let evidence=[];
            if(preview)try{evidence=JSON.parse(preview.context_text.split('\nEVIDENCE_JSON:\n')[1]);}catch{}
            body=h('div',{style:{display:'grid',gap:'10px'}},
              line('auto',h('input',{type:'checkbox',checked:settings.autoRead,disabled:busy||!controls,onChange:e=>setSettings({...settings,autoRead:e.target.checked})})),
              ...[['chars','maxContextChars',2000,40000],['items','maxItems',1,20],['excerpt','excerptChars',40,4000]].map(([label,key,min,max])=>
                line(label,h('input',{type:'number',min,max,value:settings[key],disabled:busy||!controls,style:{...style,width:'110px'},onChange:e=>setSettings({...settings,[key]:Number(e.target.value)})}))),
              h('div',{style:{display:'flex',gap:'6px',flexWrap:'wrap'}},button('save',saveSettings,!controls),button('test',async()=>{const r=await run('ping');onConnection?.(r?'online':'offline');if(r){setConnection(r);setNotice(t('connected'));}})),
              h('small',null,t('budgetNote')),
              line('query',h('input',{type:'text',value:query,maxLength:2000,style:{...style,flex:'1'},onChange:e=>{setQuery(e.target.value);setPreview(null);}})),
              button('preview',search,!query.trim()),h('small',null,t('previewNote')),
              preview&&h('div',null,h('p',null,`${preview.accounting.context_characters} ${t('characters')} · ${preview.evidence_count} ${t('evidence')} · ${t(preview.partial?'partial':'full')}`),
                ...evidence.map((row,index)=>h('article',{key:index,style:{padding:'8px',border:'1px solid var(--dsw-alias-border-l)',marginBottom:'6px'}},
                  h('strong',null,row.title),h('p',{style:{whiteSpace:'pre-wrap'}},row.excerpt),h('small',{style:{overflowWrap:'anywhere'}},`${row.kind??''} ${row.id} · ${row.verification} · ${row.source?.content_hash??''}`),
                  row.reported_conflict&&h('p',null,row.reported_conflict))),
                evidence.length===0&&h('p',null,t('noEvidence')),
                button('use',async()=>{const r=await run('queue_preview',{ticket:preview.ticket});if(r)setNotice(t('queued'));})),
              controls?.next&&h('div',null,h('p',null,t('queued')+': '+controls.next.query),button('cancel',async()=>{const r=await run('cancel_preview');if(r)setNotice(t('cancelOkay'));})));
          } else if(tab==='mecha') {
            const latest=mecha?.events?.at(-1);
            body=h('figure',{style:{margin:0,maxWidth:'340px',padding:'12px',border:'1px solid var(--dsw-alias-border-l)',borderRadius:'8px'}},
              h('svg',{viewBox:'0 0 240 110',width:'240',height:'110',role:'img','aria-label':t('probe'),style:{maxWidth:'100%'}},
                h('circle',{cx:120,cy:55,r:42,fill:'none',stroke:'currentColor',opacity:.12}),
                ...[-1,1].flatMap(side=>[0,1,2,3].map(i=>h('path',{key:side+':'+i,d:`M ${120+side*12} ${39+i*10} Q ${120+side*38} ${20+i*16} ${120+side*(59-i*2)} ${14+i*26}`,fill:'none',stroke:mecha?.state==='active'?'#8ad9ed':'#b9c7cf',strokeWidth:1.3}))),
                h('path',{d:'M120 31 L135 44 L135 64 L120 78 L105 64 L105 44 Z',fill:'none',stroke:'#b9c7cf',strokeWidth:1.5}),
                h('circle',{cx:120,cy:55,r:8,fill:mecha?.state==='active'?'#a7e4ed':'#f2e7c9',opacity:mecha?.state==='disconnected'?.3:1})),
              mecha?h('figcaption',null,h('p',null,t('location')),h('strong',null,mecha.location_title||mecha.position?.topic_id||mecha.position?.object_id||t('idle')),
                h('p',null,`${t('state')}: ${t(mecha.state)}`),latest&&h('p',null,`${t('action')}: ${latest.tool??latest.tool_name??t(latest.action)} · ${t(latest.status)}`),
                h('small',null,mecha.position?.object_id??mecha.position?.topic_id??'')):h('p',null,t('emptyMecha')),
              mechaError&&h('p',{role:'alert'},mechaError),h('small',null,t('observeNote')));
          } else if(tab==='developer') {
            body=h('div',{style:{display:'grid',gap:'10px'}},h('small',null,t('developerNote')),
              line('file',h('select',{value:file,disabled:busy,style,onChange:e=>{setFile(e.target.value);setCode('');setReview(null);setProposal(null);setSaved(null);}},
                ...(connection?.developmentFiles??['client.js']).map(name=>h('option',{key:name,value:name},name)))),
              button('readFile',async()=>{const r=await run('developer_read',{file});if(r){setCode(r.content);setReview(null);}}),
              line('source',h('textarea',{value:code,rows:12,maxLength:128000,disabled:busy,spellCheck:false,style:{...style,width:'100%',boxSizing:'border-box',fontFamily:'monospace'},onChange:e=>{setCode(e.target.value);setReview(null);}})),
              button('check',check,!code),review&&h('div',null,h('pre',{style:{whiteSpace:'pre-wrap',overflowWrap:'anywhere',maxHeight:'240px',overflow:'auto'}},review.diff),review.diff_truncated&&h('p',null,t('truncated')),
                button('apply',()=>setConfirm('apply'),!review.checked)),
              saved&&h('div',null,h('small',{style:{overflowWrap:'anywhere'}},t('backup')+': '+saved.backup),button('restore',()=>setConfirm('restore'))),
              line('request',h('textarea',{value:request,rows:2,maxLength:2000,disabled:busy,style:{...style,width:'100%'},onChange:e=>setRequest(e.target.value)})),
              button('propose',propose,!request.trim()),
              proposal&&button('loadProposal',async()=>{const r=await run('developer_review',{id:proposal.id});if(r){setCode(r.content);setReview(null);setFile(r.file);}}),
              button('install',()=>setConfirm('install'),!connection?.sourceDir),h('small',{style:{overflowWrap:'anywhere'}},connection?.sourceDir??t('notReady')));
          } else if(tab==='research') {
            const isRunning=researchStatus?.status==='running';
            const hasImprovement=researchStatus?.bestScore!=null&&researchStatus?.baselineScore!=null&&researchStatus.bestScore>researchStatus.baselineScore;
            const improvementPct=hasImprovement?(((researchStatus.bestScore-researchStatus.baselineScore)/researchStatus.baselineScore)*100).toFixed(1):null;
            body=h('div',{style:{display:'grid',gap:'10px'}},
              h('small',null,t('researchNote')),
              h('div',{style:{display:'flex',gap:'6px',flexWrap:'wrap'}},
                h('button',{type:'button',style:{...style,flex:1,minWidth:'90px'},'aria-pressed':researchMode==='library',disabled:busy||isRunning,onClick:()=>{setResearchMode('library');setResearchTarget('retrieval_config.json');setResearchEval('eval_retrieval.py');}},t('modeLibrary')),
                h('button',{type:'button',style:{...style,flex:1,minWidth:'105px'},'aria-pressed':researchMode==='darwin',disabled:busy||isRunning,onClick:()=>{setResearchMode('darwin');setResearchTarget('.agents/skills/brainstorming/SKILL.md');setResearchEval('test-prompts.json');}},t('modeDarwin')),
                h('button',{type:'button',style:{...style,flex:1,minWidth:'90px'},'aria-pressed':researchMode==='local',disabled:busy||isRunning,onClick:()=>{setResearchMode('local');setResearchTarget('train.py');setResearchEval('eval.py');}},t('modeLocal'))),
              h('div',{style:{display:'flex',gap:'8px',flexWrap:'wrap'}},
                line('modelEngine',h('select',{value:researchEngine,disabled:busy||isRunning,style:{...style,flex:1},onChange:e=>setResearchEngine(e.target.value)},
                  h('option',{value:'free'},t('modelFree')),
                  h('option',{value:'deepseek'},t('modelDeepSeek')),
                  h('option',{value:'local'},t('modelLocal')),
                  h('option',{value:'current'},t('modelCurrent')))),
                line('targetIters',h('select',{value:researchIters,disabled:busy||isRunning,style:{...style,width:'110px'},onChange:e=>setResearchIters(Number(e.target.value))},
                  h('option',{value:3},'3 轮快速验证'),
                  h('option',{value:5},'5 轮标准连续跑'),
                  h('option',{value:10},'10 轮深度进化'),
                  h('option',{value:20},'20 轮全自主进化')))),
              h('article',{style:{padding:'12px',border:'1px solid var(--dsw-alias-border-l)',borderRadius:'12px',background:'linear-gradient(145deg, rgba(17,25,35,0.85) 0%, rgba(13,20,29,0.95) 100%)'}},
                h('div',{style:{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'8px'}},
                  h('strong',null,`${t('iteration')} #${researchStatus?.iteration??0} / ${researchIters}`),
                  h('span',{style:{color:isRunning?'#95dcd9':'#a6b2c1',fontWeight:600,fontSize:'11.5px'}},t(isRunning?'researchRunning':researchStatus?.status==='paused'?'researchPaused':'researchIdle'))),
                h('div',{style:{display:'flex',gap:'20px',fontSize:'12.5px',color:'#eef1f5',alignItems:'baseline'}},
                  h('span',null,`${t('baseline')}: ${researchStatus?.baselineScore??'—'}`),
                  h('span',null,`${t('best')}: `,h('strong',{style:{color:'#95dcd9'}},String(researchStatus?.bestScore??'—'))),
                  improvementPct&&h('span',{style:{color:'#95dcd9',fontSize:'11.5px',fontWeight:600}},`+${improvementPct}%`))),
              line('targetFile',h('input',{type:'text',value:researchTarget,disabled:busy||isRunning,style:{...style,flex:1},onChange:e=>setResearchTarget(e.target.value)})),
              line('evalScript',h('input',{type:'text',value:researchEval,disabled:busy||isRunning,style:{...style,flex:1},onChange:e=>setResearchEval(e.target.value)})),
              line('programGoal',h('textarea',{value:researchGoal,rows:2,maxLength:2000,disabled:busy||isRunning,placeholder:researchMode==='darwin'?'针对技能指令与边界规则进行 9 维自进化评测优化…':'设定优化假设，例如：微调检索 RRF k 因子…',style:{...style,width:'100%'},onChange:e=>setResearchGoal(e.target.value)})),
              h('div',{style:{display:'flex',gap:'6px',flexWrap:'wrap'}},
                !isRunning?button('startResearch',startResearch,busy):button('stopResearch',stopResearch,busy),
                button('stepResearch',stepResearch,busy||isRunning),
                button('resetResearch',resetResearch,busy||isRunning),
                button('archiveToTopic',archiveResearch,busy||!hasImprovement),
                button('proposeStrategy',proposeResearch,busy||!hasImprovement)),
              researchStatus?.history?.length>0&&h('div',{style:{marginTop:'8px'}},
                h('strong',{style:{fontSize:'11.5px',color:'#8e9cae'}},t('ratchetHistory')),
                ...researchStatus.history.slice(-5).reverse().map((item,idx)=>
                  h('div',{key:idx,style:{display:'flex',justifyContent:'space-between',padding:'5px 0',borderBottom:'1px solid rgba(255,255,255,0.05)',fontSize:'11.5px'}},
                    h('span',{style:{color:item.outcome==='kept'?'#95dcd9':item.outcome==='error'?'#ffb7b0':'#8e9cae'}},
                      `${item.outcome==='kept'?'✓ 保留':'✕ 回滚'} #${item.iteration}: ${item.message}`),
                    h('span',{style:{color:'#8e9cae'}},item.score!=null?`得分: ${item.score}`:'')))));
          }
          return h('section',{style:{fontSize:'12px'}},
            h('nav',{'aria-label':'AI Library',style:{display:'flex',gap:'6px',marginBottom:'12px'}},...['library','mecha','developer','research'].map(key=>
              h('button',{type:'button',style,'aria-pressed':tab===key,disabled:busy,onClick:()=>{setTab(key);setConfirm('');}},t(key)))),
            body,confirm&&h('div',{role:'group',style:{padding:'10px',border:'1px solid var(--dsw-alias-border-l)',marginTop:'10px'}},
              h('p',null,t(confirm==='install'?'installNote':'applyNote')),button(confirm==='apply'?'confirmApply':confirm==='restore'?'confirmRestore':'confirmInstall',perform),button('dismiss',()=>setConfirm(''))),
            busy&&h('p',{role:'status'},t('loading')),notice&&h('pre',{role:'status',style:{whiteSpace:'pre-wrap',overflowWrap:'anywhere',maxHeight:'180px',overflow:'auto'}},notice),error&&h('p',{role:'alert'},error));
        }
        function ProjectSelector({ sessionId, useProjection, t, inputActions }) {
          if (!sessionId) return null;
          const [expanded,setExpanded]=React.useState(false);
          const [visited,setVisited]=React.useState(false);
          const [connectionState,setConnectionState]=React.useState('checking');
          const [observed,setObserved]=React.useState(null);
          const project = useProjection('aiLibraryProject');
          const controls = useProjection('aiLibraryControls');
          const [catalog, setCatalog] = React.useState({ projects: [], offset: 0, has_more: false });
          const [status, setStatus] = React.useState('loading');
          const [error, setError] = React.useState('');
          const [latestImport, setLatestImport] = React.useState(null);
          const [recommendation, setRecommendation] = React.useState(null);
          const [recBusy, setRecBusy] = React.useState(false);
          const [newTopicName, setNewTopicName] = React.useState('');
          const [notice, setNotice] = React.useState('');
          const lastImportIdRef = React.useRef(null);
          const switchedImportIds = React.useRef(new Set());
          const generation = React.useRef(0);
          const toggleRef = React.useRef(null), contentRef = React.useRef(null);
          const titleId = React.useId();
          const contentId = React.useId();
          const open=()=>{setVisited(true);setExpanded(true);};
          const close=()=>{setExpanded(false);toggleRef.current?.focus();};
          React.useEffect(()=>{
            if(expanded)contentRef.current?.querySelector('select:not(:disabled), button:not(:disabled), input:not(:disabled), textarea:not(:disabled)')?.focus();
          },[expanded]);
          const refresh = async (more = false) => {
            const stamp = ++generation.current;
            setStatus('loading'); setError('');
            try {
              const offset = more ? catalog.offset + catalog.projects.length : 0;
              const page = await execute(sessionId, { operation: 'list_projects', offset, limit: 100 });
              if (stamp !== generation.current) return;
              if (!Array.isArray(page.projects) || page.projects.length > 100) throw new Error('Invalid library catalog');
              setCatalog({ ...page, offset: 0, projects: more ? [...catalog.projects, ...page.projects] : page.projects });
              setStatus('ready');
            } catch (failure) {
              if (stamp !== generation.current) return;
              setError(failure.message); setStatus('error');
            }
          };
          const checkLatestImport = async () => {
            try {
              const res = await execute(sessionId, { operation: 'latest_import' });
              const item = res?.latest_import;
              if (item && item.id) {
                setLatestImport(item);
                if (item.id !== lastImportIdRef.current) {
                  lastImportIdRef.current = item.id;
                  if (item.topic_id && item.topic_id !== project?.id && !switchedImportIds.current.has(item.id)) {
                    switchedImportIds.current.add(item.id);
                    await select(item.topic_id);
                    setNotice(t('autoSwitched') + ': ' + (item.topic_title || item.title));
                  }
                }
              }
            } catch {}
          };
          React.useEffect(() => {
            let stopped=false;
            setConnectionState('checking');setObserved(null);
            execute(sessionId,{operation:'ping'}).then(()=>{if(!stopped)setConnectionState('online');},()=>{if(!stopped)setConnectionState('offline');});
            void refresh();
            return () => { stopped=true;++generation.current; };
          }, [sessionId]);
          React.useEffect(() => {
            let stopped = false, timer;
            const poll = async () => {
              if (stopped) return;
              await checkLatestImport();
              if (!stopped) timer = setTimeout(poll, 4000);
            };
            void poll();
            return () => { stopped = true; clearTimeout(timer); };
          }, [sessionId, project?.id]);
          const select = async (value) => {
            const stamp = ++generation.current;
            setStatus('saving'); setError('');
            try {
              await execute(sessionId, { operation: 'select_project', project_id: value || null });
              if (stamp === generation.current) setStatus('ready');
            } catch (failure) {
              if (stamp !== generation.current) return;
              setError(failure.message); setStatus('error');
            }
          };
          const matchTopic = async () => {
            setRecBusy(true); setError(''); setNotice('');
            try {
              const res = await execute(sessionId, { operation: 'match_topic', text: '' });
              setRecommendation(res);
              if (!res.matched && res.suggested_title) {
                setNewTopicName(res.suggested_title);
              }
            } catch (failure) { setError(failure.message); }
            finally { setRecBusy(false); }
          };
          const confirmAssign = async (topicId) => {
            setRecBusy(true); setError('');
            try {
              await select(topicId);
              setNotice(t('assignedTopicSuccess'));
              setRecommendation(null);
            } catch (failure) { setError(failure.message); }
            finally { setRecBusy(false); }
          };
          const confirmCreateAndAssign = async () => {
            const title = newTopicName.trim();
            if (!title) return;
            setRecBusy(true); setError('');
            try {
              const res = await execute(sessionId, { operation: 'create_topic', title });
              const topic = res?.topic;
              await refresh();
              if (topic?.id) {
                await select(topic.id);
                setNotice(t('createdTopicSuccess') + ': ' + (topic.title || title));
              }
              setRecommendation(null);
            } catch (failure) { setError(failure.message); }
            finally { setRecBusy(false); }
          };
          const openBrowser=()=>{
            try {
              if(ctx.sidebarRight.mounted.getSnapshot()!==sessionId)throw new Error(t('sessionChanged'));
              ctx.sidebarRight.openTab(browserId);
            } catch(failure) {setError(failure.message);}
          };
          const busy = status === 'loading' || status === 'saving';
          const known = project == null || catalog.projects.some(row => row.id === project.id);
          const names = new Map(catalog.projects.map(row => [row.id, row]));
          const label = (row) => {
            const parts = [row.title], seen = new Set([row.id]);
            let parent = names.get(row.parent_id);
            while (parent && !seen.has(parent.id)) {
              parts.unshift(parent.title); seen.add(parent.id); parent = names.get(parent.parent_id);
            }
            return parts.join(' / ');
          };
          const control = { font: 'inherit', fontSize: '12px', color: 'var(--dsw-alias-label-primary)',
            background: 'var(--dsw-alias-interactive-bg-hover-solid)',
            border: '1px solid var(--dsw-alias-border-l)', borderRadius: '6px', padding: '4px 8px' };
          return h('aside',{className:'ail-island',style:{pointerEvents:'none'},'aria-label':t('island'),'data-expanded':String(expanded),onKeyDown:event=>{
            if(event.key==='Escape'&&expanded){event.preventDefault();event.stopPropagation();close();}
          }},h('style',null,islandStyles),h('div',{className:'ail-island__surface'},
            h('button',{type:'button',ref:toggleRef,className:'ail-island__toggle','aria-label':t(expanded?'close':'open'),
              'aria-expanded':expanded,'aria-controls':contentId,onClick:expanded?close:open},
              h('svg',{viewBox:'0 0 48 48',width:32,height:32,'aria-hidden':true,style:{flexShrink:0}},
                ...[-1,1].flatMap(side=>[0,1,2,3].map(i=>h('path',{key:side+':'+i,d:`M ${24+side*5} ${17+i*4} Q ${24+side*12} ${8+i*9} ${24+side*19} ${6+i*12}`,fill:'none',stroke:'#9aaab9',strokeWidth:1}))),
                h('path',{d:'M24 12 L30 18 L30 30 L24 36 L18 30 L18 18 Z',fill:'#1c2b38',stroke:'#b9c7cf'}),
                h('circle',{className:observed==='active'?'ail-island__pulse':'',cx:24,cy:24,r:4,fill:observed==='active'?'#95dcd9':'#f2e7c9'})),
              h('span',{className:'ail-island__title'},
                h('strong',null,'AI LIBRARY'),
                h('span',{className:'ail-island__project',title:project?.title??t('all')},
                  project?.title??t(project===undefined?'unavailable':'all')),
                (latestImport?.topic_title || latestImport?.title)&&h('span',{className:'ail-island__badge',title:latestImport.title},
                  t('latestImport')+': '+(latestImport.topic_title || latestImport.title))),
              h('span',{className:'ail-island__status'},h('span',{'data-online':String(connectionState==='online')},t(connectionState)),
                h('br'),observed?h('span',{title:t('lastSeen')},t('lastSeen')+' · '+t(observed)):t(controls?.settings.autoRead===false?'manual':'automatic')),
              h('svg',{className:'ail-island__chevron',viewBox:'0 0 16 16','aria-hidden':true},h('path',{d:'m4 6 4 4 4-4',fill:'none',stroke:'currentColor',strokeWidth:1.5}))),
            h('div',{id:contentId,ref:contentRef,className:'ail-island__content',hidden:!expanded},
            h('div',{className:'ail-island__project-controls'},
            h('label', { htmlFor: titleId }, t('project')),
            h('select', { id: titleId, value: project?.id ?? '', disabled: busy || project === undefined,
              onChange: event => void select(event.target.value), style: { ...control, maxWidth: '260px' } },
              h('option', { value: '' }, t('all')),
              !known && h('option', { value: project.id, disabled: true }, t('missing') + ': ' + project.title),
              ...catalog.projects.map(row => h('option', { key: row.id, value: row.id }, label(row)))),
            h('button', { type: 'button', disabled: busy, onClick: () => void refresh(), style: control }, t('refresh')),
            h('button',{type:'button',style:control,onClick:openBrowser},t('openBrowser')),
            catalog.has_more && h('button', { type: 'button', disabled: busy, onClick: () => void refresh(true), style: control }, t('more')),
            h('span', { role: 'status', 'aria-live': 'polite' }, project === undefined ? t('unavailable') : busy ? t(status) : t('next')),
            error && h('span', { role: 'alert', style: { flexBasis: '100%' } }, error)),
            h('div',{className:'ail-island__rec-box'},
              h('div',{className:'ail-island__rec-title'},
                h('div',{style:{display:'flex',alignItems:'center',gap:'6px'}},
                  h('svg',{viewBox:'0 0 16 16',width:14,height:14,fill:'none',stroke:'currentColor',strokeWidth:1.5},
                    h('circle',{cx:8,cy:8,r:6,strokeOpacity:0.3}),
                    h('circle',{cx:8,cy:8,r:3}),
                    h('path',{d:'M8 2 A6 6 0 0 1 14 8',stroke:'#95dcd9'})),
                  h('span',null,t('topicRecommendation'))),
                h('button',{type:'button',disabled:recBusy||busy,onClick:()=>void matchTopic(),
                  style:{...control,minHeight:'30px',padding:'2px 10px',background:recBusy?'rgba(21,30,41,0.5)':'rgba(25,48,51,0.8)',borderColor:'#95dcd9',color:'#95dcd9'}},
                  recBusy?t('matchingTopic'):t('matchTopic'))),
              recommendation&&h('div',{style:{marginTop:'10px',padding:'10px 12px',background:'rgba(13, 20, 29, 0.75)',borderRadius:'10px',border:'1px solid rgba(255, 255, 255, 0.06)'}},
                recommendation.matched?h('div',null,
                  h('div',{style:{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'6px'}},
                    h('span',{style:{color:'#95dcd9',fontWeight:600}},`${t('matchedTopicNotice')}: 【${recommendation.topic_title}】`),
                    h('span',{style:{fontSize:'11px',color:'#8e9cae'}},`匹配度: ${Math.round(recommendation.score * 100)}%`)),
                  h('small',{style:{display:'block',marginBottom:'10px',lineHeight:1.4}},recommendation.reason),
                  h('button',{type:'button',disabled:recBusy,style:{...control,width:'100%',background:'linear-gradient(135deg, #1b3c3d 0%, #162f30 100%)',borderColor:'#95dcd9',color:'#95dcd9'},onClick:()=>void confirmAssign(recommendation.topic_id)},t('assignTopic'))):
                h('div',null,
                  h('p',{style:{margin:'0 0 6px 0',color:'#fca5a5',fontWeight:600}},t('noMatchNotice')),
                  h('small',{style:{display:'block',marginBottom:'10px',lineHeight:1.4}},recommendation.reason),
                  h('div',{style:{display:'flex',gap:'8px',alignItems:'center'}},
                    h('input',{type:'text',value:newTopicName,placeholder:t('suggestedTopic'),disabled:recBusy,style:{...control,flex:1,minHeight:'36px'},onChange:e=>setNewTopicName(e.target.value)}),
                    h('button',{type:'button',disabled:recBusy||!newTopicName.trim(),style:{...control,minHeight:'36px',padding:'0 14px',background:'linear-gradient(135deg, #1b3c3d 0%, #162f30 100%)',borderColor:'#95dcd9',color:'#95dcd9'},onClick:()=>void confirmCreateAndAssign()},t('createAndAssign'))))),
              latestImport&&h('div',{style:{marginTop:'10px',fontSize:'11px',color:'#8e9cae',display:'flex',alignItems:'center',gap:'6px'}},
                h('span',{style:{display:'inline-block',width:6,height:6,borderRadius:'50%',background:'#95dcd9'}}),
                h('span',null,`${t('latestImport')}: ${latestImport.title} ` + (latestImport.topic_title?`(${latestImport.topic_title})`:'') + ' · ' + t('autoSwitched'))),
              notice&&h('p',{role:'status',style:{margin:'8px 0 0 0',color:'#95dcd9',fontWeight:500}},notice)),
            visited&&h(ControlPanel,{key:sessionId,sessionId,useProjection,t,inputActions,projectId:project?.id??null,active:expanded,
              onConnection:setConnectionState,onObserved:setObserved}))));
        }
        function IslandRoot({usePanelInfo,SessionProvider,renderSlot}) {
          const visible=usePanelInfo(info=>info.activePanelId===null);
          if(!visible)return null;
          // Inherit the root's native session binding. The host omits absent sessions.
          return h(SessionProvider,{empty:()=>null},renderSlot('aiLibrary.island',{}));
        }
        ctx.slots.inject('shell.overlay', () => ctx.slots.register({
          name:'shell.overlay',id:'ai-library-island-root',order:5,locale:namespace,
          children:{'aiLibrary.island':{kind:'single',scope:'session'}},
        }, IslandRoot));
        ctx.slots.inject('aiLibrary.island', () => ctx.slots.register({
          name:'aiLibrary.island',id:'ai-library-island',locale:namespace,
        }, ProjectSelector));
      },
    };
  },
});
