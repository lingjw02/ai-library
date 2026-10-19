# AI Library · DeepSeek Harness plugin v1.5.0

v1.5 带来 **灵动岛自动更新最新上传（import）内容** 与 **星图会话主题智能匹配/一键开辟新主题**：
- **自动更新为上传内容**：后台导入新的 Notes/Memory 资料时，灵动岛自动识别最新导入批次，顶部状态展示徽章，并自动将资料项目无缝切换为该导入内容所在主题。
- **星图会话主题探测与开辟新主题**：支持智能分析当前会话核心关键词与星图已有主题天体的相似度；匹配成功提供一键归入，未匹配时智能提炼新主题名，支持一键在数据库建主题并在星图天体系统中生效。
- **DeepSeek Harness 专属主题与分区**：已在星图新增 `deepseek harness` 天体主题，并在“我的主页/我的空间”中将 DeepSeek Harness 列为专属导入分区。

历史 v1.4 按已批准模式A提供 **Library → DSH资料浏览/刷新；DSH → Library真实动作与待审记忆**。点击灵动岛里的 **打开资料侧栏**，在右侧查看目录、来源正文、待审包和动作。更新说明见 [v1.4记录](../../work/DEEPSEEK-HARNESS-PLUGIN-v1.4.md)。未安装新版前，旧宿主界面不会因仓库代码修改自动更新。

历史 v1.3.1修复 `cannot get property "remote" without inject`：Client补齐父级remote依赖，在实际Cordis sibling服务加载方式中验证。已安装v1.3.0的用户需重新安装/更新本目录，并按返回的application提示重启或重载；只刷新聊天页面不会自动复制本目录的新代码。见 [修复记录](../../work/DEEPSEEK-HARNESS-PLUGIN-v1.3.1.md)。

原生 Cordis Host bundle。在模型处理新的用户文字前，本地读取当前 AI Library 使用规则、检索获准资料并注入一份有来源的证据快照。无需让模型逐轮调用检索工具，也不向模型新增 MCP 工具清单。

连接现有完整资料库后，点击 Harness 窗口顶部居中的 **AI LIBRARY 灵动岛**，在“资料项目”选择 **整个 AI Library** 或星图已有项目/主题。默认全库，主题选择包含其子主题；每个聊天独立，成功选择记录在原生会话日志中，可恢复/继承。无需新建项目或复制资料。

v1.3 将控制面板迁移到顶部悬浮灵动岛：收起显示当前主题、连接检查结果和读取模式/上次机甲观察；点击展开 **资料、机甲观察、插件开发**。机甲只观察，位置与动作显示在轻量探针小窗口中。Esc在岛内收起并恢复按钮焦点；收起保留当前聊天草稿并停止机甲轮询。非聊天页面或没有有效聊天时隐藏。

深色胶囊采用暖白探针核心和低亮青色状态；展开内容自身滚动，适配手机宽度、桌面标题栏和短窗口，遵循减少动态设置。检查范围见 [v1.3记录](../../work/DEEPSEEK-HARNESS-PLUGIN-v1.3.md)；完整宿主视觉验收仍未运行。

此前核对：资料使用 `default` 授权分区，星图主题区分项目；授权分区 scope 与项目/主题不是同一个概念。未来若新增独立授权分区，仍需本机 owner 配置相应连接，不会借项目选择扩大授权。

状态：**IMPLEMENTATION_COMPLETE / TESTED**。已验证本机 Harness 源码 `0.2.1-alpha.2` 的真实 Cordis 加载、bundle patch 解析、pre-step waterfall、消息格式和清理；用户实际 profile 安装、宿主界面与模型响应为 **NOT RUN / USER_ACCEPTANCE_PENDING**。

## 连接方法

本地 bundle 目录：

```text
D:\02_Development_Tools\AI library\plugins\deepseek-harness-ai-library
```

在 DeepSeek Harness 对话中粘贴：

