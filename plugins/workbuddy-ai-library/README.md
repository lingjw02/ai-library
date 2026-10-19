# AI Library · WorkBuddy 插件 v1.0

把本仓库的 AI Library 以 **WorkBuddy 插件**（`mcp-app` 形态）接入，而不是散落在 `~/.workbuddy-ai/mcp.json` 里的裸 MCP 配置。能力与裸配置等价，区别是进入插件体系统一管理、可禁用/可分发。

## 与 DeepSeek Harness 插件的关系

| | 本插件（WorkBuddy） | `../deepseek-harness-ai-library`（Harness） |
| --- | --- | --- |
| 宿主 | WorkBuddy | DeepSeek Harness |
| 形态 | MCP 服务器（`.mcp.json`） | 原生 Cordis bundle（`dsh` 字段 + `cordis.patch.yml`） |
| 检索时机 | 模型按需调用工具 | 每轮 `agent/pre-step` 自动预取注入 |
| 模型请求 | 每次调用 1 次 | 本地检索 0 次（主模型仍消耗快照输入 token） |
| principal | `workbuddy` | `deepseek-harness` |

**两者不能互相加载**：WorkBuddy 没有 Cordis 容器与 `dsh` 加载器；Harness 不读 WorkBuddy 的插件清单。共用同一份 `data/` 与 backend。

## 结构

```text
plugins/workbuddy-ai-library/
├── .codebuddy-plugin/plugin.json   # 插件清单
├── .mcp.json                       # MCP 服务器定义（按需调用通路）
├── hooks/
│   ├── hooks.json                  # SessionStart / UserPromptSubmit / SessionEnd
│   ├── workbuddy_daemon.py         # 常驻检索守护进程
│   ├── workbuddy_prefetch.py       # hook 薄客户端
│   └── prefetch.config.json        # 端口、预算、重试、日志配置
└── README.md
```

## 安装

本插件不随仓库自动安装。两种方式：

1. **作为自定义连接器**：WorkBuddy 左侧边栏「专家 · 技能 · 连接器」→ **连接器** 页签 → 右上角 **自定义连接器**，指向本目录（或直接采用 `.mcp.json` 中的服务器定义）。
2. **作为插件**：由插件管理器以本地目录为源安装；若你的 WorkBuddy 版本支持 `marketplace.json`，可把本目录挂到一个 `type: "directory"` 的市场源下。

安装后工具清单会以**延迟加载**方式注册（`defer_loading: true`），静态上下文只占 `name + description`。

## 配置项

| 位置 | 参数 | 当前值 | 说明 |
| --- | --- | --- | --- |
| `.mcp.json` | `command` | 本仓库 `.venv/Scripts/python.exe` | 使用已安装后端环境 |
| `.mcp.json` | `--data-dir` | 本仓库 `data` | 与网页端、Harness 插件共用同一资料库 |
| `.mcp.json` | `--scope` | `default` | 本连接的授权分区，非项目选择项 |
| `.mcp.json` | `--principal` | `workbuddy` | 持久化本机连接身份；撤权后不会因重启自动恢复 |
| `.mcp.json` | `defer_loading` | `true` | 工具 schema 延迟加载，压低静态上下文 |

### 关于写权限：用授权层，不要用 `--read-only`

**不要把 `--read-only` 当作权限开关。** 它会连 `work_handoff` 家族一起隐藏，而工作交接是本库的主要能力之一。

本库的写控制是**两层独立机制**：

| 层 | 作用范围 | 效果 | 控制方式 |
| --- | --- | --- | --- |
| 启动标志 `--read-only` | 单次连接 | 隐藏并拒绝 14 个写工具（呈现层，粗粒度） | 改 `.mcp.json` 的 `args` |
| **授权记录** `access_grants` | principal + scope，持久化在库内 | SQL 层强制：无写授权时任何写语句被拒 | `scripts/manage_mcp_access.py` |

授权层的操作方式（**只能由本机 owner 执行**，Agent 无法自我提权）：

```bash
# 查看当前授权与 revision
python scripts/manage_mcp_access.py --principal workbuddy --scope default show

# 只读（可读不可写）
python scripts/manage_mcp_access.py --principal workbuddy --scope default \
  set --access read-only --expected-revision <N>

# 完全授权（读 + 写）
python scripts/manage_mcp_access.py --principal workbuddy --scope default \
  set --access full --expected-revision <N>

# 撤销（连读也禁止）
python scripts/manage_mcp_access.py --principal workbuddy --scope default \
  set --access revoked --expected-revision <N>

# 限时授权（到期自动失效）
... set --access full --expected-revision <N> --expires-at 2026-10-20T00:00:00+08:00
```

