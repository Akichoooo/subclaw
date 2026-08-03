# subclaw 引擎特化路由与回显设计 —— 调研、讨论与借鉴记录

> 日期：2026-08-03
> 参与：Akichoooo + AI 协作调研（两轮并行深度调研 + 本机代码全量审查）
> 目标：解决 ① 引擎串线 bug（在 claude 里误用 codex 子代理机制导致报错）② worker 进度回显缺失 ③ Kimi Code 接入
> 建议归档位置：`subclaw/docs/research-engine-routing-and-echo.md`

---

## 一、问题背景

### 1.1 已确认的本机现状
- Claude 侧已安装：`~/.claude/commands/subclaw.md` + `~/.claude/scripts/run-claw-pool.sh`（与仓库版一致）。
- Codex 侧已安装：`~/.codex/skills/subclaw/`（本机版有本地修改：硬编码 `Model="mimo-v2.5"`、`CodexCmd` 绝对路径，需同步回仓库）。
- Kimi Code：仅存残留配置 `~/.kimi-code/config.toml`（新一代 TS 版 kimi-code 格式）+ 日志（最后活跃 2026-07-29），**可执行文件需重装**。
- claw-proxy 当前未运行（`http://127.0.0.1:4748` 不可达）。

### 1.2 三类核心痛点
1. **引擎串线**：在 Claude 编排环境里用 codex 的方式创建子代理 → 报错。根因：三引擎的原生子代理机制完全不同（Claude=Markdown frontmatter + Task 工具；Codex=`~/.codex/agents/*.toml` + 自动委派；Kimi=`--agent-file` / `/swarm`），提示词里没有强路由约束，模型会混用语法。
2. **回显缺失**：
   - `claude -p` 默认 text 模式**结束后才一次性输出** → run-claw-pool.sh 的 `tail -f` 进度监控在 worker 运行期间永远看不到 `[PROGRESS]`，status.json 恒为 starting。
   - proxy 的 `stream_responses` 存在 tool_use 参数丢失（content_block_start 时即发出 completed 的 function_call，未累积 input_json_delta）、`response.completed` 的 `output: []` 为空等问题，导致 codex worker 响应内容/工具参数丢失。
3. **统计失真**：run-claw-pool.sh 末尾 `grep '\[EXIT\] code=0 '`（尾随空格）永远不匹配实际写入的 `[EXIT] code=0 duration_sec=` → `POOL_DONE OK=` 恒为 0。

### 1.3 讨论中的关键结论
- **"引擎特化路由"方向正确且必要**：每个引擎一条专属分支，物理隔离委派语法，是消除串线 bug 的根本手段（共识：Agent-as-a-Router 证明显式路由 + 信息累积优于隐式判断；Codex V2 实践帖证明显式 dispatch 比隐式自动委派更可预测、可审计）。
- **两个维度必须分开**：
  - **编排者引擎**（谁在指挥）：claude / codex / kimi，决定用哪套原生语法与提示词规则；
  - **worker 引擎**（谁在干活）：可以是任意 CLI 的 `-p/exec` 进程——spawn 外部 CLI 进程属于"进程管理"，任何编排者都能做，不算串线。串线只发生在"在 A 引擎里调用 B 引擎的**原生子代理机制**"。
- **回显必须走结构化事件流**：三引擎都已提供 JSONL 事件流能力（claude `--output-format stream-json --verbose` / codex `exec --json` / kimi `-p --output-format stream-json`），自由文本 tail 方案应废弃。

---

## 二、借鉴清单（项目 + 论文）

### 2.1 论文