```text
请先使用 plugin_manager 查看当前 profile 的插件和 bundles。
使用 install_bundle 安装或更新这个本地目录为 @ling/ai-library-harness v1.5.0：
D:/02_Development_Tools/AI library/plugins/deepseek-harness-ai-library
启用该 bundle。核对返回的 application、warnings，并使用 cordis_inspect_query 核验插件条目；有加载错误就报告，不宣称连接成功。
不要修改模型供应商配置，不要申请版本兼容豁免，不要导入或写入正式记忆。
```

核心工具参数：

```json
{"action":"install_bundle","target":"D:/02_Development_Tools/AI library/plugins/deepseek-harness-ai-library"}
```

插件管理操作作用于整个当前 profile，并可能需要宿主自己的权限批准。Live profile 的首次安装可立即应用；startup profile 以及替换已加载包通常需要重启，按返回的 application / warnings 操作。安装成功后开启新对话测试，避免把安装前已有模型上下文混入接入判断。这里没有替用户安装或改写 profile。

## 前置条件和配置

- 已存在 AI Library 仓库及其 Python 虚拟环境；bundle 包含原生 Host / Client JS，不打包整份后端或数据库。
- Harness 本身能启动。本机源码要求 Node `^22.19.0 || >=24.0.0`；本次命令行是 `22.18.0`，虽通过源码模块检查，仍须满足宿主声明的版本要求再启动完整宿主。
- 资料读取使用本地持久化授权。scope 和 principal 在部署配置固定；用户提示词、来源内容和请求帧均不能覆盖。

安装前可修改本目录 `cordis.patch.yml`。安装后配置应通过 Harness 的 profile override 管理；对原目录的修改不保证自动传播到已安装的包。本版部署参数通过 patch 调整，未提供宿主 Settings 表单。AI Library 自身的使用规则仍可在已有网页设置中微调，发布后在下一次新用户文字触发时重新读取。

常用读取偏好已可在本插件控制面板调整，按聊天保存；部署身份和路径保持固定，不通过聊天切换权限。

## 直接在 Harness 操作

点击窗口顶部居中的 **AI LIBRARY** 胶囊。点击同一按钮或在面板内按Esc收起，不因鼠标经过而自动打开。收起保留当前聊天的未保存源码、检索输入和开发需求；离开/切换聊天按宿主生命周期重新挂载，不把草稿转移到其他聊天。草稿仅在当前挂载期间保留，不承诺重启恢复。

### 资料

- 自动检索开关、上下文字符上限（2000–40000）、证据条数（1–20）、单条摘要字符上限（40–4000）；点击 **保存读取设置**，每个聊天独立，记录在原生会话日志中。
- **连接测试** 核对本地只读 bridge。项目仍通过上方 **资料项目** 切换。
- 输入检索内容，点击 **手动预览**；不调用模型、不自动插入模型上下文，显示摘要、来源哈希和核实状态。
- 点击 **用于下一次回答** 只排队一条检索意图，不保存证据正文；下次真实用户输入会重读当前规则、来源、授权和项目成员，再注入一份快照。即使自动检索关闭，这次仍执行；随后消费该意图，可通过 **取消单次使用** 撤销。
- 预览五分钟后、项目或设置改变后需重新检索。项目或设置在准备期间改变会拒绝旧结果。完整规则超预算时明确报错。
- 显示字符统计，**不是实际模型 token 或节省率**。切换项目/修改设置清除旧预览和排队请求。


### 资料侧栏与自动同步（v1.4）

