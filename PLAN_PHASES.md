# AI 记忆库实施进度

## 2026-10-13 — Prompt Cache 命中率提升与 Token 极致压缩（IMPLEMENTATION_COMPLETE / TESTED）

用户明确提出建设 AI Library 的核心诉求是“提升 Cache Hit 和减少 Token 消耗”。经分析并实施 Scheme 2（ContextArtifact Lean Mode）与 Scheme 3（Frozen Prefix & Deterministic Cache Layout）。详见 [技术验证报告](work/PROMPT-CACHE-TOKEN-OPTIMIZATION-v1.0.md)。

- [x] **ContextArtifact Lean Mode（超轻量载荷）**：新增 `to_lean_dict()` 与 `lean: bool` 支持。剥离重复的 `stable_context` 与 `dynamic_context` 独立字符串，保留唯一的完整 `combined_prompt`；剔除微秒级 `created_at` 瞬态时间戳及内部调试字段，单次检索 JSON 载荷体积削减 **45% ~ 60%**。
- [x] **Frozen Prefix & Deterministic Cache Layout（确定性前缀与 Prompt Cache 共享）**：重构提示词编译布局，将静态 System Directive 与确定性排序的证据项置于最前端，动态用户 Query 作为最末尾 Suffix。同语义或跨语义检索在相同核心知识上，**前缀共享率达 80% ~ 95% 且 Byte-for-Byte 100% 一致**，最大化触发 Gemini Context Caching、Claude Prompt Caching 及 DeepSeek Chunk Caching 的折价与低延迟特性。
- [x] **MCP 检索协议协同**：`search_knowledge_context` 暴露 `lean` 与 `cache_prefix_layout` 参数；同时支持环境变量 `AI_LIBRARY_MCP_LEAN_DEFAULT` 全局静默激活。
- [x] **专项与回归测试通过**：编写 `tests/test_cache_and_token_optimizations.py`（4 passed），结合核心回归测试共 49 项用例全部 PASS。

## 2026-10-13 — Harness v1.3.1 remote注入修复（IMPLEMENTATION_COMPLETE / TESTED）

- [x] 原生Cordis兄弟服务提供方式准确复现用户报错，补齐Client父级remote依赖，版本1.3.1。
- [x] 修复后父级/commands/Manager访问与卸载清理、全部既有Client DOM功能回归通过；此前测试替身缺口记录在 [修复说明](work/DEEPSEEK-HARNESS-PLUGIN-v1.3.1.md)。
- [ ] 用户已安装包更新/按返回重启、实际Harness页面复核。未自动修改profile。

## 2026-10-13 — Harness v1.3 顶部灵动岛（IMPLEMENTATION_COMPLETE / TESTED）

用户明确选择窗口顶部居中悬浮在聊天画面上，见 [v1.3记录](work/DEEPSEEK-HARNESS-PLUGIN-v1.3.md)。

- [x] 原生shell.overlay + 私有session子slot，非聊天隐藏、有效聊天继承，无宿主DOM扫描。
- [x] 收起主题/连接/上次观察，展开全部已批准按钮和证据/机甲小画面；点击与Esc，首个可用控件焦点，收起保留当前草稿并停止轮询。
- [x] 原生标题栏避让、岛外点击穿透、短视口滚动、触控/焦点/减少动态CSS；独立源码审查修正并复核。
- [x] 发布Client组件功能和实际Harness SlotCore注册/清理合同通过；不会据此宣称完整页面视觉通过。
- [ ] 用户profile安装/按返回重启、桌面/窄屏/短窗口真实视觉与点击/聊天切换/减少动态验收；原有实际模型开发任务和token A/B仍待完成。

## 2026-10-13 — MCP 功能优化与双管齐下 Hook 体系（IMPLEMENTATION_COMPLETE / TESTED）

用户明确要求优化 MCP 功能、完善数据治理、链接 Hook 完善 AI Library 系统。按用户确认采用双管齐下方案（Antigravity 生命周期 Hook + Git 提交 Hook）。详见 [v1.0记录与验收说明](work/MCP-HOOKS-INTEGRATION-v1.0.md)。

- [x] **MCP 敏捷规则与紧凑模式**：`get_usage_rules` 支持 `compact: bool`，规则提取压缩 75% token；工具响应动态绑定版本与变更提示。
- [x] **候选记忆规范化去重**：`CandidateBridge` 引入 `normalize_claim_text` 与 `find_duplicate_candidate`，在提交候选时智能查重防抖，避免重复候选污染。
- [x] **冲突检测与隔离区体检**：新增 MCP 工具 `detect_candidate_conflicts` 与 `audit_quarantine`，支持冲突预检与隔离区错误成因聚合统计。
- [x] **Antigravity 生命周期 Hook**：配置 `.agents/hooks.json` 与 `.agents/run_hook.py`，实现 `PreInvocation` 治理/规则注入、`PostToolUse` 工具变更追踪与 `Stop` 会话安全卡点。
- [x] **Git 提交 Hook**：实现 `pre-commit`（健康检查与临时文件拦截）与 `post-commit`（代码变更自动关联沉淀为知识库 Note 记录），提供 `scripts/install_git_hooks.py` 一键安装。
- [x] **全自动化测试通过**：`tests/test_mcp_optimizations.py`（5 passed）、`tests/test_lifecycle_hooks.py`（4 passed）、`tests/test_usage_rules.py`（17 passed）等全部 PASS。

## 2026-10-12 — Harness v1.2 控制面板 / 只读机甲 / 插件开发（IMPLEMENTATION_COMPLETE / TESTED）

用户确认全部读取控制项、预览后用于下一次回答、自然语言和手动编辑；机甲澄清为只读观察小窗口。见 [v1.2实现与验收边界](work/DEEPSEEK-HARNESS-PLUGIN-v1.2.md)。

- [x] 原生Client三个区域；逐聊天自动开关/预算、项目、连接测试、手动预览与单次使用/取消。
- [x] 会话日志投影恢复、单次消费、读取前重验授权与来源、偏好变更取消/拒绝旧上下文；字符统计不冒充token。
- [x] 当前聊天轻量机甲示意图、位置/动作/状态，按需轮询，不新增驾驶。
- [x] 自然语言临时提案与手动源码编辑、差异与语法检查、明确确认、hash并发/篡改校验、链接隔离、备份与恢复；原生Manager安装新版并保留重启状态。
- [x] 70 passed Python / 9 passed Node；真实Harness会话/命令/投影/pre-step与发布Client React DOM通过。
- [ ] 当前profile安装、完整页面视觉/交互、实际Harness模型编辑提案、真实关闭/恢复与用户验收；未验证模型token节省。

## 2026-10-12 — Harness 全库连接与每聊天项目选择（IMPLEMENTATION_COMPLETE / TESTED）

用户要求连接完整AI Library，再在Harness选择项目，并确认按星图已有项目/主题。scope作为授权分区保留；当前全部资料在default，不以scope模拟项目。见 [v1.1记录](work/DEEPSEEK-HARNESS-PLUGIN-v1.1.md)。

- [x] v1.1.0原生Host+Client bundle，默认全库；输入区下方项目选择器、父子路径、刷新/分页、中英文字、可访问状态。
- [x] 原生用户命令与会话日志投影，每聊天独立、失败保留选择、重放/继承；普通文字与来源不改变偏好。
- [x] 主题及后代成员在检索截断前过滤，索引与fallback均生效；读取中移出剔除，缺失/撤权拒绝，空主题不回退全库，图扩展不越界；精确对象ID优先。
- [x] 48项专题与20项基础 Python / 6 passed Node；实际Cordis Session/commands/projection/pre-step/卸载、实际Client React DOM功能、真实资料副本37主题/原始hash/UNVERIFIED与全库切回通过；0模型请求，无日常canonical修改。
- [ ] 用户profile安装/替换包后的重启、完整页面视觉、真实持久化恢复/fork和机甲联动验收。
- [ ] 同DeepSeek模型token A/B；主题内向量检索、其他独立授权分区及此前未完成的生命周期hooks仍按各自批准范围处理。

## 2026-10-12 — DeepSeek Harness 原生 plugin（IMPLEMENTATION_COMPLETE / TESTED；用户接入待验收）

用户要求先制作插件，随后自行尝试连接。目标按本机真实 Harness `0.2.1-alpha.2` 源码核定，交付 [原生 bundle 与连接步骤](plugins/deepseek-harness-ai-library/README.md)，见 [实现记录 v1.0](work/DEEPSEEK-HARNESS-PLUGIN-v1.0.md)。