| 论文 | 核心结论 | 对 subclaw 的吸收点 |
|---|---|---|
| **MemCon**（UCLA，arXiv:2607.13591，Memory as a Controlled Process） | 记忆操作应建模为 MDP：表格老虎机 + UCB 学"何时检索/注入/遗忘"，零额外 LLM 调用；任务成功率最高 +15.2 分且 token 省 5-20% | ① 编排者给 worker 注入多少上下文由轻量控制层决定（奖励=worker 成败 + 审计评分）；② `is_stuck` 信号：worker 连续重复相同 `[PROGRESS]` → 触发换查询重检索或终止；③ PlanInject：同类任务复用历史成功计划模板；④ 冷启动期回退先验规则 |
| **Agent-as-a-Router**（arXiv:2606.22902，ACRouter） | 路由瓶颈是**信息缺失**而非模型能力；C-A-F 闭环（Context→Action→Feedback→Memory）；把"各模型各维度实测分数"喂给路由器即 +15.3%；维度标签只解释 27% 路由信号 | ① 先把"worker 模型 × 任务类型历史成功率统计表"写进编排 prompt（零成本收益最大）；② 审计结果（CLAIM/EVIDENCE 完整度、测试通过）作为 Feedback 写入路由记忆（SQLite + kNN）；③ 成本权重 `r = 质量 − 0.1×成本`；④ 保留复杂任务直通旗舰模型的逃生通道 |
| **MAST**（arXiv:2503.13657，Why Do Multi-Agent Systems Fail） | 14 种失败模式，**47% 失败在验证环节** | 编排者审计必须清单化、客观化（路径存在性、符号存在性、CLAIM↔EVIDENCE 对齐），不能凭感觉审 |
| **MoA**（arXiv:2406.04692） | 聚合时参考弱模型输出也有收益（collaborativeness） | 多 worker 聚合时匿名化呈现 + 显式要求"批判性综合而非拼接" |
| **FrugalGPT / RouteLLM** | 级联（便宜→贵 + 停止判断器）/ 路由（零延迟 + 预测器） | worker 输出先过廉价预检闸门（标记完整性、证据数量），不合格直接打回，不消耗编排者审计上下文 |
| **Optima / Cut the Crap** | agent 间最大浪费是互相说废话 | 每 worker 设通信预算：`[PROGRESS]` 总量上限 + 无进展熔断 |
| **Agent 幻觉综述**（arXiv:2509.18970） | "通信幻觉"：worker B 引用 A 的 CLAIM 会把错误洗白成证据 | 编排者追溯证据源头；证据包追加不覆盖 |

> 备注：用户提到 Agent-as-a-Router 汇报人为"杨昊波"，论文作者列表中未检索到该名字（可能为转述偏差），机制结论以 arXiv:2606.22902 原文为准。

### 2.2 项目 / 官方机制

| 来源 | 核心设计 | 对 subclaw 的吸收点 |
|---|---|---|
| **Claude Dynamic Workflows**（2026-05 发布） | 现场生成 JS 编排脚本，独立运行时后台执行；**中间结果存脚本变量/文件，不进主上下文**；最多 ~1000 并行子代理；`/workflows` 面板显示阶段 + 每 worker 状态 + token 消耗 | ① 编排者上下文只进摘要不进全量输出（subclaw 现行架构的平民版对标）；② 回显粒度照抄 `/workflows` 面板：`{worker_id, phase, last_event, tokens}`；③ 对抗性验证模式（独立 reviewer worker 质疑结论） |
| **Codex multi-agent V2** | `~/.codex/agents/*.toml`（name/description/model/sandbox_mode/developer_instructions）；`[agents]` 段 `max_threads`/`max_depth` + 自然语言路由策略；`exec --json` 输出 **JSONL 事件流**（thread.started/turn.started/item.completed），进度走 stderr、结果走 stdout；`--output-schema` 校验 | ① codex 分支 worker 一律 `codex exec --json --color never`，解析 `item.completed + agent_message` 取结果，turn/tool 事件转 heartbeat；② 照抄 TOML agent 的 sandbox_mode 分级（只读扫描 / 可写实现）；③ `[agents]` 段的"自然语言路由策略"写法可移植到各引擎 SKILL.md 开头 |
| **pi**（badlogic/pi-mono，~57k★） | 极简内核 4 工具；**子代理 = 用 bash spawn 自己的 `--print` 实例**，输出全可见可 grep；tmux 扩展：每个子代理独立面板，主 agent 维护列表 + 状态；结果文件进文件出 | ① 回显最佳模型：worker 输出走独立可见通道（`worker-id.log` + 周期摘要），不灌编排者上下文；② 结果交接文件化：worker 写约定路径，编排者读文件，stdout 只做摘要双保险；③ pi 验证了"CLI 自调用"范式可行——正是 subclaw worker 的本质 |
| **kimi-cli / kimi-code**（MoonshotAI，两代产品） | 见 §四 专章 | 见 §四 |
| **Anthropic multi-agent research system** | Opus lead + Sonnet 并行 worker；多代理 token ≈ 单 agent 的 **15 倍**；计划外置存储；专职 CitationAgent | ① 派单前显式判断"值得并行吗"；② 计划写入 `.subclaw/plan.md` 防压缩失忆；③ 审计可拆独立 CitationChecker 子任务 |
| **memorix**（AVIDS2/memorix，~521★，MCP memory server） | local-first 跨 agent 记忆层；六层记忆（Observation/Reasoning/Git/Code/Curated/Compact Continuity）；SQLite + Orama；候选→审核→批准的记忆门控；micro 工具档案（只暴露 7 个工具） | ① 可直接 MCP 接入解决跨会话失忆；② 抄六层模型与"候选记忆需审核"防幻觉污染；③ 给廉价 worker 的 MCP 工具面保持最小；④ 风险：star 低、迭代激进，建议借鉴架构思路或 fork |
| **LiteLLM** | 两层容错（组内重试 + 跨模型 fallback）；预算系统；least-busy 调度 | 补上 subclaw 缺失的 circuit breaker（`--budget` 有文档无实现）；stream 路径补 failover |
| **claude-flow / ClawTeam** | 文件信箱 + 原子写（tmp+rename）+ 文件锁 + TTL GC；worktree 隔离 | 状态文件原子写 + 定期 GC；写任务 worker 强制 worktree |

