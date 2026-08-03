# subclaw 优化记录 —— 引擎特化路由、回显重构与 Kimi Code 接入

> 日期：2026-08-03
> 范围：claw-proxy（`proxy/app.py`）、Claude 分支（`cli-skills/claude/`、`cli-skills/run-claw-pool.sh`）、Codex 分支（`cli-skills/codex/`）、新增 Kimi 分支（`cli-skills/kimi/`）
> 完整调研底稿见 `docs/research-engine-routing-and-echo.md`

---

## 一、背景与讨论过程

subclaw 此前已兼容 Claude Code 与 Codex CLI 两个编排引擎，但实际使用中暴露三类问题：

1. **Codex 调用兼容性问题频发**：codex worker 经常出现工具调用失败、响应内容丢失；
2. **编排者沟通/回显问题**：worker 运行期间编排者看不到真实进度，状态长期停在 "starting"，直到 worker 结束才"瞬间完成"；
3. **引擎串线 bug**：在 Claude 编排环境里误用 codex 的子代理创建方式直接报错——三引擎（Claude/Codex/Kimi）的原生子代理机制完全不同，提示词缺少强路由约束，模型会混用语法。

讨论中确立了两个关键设计判断：

- **引擎特化路由（物理隔离 > 运行时检测）**：每个引擎一条专属分支，各装各的技能包，每个技能包开头声明 ROUTING RULES，从根上杜绝委派语法混用。这与 Agent-as-a-Router 论文的结论（显式路由 + 信息累积优于隐式判断）以及 Codex V2 社区实践（显式 dispatch 比隐式自动委派更可预测、可审计）一致。
- **区分"编排者引擎"与"worker 引擎"两个维度**：串线只发生在"在 A 引擎里调用 B 引擎的原生子代理机制"；而 spawn 任意引擎 CLI 作为外部 worker 进程（`-p`/`exec` 模式）属于进程管理，任何编排者都合法可用，由共用 runner 统一管理。

同时明确了新需求：接入 Kimi Code 作为第三个编排引擎，并把"回显"作为本次优化的核心交付指标。

## 二、调研与借鉴（完整清单见调研底稿）

### 2.1 代码审查发现的具体 bug

对 `proxy/app.py`（1624 行）、`run-claw-pool.sh`、codex runner 全量审查后确认：

| # | 位置 | 问题 | 影响 |
|---|---|---|---|
| 1 | `stream_responses` anthropic 分支 | tool_use 块在 `content_block_start` 时即发出 completed 的 function_call，未累积 `input_json_delta` | **codex worker 所有工具调用参数为空** —— 兼容性问题最大根因 |
| 2 | `stream_responses` 结尾 | `response.completed` 事件 `output: []` 为空数组 | 客户端按 completed payload 渲染时**响应内容消失** —— 回显丢失根因之一 |
| 3 | `run-claw-pool.sh` 统计段 | `grep '\[EXIT\] code=0 '`（尾随空格）永远不匹配实际写入的 `[EXIT] code=0 duration_sec=` | `POOL_DONE OK=` 恒为 0，全部误报 FAIL |
| 4 | claude SKILL.md 示例命令 | 文档使用 `--budget 1.0`，runner 未实现该参数 | 按文档执行直接 `unknown opt` exit 2 |
| 5 | claude runner 进度监控 | `claude -p` 默认 text 模式结束后才一次性输出，`tail -f` 监控运行期间永远看不到 `[PROGRESS]` | 回显黑洞：status.json 恒为 starting |

### 2.2 论文借鉴

| 论文 | 吸收点 |
|---|---|
| **MemCon**（UCLA，arXiv:2607.13591） | 记忆操作建模为 MDP；`is_stuck` 信号 → 本次落地为 worker STUCK 检测（120s 无输出即标记）；零额外 LLM 调用的轻量控制思想用于后续上下文注入策略 |
| **Agent-as-a-Router**（arXiv:2606.22902） | 路由瓶颈是信息不是模型 → 后续把"worker 模型 × 任务类型成功率"写进编排 prompt；C-A-F 记忆闭环作为 P2 路线图 |
| **MAST**（arXiv:2503.13657） | 47% 的多代理失败在验证环节 → Kimi SKILL.md 内置客观化审计清单（CLAIM↔EVIDENCE 对齐、路径存在性、RISK 必须处理） |
| **MoA / FrugalGPT / Optima** | 聚合匿名化、廉价预检闸门、通信预算 → Kimi SKILL.md 落地为"每 worker ≤50 条 PROGRESS、证据包 ≤2K token" |
| **Anthropic context engineering** | sub-agent 只回传 1-2K 浓缩摘要、中间结果不进编排者上下文 → 证据包指针化规则 |

### 2.3 项目借鉴