- [x] manifest / cordis.patch.yml / Standard Schema 配置 / Host pre-step waterfall / 中英文 metadata / 原作图标；不修改宿主源码或当前 profile。
- [x] 持久化本地 Python bridge；固定 scope / principal，只读内容、来源与冲突、Unicode字符预算、单份证据正文、每次读取新规则及重新检查授权。
- [x] 每聊天机甲、取消和并发请求关联、帧/等待限额、Agent释放与插件卸载；异常清理也等待自身子进程退出。
- [x] 36 passed Python / 5 passed Node；实际宿主源码模块解析 bundle patch、加载、调度与卸载通过；真实资料库副本返回4044字符 / 3条证据，来源hash相符、UNVERIFIED不提升、优雅退出；npm打包清单全部8项运行/显示文件包含。0次模型请求；不修改日常资料、提交或安装依赖。
- [ ] 用户实际 profile 安装 / 宿主 UI / 模型回答 / 机甲观察与关闭信号验收；本机 Node22.18.0 仍需满足宿主要求的 `^22.19.0 || >=24.0.0`。
- [ ] 同 DeepSeek 模型 token A/B；当前不宣称节省率。

attachment-only、自动接续补查、关键决策和任务结束 hooks、按任务依赖降级、来源更新/正式记忆审批未在本版新增；本版仅为含新用户文字的请求前只读预取。原 MCP 保留，后续按现有批准范围逐项验收。

## 2026-10-12 — 机甲查找实用验证与星图原生造型（限定QA_PASSED；视觉用户接受待完成）

用户要求使用机甲查找AI Library并具体计算token；通过真实只读stdio连接登记Codex会话、检索、导航到AI library、进入MCP相关内容、查看目录并读取完整会话摘要。当前主题共79成员，AI library字面匹配74，MCP匹配38；摘要保留INFERRED/UNVERIFIED及原文来源，不把旧15工具等历史快照当作当前状态。日常8000观察区实测真实投影可见，结束后按生命周期清理，无业务资料改写。9次MCP调用的主JSON与请求文本估算共6675（字符/4向上取整），不等同真实模型token/计费；逐项记录、真实Codex日志差值与范围在 `.agent-tmp/spider-lookup-20261012/`。

用户随后指出机甲外观不够体面，明确选择A星图原生探针。已实施 [视觉v1.1](work/SPIDER-MECHA-VISUAL-v1.1.md)：暖白核心、薄片护壳、纤细弧形八足，七应用结构不同，机甲缩放1.4→0.85；无新增依赖或后处理。桌面/手机合同回归两轮通过，0 pageerror；实际模型近景、全部几何/材质dispose和设计检测通过。Codex近景绘制调用29→27，但三角形716→4532，不能据此宣称整体GPU成本下降或60fps。源码备份和实际3D对照截图 `.agent-tmp/spider-redesign-20261012/`；未改后端或重复无关后端全量测试，未commit/安装依赖。最终视觉USER_ACCEPTED和原生宿主登记仍待确认。

## 2026-10-12 — 蜘蛛机甲首版（IMPLEMENTATION_COMPLETE / TESTED / 限定 QA_PASSED；用户验收待完成）

用户要求通过问卷完善“每个接入 MCP 的 Agent 驾驶自己的蜘蛛机甲，在星图及行星内部找资料”概念。15 项选择与右侧悬浮侧栏要求已记录于 [概念 v1.0](work/SPIDER-MECHA-CONCEPT-v1.0.md)，随后“开始吧”授权实施；按 [实施计划](work/SPIDER-MECHA-IMPLEMENTATION-PLAN-20261012.md) 完成 [首版与验收说明](work/SPIDER-MECHA-IMPLEMENTATION-v1.0.md)。

- [x] 确认内部探索、应用独立造型、多会话投影、两种导航、相关内容优先、一跳关联、动画不阻塞、可切换跟随、会话内记录、两种驾驶入口、移动方式、空闲停靠和侧栏只观察。
- [x] 基于受信启动身份映射应用、明确登记稳定聊天 ID、匿名连接诚实显示；不把连接身份当作已识别聊天。
- [x] 四个临时 MCP 工具、普通读取观察、只读 HTTP 快照、跨进程临时记录、权限和真实资料过滤、心跳/结束清理；首导航固定一跳起点，失败资料页事件同事务回滚。
- [x] 七种已知应用结构、自定义结构、八腿机甲、持久层、多投影、停靠点、右侧只读观察和选择跟随；暂停/隐藏/减少动态生效。
- [x] 官方 SDK 与浏览器真实联动两个应用/三个聊天；桌面1440、手机390、跟随/取消/返回、空闲/失联/结束、无业务写请求与0 pageerror；独立复审最终PASS。
- [ ] 原生宿主逐个登记/结束信号验收、视觉用户接受、用户设备整体星图流畅度验收；本轮未安装宿主 hooks。

专项32 passed（41.80秒），最终全量396 passed / 1 skipped（165.66秒），七项纯展示回归PASS；实际stdio→8876→Chromium通过；日常8000新观察接口与桌面/手机已只读验收，无需本任务重启，未向日常资料写入测试内容。证据在 `.agent-tmp/spider-mecha-20261012/`：backend.xml、full-final.xml、browser/report.json、live-report.json、daily-report.json。独立复审问题经过红测试、修正、再次验证；失败记录归档，源码备份可恢复。未安装 Scrapling、修改个人宿主配置或实施网页抓取、独立探索 Agent 和侧栏任务派发。机甲层CPU更新已有三投影测量，复杂场景headless帧率偏低，未宣称整体流畅度达标；具体数据见实现说明及live-report。其他阶段状态保留。

## 2026-10-12 — Backend README成品对照与Saved Views首批后端（IMPLEMENTATION_COMPLETE / TESTED；限定后端，用户验收待完成）

用户要求对照当前成品与Backend README、检查未完成事项并开始执行。找到原交付的Backend v1.7并按原字节复制到work，保留旧v1.3/v1.4；结合后续DECISIONS逐项核对，形成 [完整对照与优先序](work/BACKEND-README-AUDIT-20261012.md)。现有星图Tracker不是全后端验收表，不复用其完成百分比。

- [x] Saved Views后端：Notes、Memory、Backlog、Logsheet独立声明式筛选/排序；未知schema/字段/SQL/非法时间拒绝，scope不扩大，每次读取当前状态，Memory过滤失活/替代/时间无效记录。
- [x] 定义不可变历史、expected_revision CAS、无变化幂等、软归档/恢复、独立组织审计和同事务outbox；不复制业务内容，不污染System Memory Logsheet，随快照恢复。
- [x] 本机owner REST schema/list/create/get/update/history/execute；新增三个只读MCP工具，总34个。Agent不能修改owner定义，执行绑定当前修订与保存的页预算。
- [x] 实际微秒精度过滤/排序/时间资格、明确时区与极端UTC溢出拒绝、100条页预算及SQL协作式超时；不宣称端到端硬时延或大规模性能已达标。
- [ ] Saved Views网页保存/更新/导航入口与日常服务加载：本轮未实施/未重载；日常8000 schema端点实测仍404，旧MCP进程需重连。
- [ ] 其他缺口按对照表继续：统一资料preview/confirm、真实缓存复用、完整历史检索、执行评估/策略晋升、真实会话lineage、自动整理/健康评估、hooks/变化检查/可信批准绑定及大规模基准。延后范围不自动启用。

验证：全项目基线 **326 passed / 1 skipped，140.17秒**；最终 **364 passed / 1 skipped，211.51秒**。Saved Views专项 **38 passed，19.55秒**（含真实stdio）；独立复审再次PASS，无剩余可行动问题。8875独立HTTP启动、保存和执行PASS。发现的时间舍入/UTC溢出经过4项红测试复现、修正和再次验证；失败记录已归档。没有向日常内容或保存视图写入测试数据，没有自动commit或安装依赖，没有重跑无关UI验收。

说明：[Saved Views后端合同](work/SAVED-VIEWS-BACKEND-v1.0.md)、[已执行计划](work/BACKEND-SAVED-VIEWS-PLAN-20261012.md)。证据：`.agent-tmp/backend-readme-audit-20261012/{baseline.xml,final.xml,saved-views.xml,http-report.json}`；源代码备份在同目录code-before。当前前端/日常可用状态与后端代码完成分别记录。

## 2026-10-12 — 使用规则设置与一键更新（IMPLEMENTATION_COMPLETE / TESTED / QA_PASSED；用户验收待完成）