### 2.3 上下文管理与压缩（Anthropic context engineering）
- **核心公式**：找到最大化期望结果的最小高信号 token 集合（context rot：token 越多召回越差）。
- 三大长程技术：**Compaction**（摘要 + 保留最近 5 文件）、**结构化笔记**（NOTES.md/文件型 memory tool）、**Sub-agent 隔离**（子代理探索数万 token、只回传 1-2K 摘要）。
- 对 subclaw：`[EVIDENCE]` 回传限流 ≤2K token，原文落盘按路径引用；编排者两级压缩（先清理旧 PROGRESS/已消费 EVIDENCE，再摘要压缩）。
- 警惕：反复重写记忆会引入失真（arXiv《Useful Memories Become Faulty When Continuously Updated by LLMs》）→ 证据包追加不覆盖。

---

## 三、引擎特化路由设计（防串线）

### 3.1 路由总原则
**物理隔离 > 运行时检测**。三引擎各装各的技能包，每个技能包只描述自己引擎的语法；共用层只保留协议与进程管理，不含任何引擎原生委派语法。

```
                 ┌─────────────────────────────────────────┐
                 │         共用层（引擎无关）                │
                 │  brief 协议 / 标记协议 / 报告格式        │
                 │  claw-proxy API / 状态聚合 / 预算账本     │
                 └───────┬───────────┬───────────┬─────────┘
                         │           │           │
              ┌──────────┴──┐ ┌──────┴─────┐ ┌───┴──────────┐
              │ claude 分支  │ │ codex 分支  │ │ kimi 分支     │
              │ SKILL.md     │ │ SKILL.md   │ │ SKILL.md      │
              │ + sh runner  │ │ + ps1 runner│ │ + ps1/sh runner│
              └─────────────┘ └────────────┘ └──────────────┘
```

### 3.2 每个技能包开头的强制路由声明（模板）
```markdown
## ROUTING RULES（必须最先遵守）
1. 当前编排引擎 = <claude|codex|kimi>。本文件中描述的委派方式是本引擎唯一合法路径。
2. 禁止调用其他引擎的原生子代理机制（Claude Task 工具 / Codex TOML agents / Kimi --agent-file、/swarm）。
3. 允许 spawn 任意引擎 CLI 作为外部 worker 进程（-p/exec 模式），但必须通过本技能的 runner 脚本，不得手写各引擎专属参数。
4. 若用户要求用另一引擎的子代理机制，先说明该机制在当前引擎不可用，给出本引擎等价方案。
```

### 3.3 三分支对照表（adapter 边界）

| 维度 | Claude 分支 | Codex 分支 | Kimi 分支 |
|---|---|---|---|
| 技能安装位置 | `~/.claude/commands/subclaw.md` + `~/.claude/scripts/` | `~/.codex/skills/subclaw/` | `~/.kimi-code/skills/subclaw/`（新代）或 `~/.kimi/`（旧代） |
| 原生子代理机制 | `.claude/agents/*.md` + Task 工具 | `~/.codex/agents/*.toml` + `[agents]` 段 | `--agent-file` / `/coder` `/explore` `/plan` / `/swarm` |
| worker 启动命令 | `claude -p --output-format stream-json --verbose` | `codex exec --json --color never` | `kimi -p --output-format stream-json` |
| 结果提取 | stream-json 的 result 事件 | JSONL 过滤 `item.completed`+`agent_message` | JSONL 最后一个 Assistant 消息 |
| 进度流 | stdout JSONL（**需改，现行 text 模式不流式**） | stdout JSONL，进度在 stderr | **stdout 天然实时流式** |
| 权限控制 | `--permission-mode` / `--allowedTools` | `--sandbox read-only` / `--full-auto` | `-p` 固定 auto 权限 + deny 规则 |
| 重试 | 3 次 + 指数退避（已有） | 无（**需补**） | 无（需补） |