要点：

- **首次启动默认是 full**（`ensure_launch_grant` 写入 `can_read=1, can_write=1`）。也就是说当前 `workbuddy` 连接**已经具备写权限**，除非你显式改过。
- 每次变更受 **revision 保护**（乐观并发），并写入 `access_audit` 审计表。
- 写入在 SQL 层由 `GuardedConnection` 强制校验，工具参数无法绕过，也无法扩大 scope。
- 因此推荐模式：**平时设为 `read-only`，需要落库时临时 `set --access full`，用完再改回**。这样既保住 work_handoff 工具在清单里，又让写操作显式可控。

## 每轮自动预取（已实现）

WorkBuddy 的插件 hooks 兼容 Claude Code Hooks 规范，其中 **`UserPromptSubmit`** 会在每条用户消息提交时触发，并可通过 `hookSpecificOutput.additionalContext` **向模型上下文注入文本**。Harness 插件的自动预取行为已在 WorkBuddy 中复刻完成。

### 文件

| 文件 | 职责 |
| --- | --- |
| `hooks/hooks.json` | 注册 `SessionStart` / `UserPromptSubmit` / `SessionEnd` 三个事件 |
| `hooks/workbuddy_daemon.py` | 常驻检索守护进程；`--ensure` 预热、`--serve` 运行、`--stop` 停止、`--status` 查询 |
| `hooks/workbuddy_prefetch.py` | 薄客户端；读 stdin、请求守护进程、输出 `additionalContext` |
| `hooks/workbuddy_project.py` | 列出 / 选择检索主题（灵动岛"资料项目"选择器的等价能力） |
| `hooks/prefetch.config.json` | 端口、数据目录、预算、主题限定、重试、日志等配置 |

### 为什么必须常驻

一次性进程的冷启动实测 **约 21–24 秒**（导入 backend + 打开 SQLite + 建关键词索引），超过 hook 的 20 秒超时，方案不可行。改为常驻后：**守护进程就绪约 4–7 秒（仅会话开始时付一次）**，之后每条消息只做一次本地 IPC。

### 实测延迟

| 场景 | 耗时 | 输出 |
| --- | --- | --- |
| 守护进程预热（SessionStart） | 4–19 秒（受 SQLite 争用影响波动） | — |
| 冷启动首条消息（hook 自行拉起并等待） | ~11.6 秒 | 3,303 字符快照 |
| 正常提问（守护进程已就绪） | **5.4 / 7.9 / 8.4 / 9.8 秒** | 3,087–3,517 字符快照 |
| 过短输入 / 斜杠命令 | 0.8–1.6 秒 | 无输出（短路） |
| 会话结束清理 | 1.4–1.6 秒 | 无输出 |

**注意**：5–10 秒/条消息是实打实的额外延迟，主要消耗在检索本身（多次数据库往返），而非进程启动。是否接受取决于你的使用节奏。

### 失败安全

- 永远 `exit 0`，stdout 只含 JSON 对象；桥接损坏、守护进程未就绪、超时都不会阻塞消息，只是这条不注入。
- **单实例保护**：守护进程启动前先探测端口，已有人监听则**立即退出**（不再做一次多余的数据库初始化）；绑定用 `SO_EXCLUSIVEADDRUSE`（Windows），避免两个守护进程同时绑定同一端口、成倍放大 SQLite 争用。
- 守护进程未就绪时**后台拉起**，并带 **60 秒冷却**，避免每条消息都反复拉起注定失败的进程。
- **冷启动等待**：hook 发现守护进程未就绪时，会拉起并等待至多 `coldWaitSeconds`（默认 12 秒），使**当前这条消息也能拿到快照**，而不是静默跳过。
- 守护进程侧对 `build_service` 与 `prepare` 均带**重试**（默认 8 次 / 3 次，间隔 1.5s / 0.8s）。

### 触发条件（何时不检索）

每次消息都做一次检索并不划算。以下情况直接短路，不触发检索：