用户在完成规则问卷后要求自行微调并保存一键更新。按现有可信本机 owner 设置入口实现规则编辑及发布，不修改个人客户端配置。

- [x] “设置 → Agent 使用规则”：读取、主题、import、记忆、结束触发与异常条件；未保存提示、恢复问卷默认草稿、版本历史、发送状态、额外规则候选。
- [x] 同一 SQLite 内保存不可变修订与规则哈希；owner 发布校验、expected_revision 冲突保护、无变化幂等、历史随快照恢复。
- [x] 新 MCP `get_usage_rules`；成功响应附带当前版本，首次/版本改变另附完整规则，原JSON及结构化结果形状保留；旧代码进程初次重新连接。
- [x] 撤权/到期握手不泄漏用户政策，规则发送不当作已执行；MCP transport 按需导入，避免包导入初始化默认网页数据库。
- [x] 失败保留草稿，保存结果不确定时提示重新核对；读取挂起可关闭设置，规则请求有超时。
- [ ] 生命周期 hooks、统一自动来源检测、语义分类/独立议题审核、可信单次审批绑定仍未实施；规则设置不等于这些执行能力已启用。

验证：规则 / 设置 / 权限 / 真实 MCP stdio 专项 **49 passed，99.74 秒**，包括撤权和到期握手、实时版本、并发CAS、快照恢复以及MCP导入隔离；独立复审无剩余问题。隔离8874服务的桌面1440与手机390浏览器检查通过，含保存、重开、冲突、网络失败、草稿保护及读取挂起可关闭，0 pageerror。没有重跑全项目或所有原生客户端QA。证据：`.agent-tmp/mcp-usage-rules-20261012/pytest-results.xml`、`browser/report.json`。

部署边界：981 文件的资料快照保存在 `.agent-tmp/mcp-usage-rules-20261012/daily-before-web-deploy/`。早期API模块级导入初始化过日常规则元数据，不应把全部早期探针写成完全隔离；之后已隔离默认app目录，并修复MCP包导入副作用。自动审批曾分别拒绝重启日常8000服务、在8001启动新版服务的命令，均只返回 `blocked by policy`，未提供详细理由；命令没有执行，也未换工具绕过。最终复查发现日常服务已在本轮期间由其他操作重载，具体触发方未核定；直接读取确认其数据目录仍为日常data，规则API返回200、v1与24个字段。桌面1440和手机390实际打开设置、读取24项并关闭通过，0 pageerror，规则版本保持不变；未向日常规则提交测试修改。证据：`.agent-tmp/mcp-usage-rules-20261012/live-readonly-report.json`。因此不再需要本任务发起重启。

文档：[当前规则与接入](work/MCP-USAGE-RULES-v1.1.md)。日常服务加载与客户端初次重新连接分别验收。

## 2026-10-12 — MCP 使用规则与 hooks 要求（USER APPROVED；运行接入待实施）

已完成问卷第 1–15 项，整理为 [MCP 使用规则 v1.0](work/MCP-USAGE-RULES-v1.0.md)，并同步 README、MCP 连接说明和决定索引。本轮仅编制规则与核对源码，没有修改服务端代码、个人客户端配置或日常知识数据。

- [x] 确认所有连接 Agent 的读取时机与已授权项目范围。
- [x] 确认同主题、多议题摘要、去重后 >5 子主题条件，以及保留原项目归属和人工修正。
- [x] 确认 import 检测范围、任务开始 / 结束触发、增量审批、版本保留与摘要复核。
- [x] 确认每任务一份审批清单、正式记忆候选条件、断连与冲突暂停边界。
- [ ] 将版本化规则嵌入连接级 instructions，并验证客户端实际接收。
- [ ] 逐个核查客户端生命周期事件、实现自动读取 / 补查 / 结束预览，验证重复事件和失败处理。
- [ ] 建立已导入来源的统一检测与差异预览，实施受影响摘要的复核标记。
- [ ] 对齐分类建议：现有代码按标题标记分组、默认 >=5；本轮要求去重后 >5 且形成独立议题。
- [ ] 实施可信人类确认与实际变更的绑定，验证未确认、过期确认及只读连接不会写入。
- [ ] 逐客户端端到端验收；不支持自动触发的宿主明确报告能力缺口。

本轮验证限定为文档完整性、链接与源码边界核查；没有运行功能或浏览器回归。星图设置按钮调试保留为待继续项。

## 2026-10-12 — 当前 Codex 直接接续与收尾（TESTED）

用户明确“你直接执行就好，不交付antigravity了”。本轮由当前 Codex 直接完成已批准 Gateway 范围，Antigravity 专属接入验收不再作为本轮收尾条件。跨系统兼容方向与统一执行代理延后的决策不变。

- [x] 当前 Codex 使用真实 MCP SDK / stdio 子进程读取本轮交接 v1，核对 7 个引用文件的实际 SHA-256 全部匹配，并保存 RECEIVED_ONLY。
- [x] 本轮专项回归：`tests/test_work_handoffs.py`，14 passed，48.01 秒，退出码 0。实现代码没有新增修改；此前全量 255 passed / 1 skipped 是上一轮全量执行的结果。
- [x] 通过 `revise_work_handoff` 按 expected_revision=1 回写最新执行安排和验证结果至 v2，目标提示改为 codex；`get_work_handoff` 回读与提交结果一致。
- [x] 更新当前执行安排与 README；v1 JSON / Markdown 保留历史，不覆盖。没有改写个人客户端配置、提交 Git 或修改日常记忆数据。

证据：`.agent-tmp/gateway-direct-continuation-evidence.json`、`.agent-tmp/gateway-direct-tests.json`、`.agent-tmp/gateway-direct-pytest.txt`。操作资料目录为 `.agent-tmp/gateway-release-data/`，本机交接 ID 为 `handoff-178b24bae96b44ec8b1d96952c199088`，当前 revision=2。

边界：这是当前 Codex 通过真实 MCP 工具读取并执行后回写的接续过程；未测试其他宿主的模型自动选择工具或后台自动唤醒。用户验收仍独立记录。此前 Gemini 连接失败根因未确认的记录继续保留。

## 2026-10-12 — Gateway 跨系统工作交接（IMPLEMENTATION_COMPLETE / TESTED）

用户选择统一交接包，让 Codex、Antigravity 等不同系统读取进度并继续工作；本轮仅实施该范围。统一执行代理仍延后。

- [x] 严格 v1 payload：目标、批准范围报告、决策来源、明确进度、文件 / 版本 / 哈希、验证证据、阻塞、下一步和待决定事项。
- [x] 两个独立 SQLite 表保存不可变交接版本和 RECEIVED_ONLY 确认；与现有 Memory Backlog / Logsheet 分开，写入 / outbox 原子提交。
- [x] 七个 MCP 工具：创建、修订、读取、列表、JSON / Markdown 导出、JSON 导入、按版本 / 哈希确认。固定 scope / principal、只读与持久撤权沿用现有边界。
- [x] CLI `scripts/manage_handoffs.py` 经同一工具边界交换文件；幂等提交、CAS、防重复 JSON 键、大小上限、不覆盖输出、独立目录导入与快照恢复。
- [x] 14 项专项测试通过；审核修正后全量 255 passed、1 skipped，108.24 秒。没有前端修改，因此本轮没有重跑浏览器 QA。
- [x] 最终已安装客户端串行复验：Codex app-server 写交接，Gemini 已安装 McpClient 模块读取 / 确认 / 修订 v2；跨 scope 拒绝和隔离配置精确回滚通过。证据 `.agent-tmp/c2-clients-uolc5rqe/`，30 个工具。独立审核发现的两个导出 / 来源问题已修正并复审。
- [x] 更新 [Gateway 使用与验证边界](work/GATEWAY-COMPATIBILITY-v1.0.md)、MCP 说明、README 与决策索引。原提案保留历史状态。
- [x] 在隔离资料目录生成本轮真实交接并导出 [Markdown](work/GATEWAY-HANDOFF-20261012.md) / [JSON](work/GATEWAY-HANDOFF-20261012.json)，不写入日常 `data/`；用于接收系统读取本轮进度。

未验收：实际 Antigravity 宿主、模型驱动自动续接、跨机器联网、USER_ACCEPTED。已安装客户端测试没有模型 prompt turn，不代表任务理解或完成质量。

残余风险：与全量测试并行的一次 Gemini CLI `mcp list` 返回 Disconnected；相同配置诊断及完整串行复验均通过。已核实其默认连接期限 5 秒且吞掉具体异常，但原失败根因未确认；记录保留于 `agent_records/failures/active/gemini-mcp-list-disconnected-under-parallel-validation.md`，不写成已修复。