共用层职责（唯一允许的跨引擎代码）：进程 spawn、JSONL 逐行解析、事件归一化为
`{worker_id, ts, type: progress|tool|heartbeat|ask|done, payload}`。

---

## 四、Kimi Code 专章（明天要用）

### 4.1 世代确认（重要）
存在两代产品，配置路径不兼容：
- **kimi-cli**（Python 旧代）：配置 `~/.kimi/config.toml`，子代理用 YAML agent 文件 + Task 工具，有原生后台任务运行时（`[background]` 段：`max_running_tasks`/`agent_task_timeout_s`）。
- **kimi-code**（TS 新代，0.4.0+，基于 pi-tui）：配置 `~/.kimi-code/config.toml`，内置 `/coder` `/explore` `/plan` 子代理 + `/swarm` 群组模式，skills 四级发现目录（`~/.kimi-code/skills/` → `~/.agents/skills/` → 项目级）。

**本机残留的 `~/.kimi-code/config.toml` 属于新一代 kimi-code**。重装后先 `kimi --version` 确认世代。

### 4.2 接入 claw-proxy
```toml
# ~/.kimi-code/config.toml（新代）追加
[providers.claw]
type = "openai"                      # 与本机现有 agentrouter 写法一致；旧代用 openai_legacy
base_url = "http://127.0.0.1:4748/v1"
api_key = "proxy-managed"

[models."claw/<model-id>"]
provider = "claw"
model = "<model-id>"
max_context_size = 131072
capabilities = ["tool_use"]
```
**坑**：kimi 会优先读 `OPENAI_BASE_URL`/`OPENAI_API_KEY` 环境变量并覆盖 config.toml → worker 启动前必须清掉这两个变量。

### 4.3 Kimi 分支的回显优势（三引擎里最好）
- `kimi -p` **默认就把 Assistant 输出实时流式写 stdout**（对比 claude -p 结束才输出）；
- `--output-format stream-json` 每行一个 JSON：Assistant 消息、tool_calls、Tool 结果逐条出现 → **tool_calls 天然是进度信号**；
- thinking 与工具进度走 stderr，与正文分流，管道友好；
- `-p` 模式固定 auto 权限（不等审批），静态 deny 规则仍生效。

### 4.4 Kimi worker runner 设计（run_kimi_claw_pool.ps1 草案）
1. 复用 codex runner 的队列/状态机骨架（`worker_NNN.status.json` + `pool_status.json`）；
2. worker 命令：`kimi -p "<workerPrompt>" --output-format stream-json`，stdout → `report.stream.jsonl`，stderr → `report.stderr`；
3. 监控循环逐行读 stream.jsonl：
   - `tool_calls` 事件 → status.json 更新为"调用工具 <name>"（实时回显！）；
   - Assistant 文本含 `[PROGRESS]` → 更新进度；含 `[ASK_ORCHESTRATOR]` → 标记 ask；
   - 文件无新增行超过 120s → status=STUCK（MemCon is_stuck）；
4. 结束后取最后一个 Assistant 消息写入 report `[OUTPUT]`；
5. 补 codex runner 缺的**重试**（失败/429 重试 2 次）。

### 4.5 Kimi 原生能力备选（不必第一天全用）
- `/swarm <task>`：kimi 原生并行子代理——适合 Kimi 作为编排者时替代自写队列的轻量场景；
- 旧代 `run_in_background=true` + `[background]` 段：原生 worker 生命周期管理（含超时通知主 agent）；
- Hooks（Beta）：`PreToolUse/PostToolUse` 打点，heartbeat 的另一条原生通道；
- `kimi acp`（JSON-RPC over stdio）：深度程序化集成的备选；
- `kimi mcp import --from claude`：直接迁移 Claude 的 MCP 配置。

---

## 五、回显协议 v2 设计（三引擎统一归一化）