| 项目 | 吸收点 |
|---|---|
| **Codex multi-agent V2** | `exec --json` JSONL 事件流规范（进度 stderr / 结果 stdout）；`[agents]` 段自然语言路由策略写法移植为 ROUTING RULES |
| **Claude Dynamic Workflows** | 中间结果外置不进上下文；回显粒度 `{worker_id, phase, last_event, tokens}` |
| **pi（badlogic/pi-mono）** | "CLI 自调用"范式验证；worker 输出走独立可见通道 + 结果文件化交接 |
| **kimi-cli / kimi-code 官方文档** | `-p --output-format stream-json` 每行一个 JSON、stdout 天然实时流式（三引擎中回显条件最好）；`OPENAI_BASE_URL/API_KEY` 环境变量覆盖 config.toml 的坑；skills 四级发现目录 |
| **LiteLLM** | 两层重试结构 → Kimi runner 内置失败重试 2 次 + 指数退避 |

## 三、实施内容

### 3.1 proxy/app.py —— 修复两个致命 bug

- **tool_use 参数累积**：`stream_responses` 的 anthropic 分支重写——`content_block_start(tool_use)` 时发出 `in_progress` 的 function_call 并登记 `pending_tools[index]`；`content_block_delta(input_json_delta)` 逐段累积 `partial_json`；`content_block_stop` 时按块类型（text/tool，用 `block_kind` 按 index 精确配对）分别收尾，tool 参数经 JSON 校验（失败走 `repair_tool_args`）后发出 completed。
- **completed 带完整输出**：新增 `output_items` 累积所有已发出的 output item（文本消息含累积全文 `text_buf`，anthropic 与 openai 两条分支都收集），`response.completed` 的 `output` 字段由空数组改为完整列表。

### 3.2 run-claw-pool.sh —— 统计与参数修复

- EXIT 统计改用正则 `grep -qE '\[EXIT\] code=0([^0-9]|$)'`（124 同理），OK/TIMEOUT 计数恢复正确。
- 实现 `--budget USD` 参数（解析、usage 说明、运行时回显记录），文档示例命令不再崩溃；硬熔断（proxy 断路器）列为后续项。

### 3.3 新增 Kimi 分支（cli-skills/kimi/subclaw/）

- **SKILL.md**：ROUTING RULES（kimi 引擎唯一合法路径声明）、claw-proxy 接入 config.toml 模板（`[providers.claw] type="openai"`）、环境变量坑警告、证据协议 + 通信预算、审计清单、安全规则。
- **scripts/run_kimi_claw_pool.ps1**：
  - worker 以 `kimi -p --output-format stream-json` 启动，prompt 经 UTF-8 无 BOM 文件走 stdin；
  - **实时流式回显**：监控循环逐行解析增长的 JSONL，tool_calls 事件与 `[PROGRESS]` 标记实时写入 `worker_NNN.status.json`（claims/evidence/asks 计数同步），彻底消除 claude 分支的"结束才回显"黑洞；
  - **STUCK 检测**：输出流 120s 无增长即标记 STUCK（MemCon is_stuck 落地）；
  - **重试**：非零退出自动重试 2 次、指数退避；超时（默认 900s）强制终止并落盘部分输出；
  - **环境变量清理**：spawn 前临时清空 `OPENAI_BASE_URL/OPENAI_API_KEY` 防止覆盖 config.toml；
  - 全程显式 UTF-8 读写（`[System.IO.File]` API），规避 PS 5.1 GBK 乱码与 BOM 污染。
- **scripts/kimi_subclaw_status.ps1**：proxy/模型/key 池/路由/报告一体化状态快照 + Watch 模式，报告过滤兼容 `*.kimiclaw.*.md`。

### 3.4 三分支统一 ROUTING RULES

`cli-skills/claude/subclaw.md`、`cli-skills/codex/subclaw/SKILL.md`、`cli-skills/kimi/subclaw/SKILL.md` 均在文件头部加入路由声明：当前引擎唯一合法路径、禁止调用其他引擎原生子代理机制、允许经 runner spawn 外部 worker 进程、跨引擎请求的降级话术。

## 四、优化前后对比（提升了什么）

| 指标 | 优化前 | 优化后 |
|---|---|---|
| codex worker 工具调用 | 参数 100% 丢失（function_call 在 start 时即 completed，input 恒为 `{}`） | 参数完整累积自 `input_json_delta`，含 JSON 修复兜底 |
| codex 流式响应完整性 | `response.completed.output` 为空，内容可能丢失 | completed 携带完整 output items（含全文） |
| Pool 完成统计 | `OK=` 恒为 0，全部误报 FAIL | 正确统计 OK/TIMEOUT/FAIL |
| 文档命令可执行性 | `--budget` 示例直接 exit 2 | 参数可用（记录级） |
| Kimi worker 回显 | 无 Kimi 分支 | **实时**：每个 tool_call/PROGRESS 事件秒级进 status.json；STUCK 120s 告警 |
| 引擎串线防护 | 无约束，靠模型自觉 | 三个技能包头部强制 ROUTING RULES，物理隔离委派语法 |
| 编码健壮性 | PS 5.1 下 GBK 乱码 / BOM 混入 prompt | 显式 UTF-8 无 BOM 全链路 |