## 2026-10-12 — AI Library / Memory Constellation 开发交接与基线审查（AUDITED / TRACKER ESTABLISHED）

按 ChatGPT 讨论确认规格及用户最新交接指令，完成项目源码审查与架构差异分析。

- **基线核验**: 运行全量回归，测试结果 **241 passed, 1 skipped (80.71 秒)**；`tests/universe_views.mjs` 纯表现检查 PASS；`browser_neural_ui.cjs` 桌面/平板/移动端检查 PASS；`browser_universe_hub.cjs` 核心 Hub 与多批次加载检查 PASS。0 页面错误，0 非预期 API 写请求。
- **视觉基准确认**: 依据用户指示（“还回去旧的模型和链接方式”），星图已彻底恢复线框水母（`makeJelly`）、二十面体发光核心（`makeCore`）及二次贝塞尔归属线，并完成真 3D 深度布局、画布点击自动收起目录、悬停显示标签及镜头平滑过渡。
- **Implementation Tracker 建立**: 依据严格证据化规范创建 `work/IMPLEMENTATION-TRACKER-20261012.md`，25 项功能节点严格划分为 12 项 Implemented、3 项 In Progress（Skill/MCP 模型集成与中央 Topic ID 关联）、10 项 Not Implemented（Agent 导航分级、双视角切换、4 种彩色牵引线、>5 相似聚类与多层子目录等），杜绝虚报。
- **差异分析与架构对齐**: 产出 `work/ARCHITECTURE-HANDOVER-20261012.md` 与 `work/GAP-ANALYSIS-20261012.md`。确立“正交多维投影模型”（保留中央 Topic ID 同时支持 Agent 视角与主题视角切换），以及“线框水母主题 + 空间站/战舰特定模型”的混合渲染方案。
- **开发约束遵守**: 遵守“先完成审查和差异分析，再决定是否修改源码”指令，本轮专注完成架构文档、Tracker、差异分析与 Decision Log 对齐，待用户确认 MVP 顺序后启动源码实施。

## 2026-10-12 — 交互规则确认与模型整改提案（USER APPROVED / PROPOSED）

用户确认文件夹层级加跨分类关系、中央个人空间 / 笔记、外层项目、不可点击的内部模型预览、按议题聚合、完整总结及主动展开的演变过程、分类纠正后固定、短标签 / 按需标题、局部中心加紧密内容群、布局拖动和独立整理模式、最后访问位置标记。Agent 笔记建议经确认加入；Skill 保存完整文件供按需下载；检索和提示词缓存均为目标。上述为要求确认，不是实现状态；具体模型及制作路线仍为提案。见 `work/AI-Library-Frontend-README-v2.0-DRAFT.md`。本轮只更新文档。

## 2026-10-11 — 整体呈现重新梳理（DISCOVERY / REVIEW_REQUIRED）

用户回答详细问卷，明确个人笔记 / 未处理资讯、AI 记忆及 Skill / MCP 收藏的用途和边界；希望聚合完整内容、局部空间浏览、半屏阅读、真实 3D 和更多模型。树状布局、全库对象可见范围、名称规则及 Agent 补充 / 工具同步仍待澄清。新需求记录于 `work/AI-Library-Frontend-README-v1.9-DRAFT.md`；没有开始实施新界面或自动同步，日常界面仍为 v1.8。

## 2026-10-11 — Neural Vault 参考适配（TESTED / 体验 REVIEW_REQUIRED）

按用户提供的真实仓库与本地预览，完成黑色画布、线框水母主题、中央发光 Notes 核心、右侧图例、顶部导航、底部搜索和浮动阅读面板。保留 3D 旋转、真实资料、技能清单、无分页、退出、编辑 / 来源 / 审核边界。桌面 / 平板 / 手机三组新界面检查与独立测试库四组原流程回归通过；中心 Notes 与多批次加载回归通过。

用户随后明确表示整体展现还不理想。当前版本未获用户体验认可；已停止外观微调，等待确认优先改变分支展开、空间构图或阅读主次。详细实现与验证边界见 `work/AI-Library-Frontend-README-v1.8.md`。旧前端备份保留于 `.agent-tmp/neural-redesign-20261011/frontend-before/`。

## 2026-10-11 — 主题内部预览与本机 Skill 清单（IMPLEMENTATION_COMPLETE / TESTED）

主题外围展示真实直属内容，直接点击可读。Skills 入口使用本机技能元数据名称，目录显示名称与来源，点击查看简介和位置；原四条历史资料保留。扫描时发现 492 个不同内容条目，六份 Gemini 头部元数据异常，以目录名回退并明确提示。20 项后端针对性检查通过，桌面 / 手机 Skill 流程和内部预览检查通过。未安装、运行或改写技能；没有 MCP 清单导入。规格和限制见 `work/AI-Library-Frontend-README-v1.7.md`。

后续：用户已提供 neural-vault 仓库；参考适配与最新体验反馈见本文件顶部 v1.8 记录。

## 2026-10-11 — 移除类型切换与增加退出（IMPLEMENTATION_COMPLETE / TESTED）

按用户标注移除三项类型切换；进入主题、我的笔记或其他内容结果后显示“← 退出”，返回总览时恢复混合内容。中央 Notes 主星保留，目录位置收紧。1440×1000、612×832、390×844 浏览器检查 PASS：切换组不存在、主题退出、Notes 入口、全部内容恢复两类、无横向溢出；0 pageerror、0 API 写请求。手机截图已查看。规格见 `work/AI-Library-Frontend-README-v1.6.md`；用户体验尚待验收。

## 2026-10-11 — 固定笔记主星与连续星图（IMPLEMENTATION_COMPLETE / TESTED）

按用户最新确认，中心改为较大的固定“我的笔记”入口，打开当前资料范围内全部 Notes；取消界面分页，自动分批加载当前层级全部结果。主题与内容围绕主星呈现，来源、编辑、审核及范围隔离沿用既有规则。

桌面 / 手机实际浏览器检查通过：点击球体打开全部 185 条 Notes；主题 63 项全部加载和渲染；目录阅读与返回可用；510 项模拟响应的跨批次读取、取消和缺失检测通过。0 页面异常、0 API 写请求。指定场景 QA_PASSED，尚待用户体验验收；大规模渲染性能未测。规格：`work/AI-Library-Frontend-README-v1.5.md`；证据：`work/UNIVERSE-HUB-UPDATE-20261011.md`。以下 v1.4 记录为此前阶段历史。

## 2026-10-11 — 知识宇宙首版（IMPLEMENTATION_COMPLETE / TESTED）

用户批准整案后，完成真实 3D 主界面、混合 Notes / Memory、主题 / 子主题、持久集合整理、全局字面关键词搜索、类型过滤、稳定阅读、目录与 WebGL 后备，原审核 / 来源 / 冲突 / 修复功能保留。Three.js 0.180.0 本地提供，不需要运行时 CDN。

验证：全量后端 **237 passed、1 skipped（127.03 秒）**；桌面 / 手机共 9 组宇宙浏览器检查与 4 组旧流程集成检查 PASS，0 pageerror。日常 8000 服务已启动并只读实测，9 个主题、185 Notes、85 图节点；日常浏览器检查没有写请求。按既有标题标记一次迁移 250 条集合引用，重复调用增加 0 条；原有 21 张受保护表逐行一致，198 原文 / 清单文件哈希一致。旧前端和 199 文件数据快照保留。

指定场景 QA_PASSED；用户体验验收尚待进行。大型数据性能、语义排序、MCP 集合操作、主题删除 / 改父级、Saved Views、网页 / 文件导入不在此次完成声明内。完整记录：`work/UNIVERSE-QA-20261011.md`；实施计划：`work/UNIVERSE-IMPLEMENTATION-20261011.md`。

## 2026-10-11 — 宇宙界面重设计（需求发现记录，后续已批准实施）

用户认为当前 UI 杂乱，要求通过提问重建界面。已确认：3D 自由旋转与穿行；主题太阳、内容 / 子主题混合行星；Notes 与 Memory 按子主题同屏但数据独立；第一版接通可持久保存的新建 / 重命名 / 加入 / 移出操作。新方向替代先前三 Tab 的主浏览要求，保留来源、审核和维护能力。

设计记录在 `work/AI-Library-Frontend-README-v1.4.md`，v1.3 原文件保留。此需求发现记录当时只更新文档；用户随后批准整案，实现与新验证见本文件顶部交付条目，不复用此前旧 UI 的 PASS 作为本版验收。