```
worker 进程 stdout (JSONL)
    │  adapter（每引擎一个解析器）
    ▼
归一化事件：{"worker_id":"w01","ts":...,"type":"tool","payload":{"name":"Read"}}
    │
    ├─→ worker_001.status.json   （原子写 tmp+rename，UI/status 脚本读）
    ├─→ pool_status.<stamp>.json （聚合快照，tree UI / statusline 读）
    └─→ report.md 追加（[PROGRESS]/[ASK] 行，供编排者事后审计）
```

规则：
1. **心跳**：每 30s 无事件写一次 heartbeat；连续 120s 无事件标 STUCK，连续 2 次 STUCK 终止重派。
2. **通信预算**：单 worker `[PROGRESS]` ≤50 条；超出强制收敛到 `[WORKER_DONE]` 或 `[ASK_ORCHESTRATOR]`。
3. **回显给人看的三通道**（借鉴 Claude Code 生态）：
   - TodoWrite activeForm（编排者 UI 原生渲染）；
   - status 脚本 / tree UI（盯盘者）；
   - 长任务后台化 + 完成通知。
4. **结果交接文件化**（pi 模式）：worker 结论写 `.subclaw/out/<worker-id>.md`，编排者读文件 + stdout 摘要双保险。

---

## 六、上下文管理与记忆方案决策

| 决策点 | 结论 | 依据 |
|---|---|---|
| worker 回传格式 | 摘要 + 证据指针，EVIDENCE ≤2K token，原文落盘 | Anthropic sub-agent 模式 + Dynamic Workflows |
| 编排者计划 | 派单前写 `.subclaw/plan.md` | Anthropic lead 写 Memory 防压缩失忆 |
| 跨会话记忆 | 短期：自建 `.subclaw/memory.jsonl`（路由统计表 + 成功案例）；中期评估 memorix MCP 接入 | Agent-as-a-Router 记忆闭环；memorix 六层模型 |
| 记忆写入 | 候选→验证→批准门控，追加不覆盖 | memorix + 记忆失真论文 |
| 压缩策略 | 两级：清理旧 PROGRESS/已消费 EVIDENCE → 摘要 + 最近 5 文件 | Claude Code compaction 配方 |

---

## 七、路线图（优先级）

### P0（阻塞明天 Kimi Code 使用）
1. 重装 kimi-code，确认世代与 `--version`；
2. 写 `cli-skills/kimi/subclaw/`（SKILL.md + run_kimi_claw_pool.ps1），按 §3.2 路由声明 + §4.4 runner 设计；
3. config.toml 加 claw provider，清 OPENAI_* 环境变量；
4. 修 proxy 两个致命 bug（tool_use input_json_delta 累积、response.completed 带完整 output）——否则 kimi worker 走转换链时会踩同样的坑。

### P1（稳定性）
5. 修 run-claw-pool.sh 的 EXIT grep 空格 bug + 补 `--budget`（或删文档）；
6. stream 路径补 failover；codex/kimi runner 补重试；
7. claude runner 改 `--output-format stream-json --verbose` 解析，修复回显失效；
8. PS 5.1 显式 UTF-8 编码处理。

### P2（增强）
9. 路由记忆库：worker 模型 × 任务类型成功率统计写进编排 prompt（Agent-as-a-Router 零成本项）；
10. 审计清单化 + 廉价预检闸门（MAST 47% 教训）；
11. 心跳/STUCK 检测 + 通信预算（MemCon is_stuck）；
12. 评估 memorix MCP 或自建六层记忆。

---

## 附：主要来源索引
- MemCon: arXiv:2607.13591
- Agent-as-a-Router: arXiv:2606.22902
- MAST: arXiv:2503.13657 | MoA: arXiv:2406.04692 | FrugalGPT: arXiv:2305.05176 | RouteLLM: arXiv:2406.18665
- Claude Dynamic Workflows: claude.com/blog/a-harness-for-every-task-dynamic-workflows-in-claude-code
- Codex subagents: developers.openai.com/codex/subagents
- pi: github.com/earendil-works/pi（原 badlogic/pi-mono）
- kimi-cli: github.com/MoonshotAI/kimi-cli / moonshotai.github.io/kimi-cli
- kimi-code: github.com/MoonshotAI/kimi-code / kimi.moonshot.cn/code/docs
- memorix: github.com/AVIDS2/memorix
- Anthropic: multi-agent research system / effective context engineering