- **目录**：按当前聊天选中的主题及子主题读取 Notes / Memory，支持搜索、分页和正文。正文最多40000字符，截断明确提示；查看界面不会自动把正文发送给模型。
- **自动刷新**：侧栏可见且前台时约每3秒检测修订，变化后刷新目录和当前正文；收起、后台或离开聊天停止。模型在下一次既有检索触发时取得新证据，已进入模型的上下文不会被界面刷新悄悄替换。
- **动作**：同步新提交的原生工具事件名称、开始/完成/失败和回合结束状态，不传工具参数或输出。每聊天隔离，Library使用同一dataDir时可看到活动；不等于工具结果已验证。
- **待审包**：仅在接入后、回合完整完成、起止项目一致且选中明确主题时生成。Library快照、命令、替换消息、子Agent、被中断回合与历史种子不作为新记忆。未选主题时仍同步动作，不生成可入库的记忆。
- 待审项使用最后一条非工具调用回答的原文摘录，不另调模型生成摘要；每条消息最多8000字符，来源最多24000字符，截断保存为 `source_partial`。这不是自动核实，也不是全聊天备份。按同一聊天/项目累计，建议每任务结束审一次。
- 展开 **待审包** 查看原始摘录与记忆正文，勾选保留项，点击保存后再点 **确认入库**。未选项作为本批排除项；全部取消勾选时可再次确认排除整批，不写入来源或记忆。保留项确认后创建可追溯的Notes来源、Candidate Bridge记忆以及所选主题归属。记忆保留 `INFERRED / UNVERIFIED`。项目、来源、人工编辑或审核版本改变时拒绝旧确认；部分失败保留回执，仅允许原选择重试，不重复覆盖已有内容。
- 动作/来源先在 `data/_runtime/harness-sync.sqlite3` 暂存，未确认不改正式内容；待发送事件保存在按scope/principal隔离的 `data/_runtime/harness-outbox/`。每聊天保持顺序，失败不会堵住其他聊天；重启重试保留事件编号。队列最多256帧、待审最多50项且约900KB；满额明确报错。持久队列可重试，若入队失败则显示保留的错误，不能假定该回合已同步。
- 插件关闭保留尚未发送的队列，下一次加载继续；桥接进程退出后需要重新加载插件恢复连接。没有自动导入旧聊天、抓取本机文件、覆写人工记忆或调用额外模型。当前模式不自动执行使用规则中的语义去重/冲突判定，需要审核者判断。

命令行亦可查看目录和审核（当前原生聊天身份由Host注入）：

```text
/library {"operation":"browse","kind":"all","offset":0,"limit":40}
/library {"operation":"read_object","kind":"memory","object_id":"目录返回的ID"}
/library {"operation":"sync_status"}
/library {"operation":"sync_review"}
```

正式保存通过侧栏审核确认；缺失项目、撤权、非当前聊天的批次或过时digest均拒绝。设置 `allowConfirmedSync:false` 可关闭确认入库，保持普通MCP只读；本地桥接本身不是对其他Agent开放的任意写入服务。

### 机甲观察

小窗口用轻量八足探针示意图显示当前聊天机甲的真实当前位置、最近动作和状态；不模拟不存在的探索，不显示其他聊天会话。第一次读取资料后登记机甲，未登记时明确提示。展开观察区域后约每秒读取一次，切换区域、收起面板、离开聊天或后台页面时停止读取；不嵌完整 3D 星图，不提供驾驶。

### 插件开发

**手动：** 选择白名单文件 → 读取文件 → 修改源码 → 检查并预览差异 → 应用源码修改 → 确认保存源码。只检查 JS / JSON 语法，**不运行改后代码，不等于行为验证**。检查失败、提案在检查后被改动、原源码被其他操作修改、文件链接逃逸均拒绝保存。确认后自动保留修改前备份，可在本次插件运行中恢复；重启后可凭显示的备份路径人工恢复。

**自然语言：** 选择文件并填写开发需求 → 生成开发提案并放入输入框 → 检查并发送原生草稿 → Harness AI 修改指定临时副本 → 读取 AI 修改后的提案 → 检查、差异预览与确认保存。插入采用原生草稿修订保护，不自动发送或覆盖后来输入的文字。若临时文件不在当前 Harness 工作区，仍遵守宿主的文件访问授权，不绕过审批。

只允许本插件既有 JS 和 locale JSON 文件；不提供修改宿主、profile、部署身份或依赖的编辑入口。开发临时副本/备份放在仓库 `.agent-tmp/harness-plugin-development`，最多保留64个本次运行中的提案。未安装插件自身的源码目录由受信 bridgeScript 所在仓库确定，不取已安装包目录作为编辑目标。