## 2026-10-11 — 前端 README 对齐（指定场景 TESTED / QA_PASSED）

沿用前端 v1.1 与后续 v1.3 已确认方向，保留三 Tab、Notes 居中编辑、Memory 右侧详情及维护布局。新增统一“＋ 添加资料”文本笔记入口、可收起的图谱目录、按已有标题标记筛选项目；改善图谱标签避让、全景稳定性、来源标识、待核验说明和窄屏布局。修正日志 ACCEPTED 被显示为失败及时间字段不显示的问题。切换 Tab 保留图谱选择与视图。

桌面 1440×1000、窄屏 390×844 两轮浏览器检查，最终 6 组检查 PASS，0 pageerror、0 非 GET API 请求；涵盖草稿保护、焦点恢复、来源跳转、项目筛选、视图保留、全景边界、标签避让及减少动态模式。JavaScript 语法检查通过。本轮未修改后端或知识数据；未重新运行全部后端测试。

边界：项目筛选来自已加载节点标题，搜索时仅筛选当前返回结果；不是正式 Collections。Saved Views、网页/文件采集适配器和持久集合管理尚未接入，不能将 v1.3 或 F0–F4 整体标为完成。详细证据见 `work/UI-README-UPDATE-20261011.md`；截图目录保留本轮开始时的日期 `20261010`。

## 2026-10-10 — 跨聊天历史导入（本批 TESTED）

用户确认跨聊天记录按已有项目/文件夹归类，无明确归属时按内容归类；随后明确提取结果应进入 AI Memory，并授权“开始写入吧”。本批已通过真实 MCP 写入 52 条 L2 / INFERRED / UNVERIFIED 记忆及 181 条来源/目录笔记，覆盖 29 个会话中的 27 个有正文会话。另 2 个仅保存元数据。原有 4 条 Notes、6 个节点、5 条边和 1 个候选逐行核对未变。

已实现本批解析、按会话 ID 合并、源消息定位、凭据遮蔽、分段、Agent 摘要和候选接受；所有记忆带 DERIVED_FROM 来源关系。分类以标题与目录笔记呈现，scope 保持 default。52 条新记忆提交 EmbeddingGemma 2 向量；3 组真实 MCP 混合检索检查返回目标完整内容及来源版本。7 项导入测试通过，浏览器搜索、查看关系、打开原文检查通过。142 段原文重组与副本 SHA256 一致；原始日志未改写。

边界：仅本机固定快照，非云端账号全部历史；70 个附件引用未导入二进制；未实现 Collections 界面、自动持续同步或全部历史事实复核。来源 Notes 仅关键词索引；27 个空正文 Source 图节点无向量，因此全图 coverage 仍标 partial。本次 3 组抽查不覆盖此前中文排序质量缺口。详情见 `work/CHAT-HISTORY-IMPORT-20261010.md`。

## 当前恢复部署单元 — 2026-10-09 至 2026-10-10（部署 TESTED；检索质量 REVIEW_REQUIRED）

用户已要求“继续部署，测验”。恢复既定范围：先回归暂停前写入边界，再部署已选的 EmbeddingGemma 2，真实中文检索与 Codex/Gemini CLI 隔离接入验证，备份后更新本地服务。模型运行在独立 Agent 工具进程，backend 不导入模型依赖。固定官方 revision，默认原生 768 维、CPU float32 文本推理，保留原文空白及 content-only hash；明确 query/document 提示前缀，超长拒绝而不静默截断。测试资料使用隔离数据库。个人客户端配置保持原样，生成可审查的配置计划。此单元不包含 harness 聊天连线或子 Agent 分支方案，也不代表所有阶段完成。

最新结果：

- [x] 固定 `google/embeddinggemma-2` revision，14 个官方文件完整性校验；独立 CPU 文本 MCP（`embedding_status`、`embed_texts`），保留正文、超长拒绝及特殊标记字面编码。后端依赖未添加模型运行库。
- [x] 全量回归 **220 passed、1 skipped，95.12 秒**；跳过项为 Windows 符号链接环境限制。18 项新适配器检查、2 项 backfill 检查包含在总数中。32 项实际浏览器流程通过，0 pageerror。
- [x] Codex/Gemini CLI 隔离接入完成真实嵌入生成、跨客户端向量复用与配置回滚；23 个知识工具 + 2 个独立嵌入工具。证据 `.agent-tmp/c2-clients-al2pz0dx`。个人配置未读取/改写，无交互式模型 prompt turn。
- [x] 日常资料快照 `.agent-tmp/backups/pre-embedding-deploy-20261009`（17 文件）校验通过；3 条笔记和 6 个知识节点共提交 **9** 份向量。原始文件与 notes/nodes/edges/candidates/历史表前后哈希一致；再次运行提交 0 份，无重复。证据 `.agent-tmp/daily-embedding-deploy-evidence.json`。
- [x] 2026-10-10 继续时原服务进程已停止，核对端口空闲后重新启动；当前 `127.0.0.1:8000` 首页 200、health healthy、关键词 lag 0、9/9 向量已投影、backend model_loaded=false。证据 `.agent-tmp/daily-embedding-health-20261010.json`。
- [ ] **中文排序 QA_FAILED / REVIEW_REQUIRED**：12 条合成短文、8 问的小样本中，纯向量首位 6/8、前三 7/8；现有 RRF 混合首位 5/8、前三 6/8。预设全部问题前三命中检查未通过，保留失败问题和断言，未通过调样本或调阈值宣称完成。真实报告 `.agent-tmp/embedding-acceptance-lbav8yk0/report.json`，详细说明见 `work/EMBEDDING-DEPLOYMENT.md`。

暂停前共用 Schema 写入边界已随本次全量回归并部署；全部 20 类独立 Schema、MCP 受限结构化属性提交仍未完成。浏览器搜索仍走关键词，Agent 可经 MCP 使用语义检索；自动后台补向量未实现。GPU、正式个人配置合并、交互式 Agent 验收、领域保留集/规模测试、Collections 等继续保持未完成。完整项目未标记 USER_ACCEPTED / CLOSED。

依据：`work/AI-Knowledge-OS-Backend-README-v1.4.md`、`work/AI-Library-Frontend-README-v1.0.md`（内容版本 v1.1）。

## 2026-10-07 — 本地核心流程修复与交付

用户已要求根据两份 README 开始生成。沿用现有代码与技术栈，优先完成可核查的本地单用户闭环。此计划不是完整架构验收，也不把 README 中的建议转为已批准战略决定。

- [x] 检查现有实现和基线：30 项 pytest 通过，但未覆盖关键浏览器操作。
- [x] 笔记：版本冲突返回 409，旧版本保持不变，保存失败保留草稿。
- [x] 候选：显示实际可审核状态，接受/拒绝的终态保护与重复接受幂等。
- [x] 来源：接受时验证实际 source revision、scope、hash，持久化 DERIVED_FROM 与审核日志；接受不自动等于 L3 验证。
- [x] 浏览器：笔记草稿保护、快速 Tab 切换、记忆列表等效键盘入口、维护关联定位。
- [x] 验证：独立临时数据库、51 项 pytest、11 个实际浏览器场景通过。
- [x] 交付：本地启动脚本、操作 README、已实现/未实现边界与 QA 记录。

新增修复（BUG，未扩展战略范围）：中文关键词与无匹配检索、检索缓存失效/Plane 隔离、图扩展 scope 过滤；维护事项与 Logsheet 同事务、版本冲突保护；移除虚构的“自动扫描已完成”提示。

状态：本轮核心流程 IMPLEMENTATION_COMPLETE、TESTED；指定浏览器场景 QA_PASSED。USER_ACCEPTED 尚待用户实际使用。完整架构尚未完成，不能标记 CLOSED。

修改区域：`backend/notes`、`backend/candidates`、`backend/adapters/api`、`frontend`、`tests`、`scripts`。临时脚本、数据库和截图放在 `.agent-tmp/`；故障记录放在忽略的 `agent_records/`。

验证步骤：先写能复现上述错误的测试并确认失败，再修复；运行 `.venv/Scripts/python.exe -m pytest -q`；用隔离服务实际创建中文笔记、生成候选、审核、检查关系与日志、重载、冲突保存和快速切换。测试成功只支持对应测试范围的 TESTED/QA_PASSED。

## 2026-10-08 — MCP 连接与可恢复关键词检索（IMPLEMENTATION_COMPLETE / TESTED）

目标：外部 AI Agent 可通过真实 MCP 协议操作本地记忆库；检索不再仅扫描前 100 个记忆/50 个笔记。沿用 SQLite、Notes/Candidate/Memory 的现有边界，不选择内置模型供应商或向量数据库。