## 五、后续路线图（未实施，见调研底稿 §7）

- **P1**：stream 路径补 failover（429/5xx 换 key 重试）；claude runner 改 `--output-format stream-json --verbose` 解析修复其回显黑洞；codex runner 补重试。
- **P2**：路由记忆库（模型 × 任务类型成功率统计进编排 prompt）；审计清单化 + 廉价预检闸门；心跳/通信预算全分支铺开；评估 memorix MCP 或自建六层记忆（候选→审核→批准门控）。

---

## 六、第二轮优化（2026-08-03 下午）：WorkBuddy 接入 + 代理层治理增强

### 6.1 WorkBuddy 成为第四条链路

本机调查确认 WorkBuddy v5.3.8 为 Electron 桌面 agent（`D:\devloop\WorkBuddy\WorkBuddy.exe`），关键事实：
- 有 skills 目录（`~/.workbuddy/skills/`）、MCP 支持（`.mcp.json` http 型）、memory/plans/tasks 体系；
- **无 headless CLI**——不能像 kimi/codex/claude 那样被 spawn 成 worker 进程；
- 自带模型注册表 `~/.workbuddy/models.json`：`{id, vendor:"Custom", url, apiKey, supportsToolCall...}`，url 直指 `/v1/chat/completions`。

由此确定双接入模式（均已写入 `cli-skills/workbuddy/subclaw/SKILL.md`，含 workbuddy 版 ROUTING RULES）：
1. **编排者模式**：WorkBuddy 通过 shell 调用其他引擎的 runner 派 worker（优先 kimi runner，实时流式回显）——符合“spawn 外部进程合法、原生子代理机制禁跨引擎”的路由原则；
2. **模型客户端模式**：在 `models.json` 增加 claw 条目（url 指 `http://127.0.0.1:4748/v1/chat/completions`），WorkBuddy 自身对话流量也走 claw-proxy，统一 key 池与治理。

### 6.2 代理层治理增强（借鉴 LiteLLM / CCR / OmniRoute / n9router）

针对“不同 agent 与模型 key 管理”的调研结论落地为三项纯内存轻量改造（`proxy/app.py`）：

**① Key 熔断器（三态状态机）**
- 每 key 状态 `{fails, open_until, disabled, cooldowns, last_retry_after}`；
- 错误分类：401/403 → 永久下线；429 → 优先学习服务端 `Retry-After`，否则指数退避（30s 起步、300s 封顶）；5xx/超时 → 连续 3 次失败跳闸冷却；
- `candidate_indices` 选 key 时软过滤不可用 key（全池冷却时回退全集而非硬失败）；成功即重置；
- `/api/status` 每 key 新增 `breaker: {state, cooldown_remaining, fails, last_retry_after}`。

**② 虚拟 key 与客户端身份（`proxy/clients.json`，新增 `clients.example.json` 模板）**
- 四个 agent 各一张 `ck-*` key（claude/codex/kimi/workbuddy），从 `Authorization: Bearer` / `x-api-key` 提取匹配；
- 无虚拟 key 时 User-Agent 兜底归因（claude-cli/codex/KimiCLI/workbuddy 模式表）；
- 支持 per-client 模型白名单（越权 403）。

**③ Per-agent 日预算**
- `budget_usd_per_day` 按自然日滑动重置；估算 token × 模型单价（取自 keys.json 的 `cost_per_1m_in/out`）累计；超限直接 429 拒绝（请求不打上游）；`ENFORCE_CLIENT_BUDGET=0` 可关；
- `/api/status` 新增 `clients[]`：name/agent/budget/spend/requests。

### 6.3 本轮提升对比

| 指标 | 优化前 | 优化后 |
|---|---|---|
| 引擎覆盖 | claude / codex / kimi | + **workbuddy**（四链路） |
| key 健康度 | 简单失败计数，429 后继续打 | 熔断器：429 冷却（学 Retry-After）、5xx 三连跳闸、401 永久下线 |
| 客户端身份 | 无，仅 session 粘性 | 虚拟 key 识别 + UA 兜底，四 agent 费用可归因 |
| 预算控制 | 无（`--budget` 仅 runner 记录） | per-agent 日预算硬拦截（429） |
| 可观测性 | key 请求/失败计数 | + 每 key 熔断状态/冷却倒计时 + 客户端花费视图 |