**加载修改：** 保存源码后点击 **安装 / 更新新版**，再次确认。直接调用 Harness 原生 Plugin Manager，先核对当前 bundles，不手工写 profile；返回 `restart-required` 则提示重启。诊断、warnings 和未应用状态保留，不自动批准安装脚本或兼容豁免。安装会影响当前 profile 的全部聊天；源码保存和运行新版是两个不同状态。

| 参数 | 默认 | 作用 |
| --- | --- | --- |
| pythonExecutable | 本仓库 `.venv/Scripts/python.exe` 的绝对路径 | 使用已安装后端环境 |
| bridgeScript | 本仓库 `scripts/run_harness_bridge.py` 的绝对路径 | 持续本地桥接子进程 |
| dataDir | 本仓库 `data` 的绝对路径 | 与网页读取同一资料库和机甲运行记录 |
| scope | `default` | 本连接授权分区；不是项目选择项，当前完整库均在此分区 |
| principal | `deepseek-harness` | 持久化本机连接身份；撤权后不会因重启自动恢复 |
| maxContextChars | `6000` | 单次快照总字符上限，包含规则和来源 |
| maxItems | `3` | 单次最多证据条数 |
| excerptChars | `480` | 每条原文摘录上限 |
| timeoutMs | `15000` | 单次请求等待上限 |
| maxReplyBytes | `1048576` | 单个桥接响应帧上限，包含待审包 |
| allowConfirmedSync | `true` | 允许显式审核确认入口入库；`false` 保留浏览、动作与待审，拒绝正式入库 |

完整规则必须保留；若规则已超预算，插件报告错误，要求增加预算，不会默默截断规则。预算单位为 Unicode 字符，**不是 tokenizer 的 token**。

## 选择项目

输入区下方“资料项目”默认是“整个 AI Library”。下拉列出真实星图主题，可选择父主题或子主题，父主题读取范围包含所有后代主题的成员。点击“刷新项目”更新目录；超过一页时可加载更多。等待保存完成再发送任务。切换回“整个 AI Library”恢复全部获准资料检索，包含未分类内容。

项目过滤发生在结果限额之前。所选主题不存在、授权撤销或读取失败时明确报错，不会偷偷回退全库；选择器失败保留原选择。已选项目若不在当前目录页，会显示“所选项目不可用”，可刷新或加载更多核对。

命令行宿主也可使用原生 slash command，无模型调用：

```text
/library {"operation":"list_projects","offset":0,"limit":100}
/library {"operation":"select_project","project_id":"目录返回的主题ID"}
/library {"operation":"select_project","project_id":null}
```

UI 和 slash command 共用 Host 操作。成功选择来自配对的 user command/run 与 command/done；普通提示词或资料内指令不改变选择。会话关闭、插件重载后的恢复由原生会话日志投影负责。

选择器限定本插件后续预取范围，不删除已经进入聊天的旧上下文，也不改写其他 MCP 或本机文件工具的权限。快照明确标注选定主题，要求不暗中扩大读取范围。本版主题选择使用关键词检索，不做可能越出成员范围的图扩展；未启用主题内向量筛选。

## 安装后的验收

开启新对话发送：

```text
请根据 AI Library 查找与 AI library 相关的历史资料。
指出返回资料的来源 ID、核实状态，并区分历史陈述与当前已验证事实。
如果摘录不完整或没有足够证据，请直接说明，不要补造事实。
```

另可用精准对象测试：`查找 AI Library 中 mem-ae3d8e92c1 的历史资料，并报告来源与核实状态。` 该对象在本次资料库副本中返回原来源 `note-481fe9cca9`、`UNVERIFIED`；不将历史内容提升为当前结论。

检查：

1. 插件管理器显示 `@ling/ai-library-harness` / `ling-ai-library-prefetch` 已启用且无加载错误。
   窗口顶部显示灵动岛；点击展开后显示“资料项目”。先保持全库查询，再选择“AI library”查询；在第二聊天选其他主题，核对互不影响。确认标题栏仍可操作，岛外点击不被拦截。