- [x] MCP：`backend/adapters/mcp`、`scripts/run_mcp.py`；官方 SDK stdio，固定 scope、只读模式，版本检查与真实 Agent 审核日志。测试使用真实子进程协议连接。
- [x] 索引：`backend/storage/canonical/sqlite_storage.py`、`backend/core/retrieval/keyword_index.py`；SQLite FTS5 派生索引、持久递增事件序列、连续检查点、有限重试/DLQ、事务重建，失败保留旧代。
- [x] 检索：`backend/core/retrieval/retrieval_coordinator.py`；覆盖全部当前文档，精确 ID，索引滞后时 canonical 回退，scope/active/当前有效期复核；标明预算为估算而非任意模型 tokenizer 的硬保证。
- [x] 接入：`backend/adapters/api/app.py` 生命周期 worker、索引状态/重建 API；浏览器搜索实际查询后端并保留结果来源。
- [x] 验证：新增范围/重放/中断/恢复/超过旧扫描上限的回归测试，运行全部 pytest，真实 MCP 客户端与浏览器场景，独立代码审核。
- [x] 文档：更新运行与 MCP 连接说明，记录准确的完成边界与验证证据。

实现级约束：无 Git 提交/分支变更，无 Agent 客户端配置写入，无公网监听；原始笔记和历史保留。索引为可重建派生数据，SQLite canonical 保持权威。向量语义检索、完整多用户鉴权、历史 as-of 记忆快照、语义冲突判定仍延期。

验证结果：74 项 pytest 通过（含 8 项真实 MCP 子进程场景）；11 项原有浏览器场景与 7 项新增搜索场景通过，0 pageerror。独立审核发现的 MCP 重复接受 scope 泄漏、跨 scope 迟到图谱响应、索引旧代积累均已修复并回归。详见 `work/QA-2026-10-08.md`、`work/MCP-CONNECTION.md`。

本阶段指定场景 QA_PASSED；尚未在用户选定的实际 Agent 客户端配置并验收，未标记 USER_ACCEPTED / CLOSED。

## 后续范围

## 全阶段目标（2026-10-08 用户要求：work until all the phases done）

完整目标以 Backend §78 的 P0–P6、§92 的 C0–C3 及 Frontend §14 的 F0–F4 为准。上面的两轮交付不是这些阶段全部完成。后续逐项按 `work/PHASE-ACCEPTANCE.md` 审核证据，不能以当前测试通过替代完整验收。

当前实施顺序：P0/P1 缺口（版本历史、访问授权/撤销、冲突/隔离与恢复）→ P2 有界证据和预算 → P3 混合/层级检索和基准 → P4 缓存 → P5 受控演化；C0–C3 并行准备能力管理，F0–F4 随真实接口补齐。P6 在明确具体扩展需求后执行，不能把可选扩展擅自定义为已完成。

canonical node/edge 同事务历史快照、版本冲突保护和受当前 scope 限制的历史读取已实现。迁移只保存现存状态，不虚构已丢失历史。后续核心单元为 P1 冲突/隔离恢复与 P2 完整证据/预算，能力管理继续补齐真实状态和 UI。

P3 嵌入来源已确认：完全由外部 Agent 提交，后端不加载模型。向量版本/模型/维度契约、持久索引、RRF 和 MCP/REST 接口已经实现，测试使用明确标注的向量夹具；语义质量基准仍需真实模型输出。C2 两个客户端已选定 Codex、Gemini CLI；P6 的具体需求仍待确认。

按已确认方向，模型理解与提取由通过 MCP 连接的外部 Agent 提供，不再将内置模型供应商视为必选项。持续自动摄取、语义冲突判定、完整权限系统、真实嵌入/大规模性能基准、第三方 Skill/MCP 实际安装授权及未来 harness 接入尚未完成。完整架构保持 IN_PROGRESS；战略扩展需要单独确认。

## 2026-10-08 — 版本、外部向量与指定客户端增量

- [x] Canonical node/edge 同事务历史快照、CAS 版本保护及当前 scope/活动状态约束的历史读取；迁移不虚构旧版本。
- [x] 持久 principal/scope 授权与撤销，写事务内复核及返回前复核，重启不恢复已撤销授权。通用远程多用户鉴权仍未实现。
- [x] 外部向量不可变提交、当前 revision/hash 验证、空间隔离、派生索引重建、RRF、缺向量/期限/故障元数据；18 个 MCP 工具。
- [x] 能力资源路径防护、完整文件/hash、不可覆盖版本、绑定固定版本；客户端计划保留无关配置和精确回滚。
- [x] 实际 Codex app-server 工具调用；Gemini CLI 连接及安装包 McpClient 模块复用；两客户端隔离配置回滚。修复 Gemini JSON 列表结构化兼容问题。
- [x] 本轮验证：150 passed、1 skipped（Windows 不支持测试所需符号链接），13 个真实 MCP 协议场景包含在总数中；18 个浏览器场景通过。实际客户端证据：`.agent-tmp/c2-clients-502yvi_b`。

状态：上述增量 IMPLEMENTATION_COMPLETE / TESTED，指定 UI 场景 QA_PASSED。完整 P0–P6/C0–C3/F0–F4 仍未通过所有退出条件。个人客户端配置未改写，交互式模型工作流及 Skill 跨客户端复用未验收。接入计划见 `work/C2-CLIENT-ACCEPTANCE.md`。

## 2026-10-09 — 绑定与权限生命周期增量

- [x] Profile revision 迁移，固定版本升级/恢复、重复绑定无操作、解绑保留其他客户端与所有共享版本。
- [x] 绑定变更、独立 CapabilityAudit、outbox 同事务；并发写仅一个成功，审计失败整个操作回滚。变更后兼容性回到 UNVERIFIED。
- [x] 正式绑定/解绑、配置计划和独立审计 REST 接口；配置计划只返回差异，不写客户端文件。库内解绑不伪装成运行中连接已撤权。
- [x] 持久授权期限及事务提交前复核；默认保留既有期限，移除期限需显式指定。撤销/到期跨重启保持拒绝。
- [x] 本机用户权限管理 CLI `scripts/manage_mcp_access.py`，不向 MCP Agent 暴露自我授权工具；查看/撤销/只读/恢复均受现有记录和版本检查限制。
- [x] 全部 pytest：158 passed、1 skipped；新生命周期/权限测试覆盖并发、失败回滚、共享资源保留、旧 schema、到期提交、真实 CLI 操作。仍未标记 C3 整体完成。

剩余：连接健康/安装/授权/兼容性独立状态持久化与界面、Skill 复用实际验收；P1 冲突/隔离与 P2/P4/P5 完整退出条件仍待实施。真实嵌入模型信息已向用户询问，等待期间继续不依赖此信息的工作。

## 2026-10-09 — 冲突核心与证据截断增量（TESTED，接入未完成）

- [x] 外部冲突报告核心：精确参与版本与来源检查、候选置 PENDING、阻止未解决冲突候选接受；原有决策保留。冲突、关系、候选状态、日志同事务。
- [x] 本机用户审核核心：版本检查、关闭理由与精确来源证据；关闭冲突不自动改写决策或提升记忆可信度。
- [x] 检索先匹配相关冲突再限制数量，超过上限明确 partial；证据标明实际冲突 ID 和未独立验证状态。
- [x] 上下文按完整证据单元保留或省略，不把结论与来源/冲突警告截开；仍为字符估算预算，未声称任意模型 tokenizer 硬保证。
- [x] 全量测试 163 passed、1 skipped；随后扩充冲突边界测试并单独运行 6 passed（关闭后警告消失、跨 scope/无关冲突不进入证据、预算边界）。
- [ ] 冲突 REST/MCP 操作入口、用户审核界面、隔离恢复仍待接通。日常 8000 服务未载入本轮代码，不能将核心测试视为端到端验收。

用户要求提供嵌入模型选项；比较与推荐见 `work/EMBEDDING-OPTIONS.md`，状态 PROPOSED，等待用户选择。未调用付费 API 或安装模型，未新增已批准决策。

## 2026-10-09 — 冲突报告与用户审核接通（指定范围 TESTED / QA_PASSED）