| 条件 | 配置项 | 默认值 |
| --- | --- | --- |
| 输入过短 | `minPromptChars` | 4 字符 |
| 斜杠命令 | `skipPrefixes` | `["/"]` |
| **纯客套话** | `skipRegex` | 见下 |

`skipRegex` 用**整串锚定**匹配，只拦"整句都是客套"的输入，不会误伤含实质内容的短查询：

```
^(?:(?:ok|okay|好的?|谢谢|感谢你?|多谢|收到|明白|嗯+|是的|对的|没问题|可以|行|辛苦了?|不错|棒|赞|厉害|thanks?|thank\s*you|thx|got\s*it|nice|cool|great|awesome)[\s!！。.，,、~～？?…]*)+$
```

已实测：`ok感谢你` / `谢谢` / `好的` / `嗯嗯` / `多谢！` / `thanks` 命中跳过；`工作交接怎么用` / `候选怎么接受` / `可以帮我看看吗` / `行吧那就这样吧` **不**跳过。

跳过时整条消息耗时约 1.3 秒（仅 Python 解释器启动，检索耗时 0.00s）。

### 检索主题限定（等价于灵动岛的"资料项目"选择器）

全库检索会把无关主题混进来。实测同一条查询 `灵动岛 UI 怎么接入`：

| | 全库检索 | 限定 `AI library` 主题 |
| --- | --- | --- |
| 检索范围 | （全库） | AI library |
| 返回证据 | 02-development-tools / 盘古AI P3 / Antigravity harness | **AI library 架构 / MCP 部署 / 后端 README** |
| 相关性 | 全部无关 | 全部命中 |

用法：

```bash
# 列出全部主题（含 id、条目数、子主题层级）
python hooks/workbuddy_project.py --list

# 按标题限定（子串唯一匹配）或按 id 限定
python hooks/workbuddy_project.py --set "AI library"
python hooks/workbuddy_project.py --set topic-e4c313d85bf0455f8382ff420953d2bf

# 查看 / 取消
python hooks/workbuddy_project.py --status
python hooks/workbuddy_project.py --clear
```

**要点**：

- 选择结果写入 `prefetch.config.json` 的 `projectId`，**守护进程每条请求都重读该字段**，改完立即生效，无需重启。
- 父主题的读取范围**包含全部后代主题**的成员。
- **默认是 `null`（全库）**。这是刻意选择：收窄检索会带来"漏检"风险，且该设置**全局生效**（所有项目共用），所以默认不收窄，由你按当前工作内容显式限定。
- 限定只影响**本 hook 的预取范围**；`ai-memory-library` 的 MCP 工具调用不受影响（该库的 `search_knowledge_context` 目前不接受项目过滤参数——这是与灵动岛选择器的唯一能力差距）。

### 已知约束

1. **多进程 SQLite 争用（实测）**：运行中的 MCP 服务每 10 秒写心跳并执行 `wal_checkpoint(TRUNCATE)`，与本 hook 进程争用同一 WAL。实测 4 次 `CREATE TABLE` 有 1 次报 `attempt to write a readonly database`。重试可缓解，但**根本解法是不要让两条通路同时写同一 `data/`**：要么只用 hook、要么只用 MCP。
2. **守护进程存活**：`SessionStart` 拉起的分离进程在宿主结束钩子进程树时可能被连带回收。若发现预取长期不生效，改为由你手动常驻启动：`python hooks/workbuddy_daemon.py --serve`。
3. **`BridgeRequest` 为 `extra='forbid'`**：请求体不得携带 `request_id`（该字段由桥接层在调用 `handle` 前剥离）。
4. **本库正在并行开发**：验证期间 `backend/adapters/harness/context_bridge.py` 曾被改为引用尚未创建的 `sync_store` 模块，导致导入失败；该模块随后补齐。hook 对此类中断是安全的（静默跳过），但联调前请确认仓库处于可导入状态。

**成本提醒**：预取快照约 3,100–4,100 字符（约 1,800–2,400 tokens）/条消息，内容随检索结果变化，**会打断前缀缓存**。详见 token 验证报告。

## 停用与回退

- 禁用：从连接器/插件管理中关闭本插件，或移除该自定义连接器条目。
- 完全移除：删除本目录，并确认 `~/.workbuddy-ai/mcp.json` 中不再保留同名服务器（两者会重复注册）。
- 本插件不写入资料库内容；停用不影响 `data/` 与 Harness 插件。