2. 本地桥接成功，模型收到 `ai-library` 来源的快照；内容只有一份正文，含规则、`RETRIEVAL_JSON` 和 `EVIDENCE_JSON`。
3. AI Library 右侧“AI 机甲”出现 DeepSeek 的会话记录。不同会话使用不同 `agent.session.id`；记录读取活动后进入 idle。星图网页须使用同一个 dataDir / scope 并加载已有机甲功能。
4. 关闭会话或卸载插件后临时机甲会话清理。具体宿主是否在关闭 UI 聊天时释放 Agent，仍需真实宿主验证。
5. 当规则在 AI Library 设置中保存更新，下一条用户文字的快照携带新规则修订。

## 行为边界

- 自动检索开启时，每个获准、含新用户文字的 `agent/pre-step` 执行本地检索；工具继续步骤和已拒绝步骤不重复检索。明确排队的手动检索也可用于下一次附件输入；没有排队时附件-only 仍不自动检索。自动接续时的专用补查、关键决策与任务结束 hook 仍属后续工作。
- 规则是单独标明的 owner policy；原始来源内容是 untrusted 数据。保留来源 ID / 修订 / 原始来源哈希、权威性、成熟度、核实状态和已记录冲突；截断与覆盖信息对模型可见。
- 每次检索验证授权并重读返回对象；本版不使用跨任务资料缓存。撤权或读取失败不会注入旧快照，当前准备步骤会报告失败；本版未实现按任务依赖分类的自动降级。
- 浏览、检索和普通MCP内容接口保持只读；v1.4新增受限的审核确认入库通道。动作与待审包先写运行 sidecar；关键词索引、授权与机甲运行状态沿用现有服务的本机元数据机制。
- 桥接新增 `browse / read_object / fingerprint / sync_event / sync_status / sync_review / sync_confirm`。`sync_event`只由Host订阅真实事件构造，不作为聊天命令开放。确认通道仅处理绑定聊天/项目/审核摘要的待审项，不开放任意来源或正式记忆修改、主题整理、import更新、网页爬取、外部发送或模型调用；开发区确认流程保留。
- 若同一任务还启用了原 MCP，Agent 仍可能自行再次查询。Plugin 快照已经完成该次规则读取和检索，应按证据不足情况补查，避免对同一内容无目的重复读取；本 bundle 不自动修改现有 MCP 配置。
- 本地检索的模型请求为 0，主模型仍消耗快照的输入 token。是否减少总 token，必须用同一 DeepSeek 模型、任务、缓存状态做 A/B；本次没有测得节省比例。

## 停用与回退

先在 Harness 插件管理器禁用 bundle，或使用：

```json
{"action":"set_bundle","target":"@ling/ai-library-harness","enabled":false}
```

按宿主返回的 application 判断是否需重启。禁用会移除 hook 并等待本插件拥有的桥接子进程退出，保留 AI Library 资料和原 MCP。永久移除包另行使用宿主的 remove_bundle；不需要删除资料库。

## 本地开发检查

在 AI Library 仓库运行：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_harness_auto_sync.py tests/test_harness_plugin.py tests/test_harness_controls.py -q
node --test --test-concurrency=1 plugins/deepseek-harness-ai-library/tests/*.test.mjs
```

v1.1 验证：Node 六项检查、原生 Cordis 命令/投影/双会话/pre-step/日志恢复/全库切回/卸载检查通过；实际 Client 模块的 React DOM 功能测试覆盖中文选择器、双会话、错误保留选择与清理。真实资料副本目录37项，选定“AI library”的快照3716字符，全库4059字符；精确原来源hash一致，UNVERIFIED不提升，0次模型请求。Python最终回归与边界见 [v1.1记录](../../work/DEEPSEEK-HARNESS-PLUGIN-v1.1.md)。证据在本仓库 `.agent-tmp/harness-project-selection-20261012/`。这些不等于真实 Harness 页面视觉验收。