- [x] MCP 增至 20 个工具：新增 `report_memory_conflict`、`list_memory_conflicts`，固定 scope 与连接身份、分页、只读隐藏/拒绝。无 Agent 关闭冲突工具。
- [x] `/api/conflicts` 报告/列表/详情及版本保护的 transition；无证据关闭拒绝，原有 claims/decision 与候选状态不自动提升。
- [x] 现有 AI Memory 侧栏展示关联报告和参与来源，支持带精确笔记版本证据的审核；可查看最近一次审核及来源。过期版本拒绝且保留表单，分页、来源搜索及迟到响应保护已实现。
- [x] 检索返回 Conflict 报告时明确其生命周期状态；未记录来源版本显示 unknown，不再伪造 revision 1。
- [x] 全量 pytest 166 passed、1 skipped（Windows 符号链接环境限制），包含 14 个真实 MCP 协议场景。
- [x] 原 18 个浏览器场景回归通过，新增 6 项冲突检查通过，0 pageerror。截图与结果：`.agent-tmp/qa-conflicts/`，脚本 `tests/browser_conflicts.cjs`。

本轮三级审核：事实/接口依据 PASS；事务、权限和版本逻辑 PASS（限可信本机 REST / scoped MCP 边界）；实际界面及迟到响应检查 PASS。完整远程鉴权、隔离恢复和 P1 整体退出条件仍未完成，不能标记全阶段 QA_PASSED。

日常服务已更新并检查健康状态；备份 `.agent-tmp/backups/pre-conflict-review-20261009-161807/data`。重启前确认原进程已不存在且 8000 无监听，没有停止未知进程。

下一实施单元（Backend §16 已有范围）：为新隔离记录保存 caller scope、来源 revision 和原始提交；旧记录未知字段保留为空。提供版本保护的修复再验证，将通过的内容原子地生成待审核候选并记录恢复事件，保留原始错误数据；无来源/范围证据时拒绝自动恢复。先测迁移、幂等、跨范围、旧版本、失败回滚，再接 MCP/REST/界面。

## 2026-10-09 — 隔离恢复核心与协议（TESTED，用户界面待接通）

- [x] 隔离记录增量迁移保留原始 payload；旧 scope/source_revision/submission 未知时保持空值。新失败提交保存准确范围、来源版本与原始提交，相同输入返回相同隔离 ID。
- [x] 版本保护的显式修复：校验原始来源完整性，保留 source/authority，重新验证候选；候选、隔离结果、outbox 与恢复日志同事务。无自动接受或验证提升。
- [x] MCP 23 个工具：新增隔离列表/详情/修复；只读模式禁止修复，Agent 限 INFERRED，实际连接身份进入审计。REST `/api/quarantine` 列表/详情/repair。
- [x] 迁移、幂等、范围、过期版本、来源篡改、审计失败回滚、并发修复以及真实 MCP 调用已测。
- [x] 最新全量结果：174 passed、1 skipped；其中 15 项真实 MCP 协议场景。冲突与既有 UI 共 24 项浏览器检查通过，隔离尚无专用 UI，不列入浏览器验收。
- [ ] 隔离专用用户入口、旧记录人工重新导入指引及完整恢复演练继续推进。P1 整体仍为 PARTIAL。

日常服务已载入本增量：健康 healthy、关键词索引 lag 0、隔离列表返回 200。更新前备份 `.agent-tmp/backups/pre-quarantine-core-20261009-162641/data`。未修改个人 Agent 配置、调用嵌入服务或安装模型。

## 2026-10-09 — 隔离修复用户入口（指定范围 TESTED / QA_PASSED）

- [x] 维护工作区新增 Needs repair，保留 Backlog 与 Logsheet 职责；范围筛选、待修复/已恢复/旧记录、分页、错误原因、右侧修复表单与来源跳转已接通。
- [x] 原始记录默认折叠展示，修正后重新验证；失败/并发版本冲突保留表单，迟到响应不会跨范围打开记录。成功不自动接受或验证候选。
- [x] 本机 owner 可查看 `/api/quarantine-legacy`；未知 scope 不采用旧 payload.scope 猜测。Agent 禁止读取旧归属未知记录。UI 提示核实原文与版本后提交新候选。
- [x] 修复后的 claim 同时决定候选正文，旧 payload.content 不再覆盖新主张；原始提交不变。
- [x] 全量 pytest 176 passed、1 skipped；原有 18 项浏览器场景、6 项冲突场景及 8 项隔离场景均通过，0 pageerror。新证据 `.agent-tmp/qa-quarantine/`，脚本 `tests/browser_quarantine.cjs`。

三级审核：来源/接口事实 PASS；修复与版本/范围逻辑 PASS；实际界面、原文跳转、窄窗口及迟到响应 PASS，限以上场景。P1 完整恢复仍未验收：`raw_manifests.file_path` 保存绝对路径，Capability manifest 也包含 artifact_path；文件夹复制不证明跨目录恢复能独立读取全部原文和资源。下一步实现并验证一致性快照、清单校验和新目录恢复，保留原目录且不自动覆盖现有数据。

日常服务已更新：healthy、关键词索引 lag 0、旧隔离记录入口 200。更新前备份 `.agent-tmp/backups/pre-quarantine-ui-20261009-163920/data`。

## 2026-10-09 — 一致性快照与独立目录恢复（指定范围 TESTED）

- [x] owner CLI `scripts/manage_snapshots.py` 创建、校验、恢复；SQLite backup 捕获已提交状态，原文/旧清单/Skill 固定版本按哈希复制。目标必须不存在；失败保留 staging，不覆盖原目录。
- [x] 恢复前校验文件清单、目录布局及 canonical 来源/资源绑定，拒绝越界、篡改和错误重定位；新目录原文与资源路径重定位，原快照保持不变。
- [x] 14 项专用测试：原目录移走后独立读取、记忆历史/审计/outbox/撤销授权/固定资源版本、并发原文更新、损坏与复制中断、旧原文、权限及无效期限。测试曾揭示初始化连接未关闭导致 Windows 目录占用，已用显式关闭修复；错绑路径问题也已修复。
- [x] 全量 pytest：190 passed、1 skipped（Windows 符号链接环境限制），101.50 秒。此增量无前端改动；既有 32 项浏览器结果保持原验证范围，本轮未重复执行。
- [x] 日常数据快照 `.agent-tmp/backups/verified-20261009-recovery`（17 文件），恢复到 `.agent-tmp/recovery-drill-20261009-restored`；28 个表逐行比较通过，15 份旧版原文读取/重定位通过。此日常样本没有 SQL raw manifests、Skill 或授权记录，相关场景由专用测试覆盖，不能混称日常实测。
- [x] 恢复副本在独立 8767 端口启动，health healthy、关键词索引 lag 0；原日常目录未移动或覆盖。证据 `.agent-tmp/recovery-drill-20261009-evidence.json` 与同名服务日志。

限度：数据目录外的客户端配置、凭据和安装不包含在快照中；恢复报告不宣称它们已验证。期限为协作式检查；快照校验不提供发布者认证。P1 仍需完整失败/重放门槛审核；全阶段保持 PARTIAL。

下一实施单元（已有 Schema/时态/候选边界范围）：为 canonical 节点与关系接通统一验证，补齐有限数值、有效版本和带时区且有序的时间区间；接受候选时保留经验证的属性分区与有效期，来源/authority/审核等级仍由受控流程决定。REST 错误返回可处理状态，外部 MCP 提交可提供受限结构化属性。先验证非法写入与接受回滚，再做真实协议和检索回归。此单元不把尚未实现的全部 20 类独立 Schema/版本迁移标为完成，也不自动重写旧数据。

## 2026-10-09 — 用户要求暂停开发，先处理选型想法

开发暂停，保留未提交改动。停止点：已为 canonical 节点/关系接通共用验证，候选接受保留结构化属性和有效期，REST 无效记忆写入返回 422 并保存隔离记录。新增 10 项写入边界测试由失败转为通过，48 项相关回归通过；这之后尚未执行全量回归、实际 MCP 属性提交或界面验证，不能把此前 190 passed/1 skipped 套用为当前所有改动的全量结果。MCP 受限属性提交仍未实施。

用户已选择 EmbeddingGemma 2，见 DECISIONS.md 与 work/EMBEDDING-OPTIONS.md。本轮只核对官方模型与下载来源、记录决定和暂停点；未下载/安装模型，也未接续实现。等待用户明确恢复开发。

较早澄清（归属已被下条更正）：用户确认“自己主动链接＋系统自动发现后提醒＋用户决定是否关联”，当时被记为知识库新增交互；子 Agent 草稿隔离/知识分支/审核合并暂缓。当时仅更新方向记录，没有恢复实现或测试。

最新更正：用户明确第 1、3 点属于未来 DeepSeek harness 的聊天流程，不属于知识库。撤回将聊天连线/关联提醒列入 AI Library 新增范围的记录；子 Agent 协作方案归到 harness 且仍暂缓，具体机制未选定。第 2 点资料收藏留在 AI Library 方向。此更正不删除原有知识图关系或写入保护，也不代表 harness 已有实现或接入协议。本轮仅修正文档，开发保持暂停。

## 2026-10-12 — 动画转场与星图流畅度优化（Antigravity 执行，IMPLEMENTATION_COMPLETE / TESTED / QA_PASSED）

- [x] **根因定位与测量**：
  - 冷启动与连续 10 次往返实测基线发现：每次返回星图触发全量销毁重建（`clearContent()` 重复构造与编译 4,265+ 几何与 WebGL 着色器）、每帧标签全量遍历（1,807 个节点投影计算）、以及平均 16 次重复 HTTP 请求风暴（总览/牵引线/本地技能反复重查）。
- [x] **最小可恢复优化实施**：
  - `UniverseController` 引入总览与牵引线内存缓存（`overviewCache`、`tractorLineCache`），数据变更/整理/范围切换时精准失效（`invalidateCache()`），10 次往返请求数从 155 次骤降至 17 次（减少 89%）。
  - `UniverseScene.setData` 增加数据签名守卫（`currentDataKey`），结构未变化时不销毁场景，实现 0ms 瞬间平滑切回。
  - WebGL 几何资源池化与生命周期闭环：水母网格与空间站/战舰采用预生成单元几何体（`UNIT_JELLY_*`、`UNIT_HEART_*` 等），材质通过 `MATERIAL_CACHE` 按颜色/透明度复用；动态连线实例在场景清理时显式释放（`dispose()`），10 次往返后几何体净增长为 **0**（`geometryGrowth: 0`）。
  - 标签投影计算从 $O(N)$ 降至 $O(1)$：仅针对当前 hover / selected 节点执行投影计算与 DOM 显示，避免每帧 1,807 次遍历。
  - 单循环帧更新与 GC 零分配：整合多重 `bodies` 循环为单趟遍历，牵引流光粒子 `curve.getPoint` 避免每帧分配临时向量，移除渲染前冗余的 `scene.updateMatrixWorld()`。
  - 相机转场优化为 380ms ease-out cubic 平滑可打断过渡；阅读面板与维护界面增加 GPU 加速滑动过渡（`240ms cubic-bezier(0.16, 1, 0.3, 1)`），完美继承 `prefers-reduced-motion` 规则。
- [x] **用户视觉定制对齐**：
  - 技能空间站（Skills）彻底解除与中心发光核心的物理连线，定位至深空坐标 `[25.0, 13.5, -18.0]` 独立谐波漂流（drift）。
  - 牵引线升级为动态流光粒子能量流（`THREE.Points` 能量粒子沿二次贝塞尔曲线匀速流动，速度随关联紧密度与 hover 动态变化）。
  - 水母节点布局升级为 3D 360° 斐波那契球面立体分布，结合 10 次迭代泊松圆盘排斥碰撞检测算法，保证三维环绕且各节点绝不重叠相撞。
- [x] **自动化测试与回归证据**：
  - `tests/browser_skills_station_verify.cjs`：PASS（空间站模型、独立漂流、492 项子技能正常）。
  - `tests/browser_neural_ui.cjs`：PASS（覆盖 1440 桌面、612 平板、390 手机三种视口，0 pageerror，0 越界）。
  - 完整基准评测报告保存于 `.agent-tmp/motion-benchmark-20261012/`，截图保存于 `.agent-tmp/motion-benchmark-20261012/final-universe-spherical.png`。

## 2026-10-12 — 交接弹出原生新会话（IMPLEMENTATION_COMPLETE / TESTED / QA_PASSED，限定范围）

- [x] 用户确认交接时打开接收 Agent 的可见新会话，已开启时仍不复用，并绑定任务所属项目目录。已记录 DECISIONS.md。
- [x] 本机 scripts/dispatch_work_handoff.py 保存/读取真实 scoped 交接，携带直接用户任务，通过桌面适配器打开原生窗口。新任务使用交互模式；MCP 存储操作仍只处理交接数据。
- [x] 当前 Antigravity 真实新会话 dfb2b7bf-3ba8-4a30-8621-2b2931d20f18，与旧会话不同；项目目录和原生 UI 已核对。已有后台优化本次采用 observe-only，避免重复执行。
- [x] 原子状态写入、启动前占位、重复派送保护、runner 握手、scope/哈希/目标验证、可信参数配置及客户端参数 allowlist 已覆盖。Windows 短暂共享冲突已复现并修正，持续错误和未确认状态明确报告，不自动重启。
- [x] 相关回归 92 passed（133.58 秒）；最终 15 秒启动等待调整后桌面专项 43 passed（4.86 秒）。独立只读复审通过，未发现剩余重要问题。
- [x] 最终 Windows Terminal 原生 fixture v6 PASS：接收进程运行时唯一窗口可见、实际 cwd/任务正确、退出码 0；相同派送返回 ALREADY_DISPATCHED。证据 .agent-tmp/agent-window-native-qa/evidence.json；Antigravity 实际会话证据 .agent-tmp/agent-window-visible-evidence.json 与 .agent-tmp/agent-window-native.png。

验收边界：Antigravity CLI 已实际测试；Codex/Gemini CLI 只核对本机帮助和参数测试，未启动模型/UI 验收。其他宿主需明确本机适配配置并逐个验收，不代表任意网页、远端或厂商 GUI 可自动操控。Windows 前台焦点由操作系统决定。未提交 Git、未改个人客户端权限/模型配置。使用说明：work/AGENT-WINDOW-HANDOFF-v1.0.md。


## Harness v1.4 · 资料侧栏与双向同步（2026-10-13）

用户批准A：动作自动同步，记忆形成待审包，按任务确认后入库。

- [x] 当前聊天/主题目录与有界正文读取、可见侧栏修订刷新；界面读取不自动增加模型上下文。
- [x] DSH原生新提交事件接入，工具名称/状态同步，所有回合结束恢复idle；参数、工具输出、历史/子Agent/Library快照排除。
- [x] 捕获回合项目、来源原文摘录、有界持久事件队列、按聊天有序且独立重试、捕获失败保持可见。
- [x] 项目/聊天绑定待审清单，逐项排除、二次显式确认，来源Notes和Candidate Bridge正式记忆保留INFERRED/UNVERIFIED与主题归属。
- [x] 版本、撤权、源文本/人工修改、重复事件/重复确认和部分提交恢复专项检查；实际Cordis会话/命令/投影、兄弟服务注入、原生侧栏注册与组件验证。
- [ ] 在用户实际profile更新v1.4并按宿主提示重启，完整聊天页/星图联动与用户接受；NOT RUN。未替用户改profile或调用模型API。

状态：IMPLEMENTATION_COMPLETE / TESTED；完整宿主QA、USER_ACCEPTED未完成。具体证据和限额见 work/DEEPSEEK-HARNESS-PLUGIN-v1.4.md。

## 2026-10-14 — Backend README v1.7 gap implementation (IMPLEMENTATION_COMPLETE / TESTED; §87 benchmark open)

- [x] Compared the v1.7 backend contract with runtime routes, stores and tests. Added source intake, versioned schema migration, evidence-based evolution, source lineage, outbox-backed graph projection and separate ViewState, health/debt reporting, durable budget reservations, collection audit/outbox and approval-ticket protection.
- [x] Mounted source-intake routes in the main API; forwarded search read-mode, historical-time, minimum-revision, deadline and depth parameters. Cache telemetry reports local retrieval/context caches separately from explicit provider reports.
- [x] Agent candidate acceptance now requires an exact local-owner single-use approval. Historical transcript import leaves candidates pending. Explicit reviewed Harness confirmation creates an exact, short-lived ticket for the selected candidate.
- [x] Full isolated backend regression: **559 passed, 1 skipped (306.58 seconds)**. Focused integration regression: **51 passed**. Baseline before implementation: **448 passed, 1 skipped**.
- [ ] §87 fixed-hardware 1k/10k/100k correctness/performance protocol, semantic retrieval benchmark and user acceptance remain open.

The implementation record and preserved v1.7 source are in `work/AI-Knowledge-OS-Backend-README-v1.8.md`. No real provider measurements, full browser acceptance, automatic install, continuous crawl, universal execution gateway, model training or automatic deletion are claimed or enabled.
- [x] Final telemetry and durable-health wiring follow-up: **16 focused tests passed**; local cache state and durable budget reports are not represented as provider reports.
