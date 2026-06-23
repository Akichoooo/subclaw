<div align="center">

# subclaw

### 面向 Claude Code / Codex CLI / Aider / Cursor 的多模型 LLM 网关

**斜杠命令 + FastAPI 代理。** 会话钉定的多 Key 轮询、Anthropic 与 OpenAI 协议双向翻译、限流故障转移、预算熔断器。
**在以读取为主的工作负载上，将 Claude API 成本降低 60-90%** —— 把繁重的读取下发给廉价模型集群，让 Opus 只做最终审计。

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-009688.svg)
![自托管](https://img.shields.io/badge/自托管-100%25-success.svg)
![无 SaaS](https://img.shields.io/badge/无_SaaS-数据不出本机-orange.svg)
![Anthropic 兼容](https://img.shields.io/badge/Anthropic-兼容-D97757.svg)
![OpenAI 兼容](https://img.shields.io/badge/OpenAI-兼容-412991.svg)

[快速开始](#-快速开始) · [工作原理](#-工作原理) · [完整文档](docs/) · [English](README.md) · [常见问题](docs/faq.md)

</div>

---

## 痛点

只要你在生产环境用 **Claude Code、Codex CLI、Aider 或 Cursor**，一定踩过至少一个：

- 💸 **账单失控。** 一次 Opus 在 5 万 token 仓库上跑死循环就是 7.5 美元。一个下午烧 150 美元并不罕见。
- 🚦 **HTTP 429 限流。** 两个并发 Agent 一起跑，单 Key 立刻被限流。
- 🔒 **厂商锁定。** 80% 的工作是"扫 50 个文件找未使用的 import"——`gpt-4o-mini` 百分之一的成本就能干，但你用 Opus 全价在做。
- 🧠 **上下文膨胀。** 昂贵的模型把 8 万 token 用来重读代码，本来 200 token 的摘要就够了。

`/subclaw` 是一个 **斜杠命令 + FastAPI 网关**，四件事一起解决。

---

## 为什么选 subclaw

| 你能拿到 | 实现方式 |
|---|---|
| **成本下降 60-90%**（读取密集型） | 扫、起草、检索等重活交给比 Opus 便宜约 15-20 倍的模型；Opus 只审计最终摘要。 |
| **绕过 429 限流** | 多把 API Key 在 worker session 之间轮询，限流时自动漂移。 |
| **提示缓存亲和性** | 每个 session 钉在同一把 Key，Anthropic 提示缓存持续命中，最高 90%。 |
| **协议自动翻译** | 使用 Claude Code（Anthropic 协议）的 worker 可以直连 OpenAI 端点，零代码改动。 |
| **预算熔断器** | 单 session 与单日美元上限，到点停机，绝不让你破产。 |
| **完全自托管** | 一行 `python app.py` 跑在 `localhost:4748`，Key 不出本机。 |
| **模型无关** | Anthropic、OpenAI、OpenRouter、任何 Anthropic 兼容端点都支持。三档分级：`cheap` / `balanced` / `smart`。 |

---

## 工作原理

```
         你（Claude Code / Codex / Aider）  =  团队主管 / 调度者
                          |
                          |  /subclaw "审计这个仓库"
                          |  （Step 0.5：先写下可核验的验收标准）
                          v
              run-claw-pool.sh   （把任务拆成 N 份，并行下发给 N 个 worker；
                          |        每个 brief 可用 tools:/permission: frontmatter 覆盖权限）
                          v
              claw-proxy :4748   （持有 Key 池，按 session 钉定 Key，
                          |        协议翻译，429 故障转移）
                          v
              Worker 模型（cheap / balanced / smart）—— 隔离上下文
              把精简的 file:line 证据回传给你
                          |
                          v
              你先自审，再派一个独立 JUDGE（smart，只读）对照验收标准
              返回 JUDGE_VERDICT: TRUE|PARTIAL|FALSE。
              Judge 循环上限 3 轮；超过则上报人类——
              你不再是“是否完成”的唯一裁决者。
                          |
                          v
              你汇总并应用 → 最终结论
              （整个流程在 GET /orchestration + dashboard “Orchestration” 区块可见）
```

最关键的细节：**`x-session-id` 会话亲和性**。完成多轮任务的 worker 会一直命中同一把 API Key，让 Anthropic 的提示缓存保持温热。其他网关都没做这件事。

---

## 快速开始

### 1. 启动网关

```bash
git clone https://github.com/Akichoooo/subclaw.git
cd subclaw/proxy
cp keys.example.json keys.json
# 编辑 keys.json：填入 API Key、模型别名与预算上限

python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python app.py
# claw-proxy 监听 http://localhost:4748
```

### 2. 安装斜杠命令

**如果你用 Claude Code：**
```bash
cp ./cli-skills/claude/subclaw.md ~/.claude/commands/
cp ./cli-skills/run-claw-pool.sh ~/.claude/scripts/
cp ./cli-skills/live_tree_ui.py ~/.claude/scripts/
chmod +x ~/.claude/scripts/run-claw-pool.sh
chmod +x ~/.claude/scripts/live_tree_ui.py
```

**如果你用 Codex CLI / Aider / Cursor：** 见 [`docs/integrations.md`](docs/integrations.md)。

### 3. 开始使用

```
/subclaw 找出后端所有未使用的 import，并提供 file:line 证据
/subclaw 给 src/payments/ 起草单元测试
/subclaw 审计整个仓库的安全隐患
```

你的主模型把任务拆成 N 份，代理并行下发给廉价模型，你读 N 份精简报告即可。

---

## 适用场景

- 🧹 **大规模代码审计** —— 50 个文件，Opus 只看 50 份摘要。
- 🔍 **全仓库检索** —— "找出所有调用 `deprecated_api()` 的位置"。
- 🧪 **测试生成** —— 用廉价模型起草 30 个测试文件，Opus 复核。
- 📝 **文档补全** —— 给 200 个函数批量生成 docstring。
- 🛡️ **安全扫描** —— 在 monorepo 内搜索硬编码的密钥。
- 🔁 **重构规划** —— 廉价模型提 diff，聪明模型挑刺，Opus 整合。

完整剧本见 [`docs/use-cases.md`](docs/use-cases.md)。

---

## 基准数据

> *基于下方假设的示意性测算，非独立实测；实际节省随工作负载差异很大。*

| 场景 | 单独 Opus（无代理） | subclaw（Opus + 廉价虫群） | 节省 |
|---|---|---|---|
| 审计 50 个文件（5 万 token） | 7.5 美元输入 + 10 次循环 ≈ 75 美元 | Opus 0.10 美元 + 50 路 Haiku 并行 0.0075 美元 ≈ 0.11 美元 | **约 99%（估算）** |
| 全仓库 grep（20 万 token） | 30 美元输入 | 0.30 美元（20 万输入 × 1.50/1M 缓存价） | **约 99%（估算）** |
| 每日预算：20 次审计 | 约 1500 美元 | 约 15 美元 | **约 99%（估算）** |

方法论与完整数据见 [`docs/benchmarks.md`](docs/benchmarks.md)。

---

## 与同类项目的对比

`subclaw` 是众多 LLM 网关之一。为什么要选它？

| 特性 | subclaw | LiteLLM | OpenRouter | Portkey | claude-code-router |
|---|---|---|---|---|---|
| 完全自托管、无 SaaS | ✅ | ✅ | ❌ | ⚠️ 部分 | ✅ |
| **会话亲和性，提示缓存命中** | ✅ 核心 | ❌ | n/a | ❌ | ❌ |
| **多 Key 轮询 + 预算熔断** | ✅ | ❌ | n/a | ⚠️ | ❌ |
| **Anthropic 与 OpenAI 流式翻译** | ✅ | ✅ | n/a | ✅ | ✅ |
| **斜杠命令 UX**（Claude Code / Codex） | ✅ | ❌ | ❌ | ❌ | ✅ |
| 429 故障转移 + Key 重新绑定 | ✅ | ⚠️ | n/a | ✅ | ❌ |
| 模型无关 | ✅ | ✅ | ✅ | ✅ | ⚠️ 部分 |
| **专为成本优化的虫群而设计** | ✅ | ❌ | ❌ | ❌ | ❌ |

完整对比见 [`docs/comparisons.md`](docs/comparisons.md)。

---

## 常见问题

**问：subclaw 必须搭配 Claude Code 用吗？**
答：不需要。斜杠命令只是前端之一。任何讲 Anthropic 或 OpenAI 协议的 HTTP 客户端都能直接对接网关。

**问：它和 LiteLLM 有什么区别？**
答：LiteLLM 是一个支持 100 多家厂商的协议路由库——是个优秀库，不是成本优化器。subclaw 只做一件事：把脏活下发给廉价 Key，让昂贵模型只看汇总，配合会话钉定提示缓存与预算熔断。需要 30 家厂商时选 LiteLLM，需要砍 Claude 账单时选 subclaw。

**问：它和 OpenRouter 有什么区别？**
答：OpenRouter 是 SaaS。你把 Key 交出去（或付钱给它），它负责路由。subclaw 跑在你自己的机器上，Key 不出本机。而且 subclaw 的会话钉定是 OpenRouter 没有的独门提示缓存优化。

**问：能搭配非 Anthropic 模型吗？**
答：能。任何 OpenAI 协议端点、任何 Anthropic 协议端点、任何 Anthropic 兼容厂商都可以。在 `keys.json` 里配置模型并指定 tier（`cheap` / `balanced` / `smart`）即可。

**问：把真 Key 给 worker 安全吗？**
答：不安全——这正是 subclaw 要解决的问题。网关独占 Key，worker 只持有代理 URL。

**问：预算熔断器是什么？**
答：在 `keys.json` 的 `global_proxy_settings.circuit_breaker` 里配置。`max_spend_per_session_usd` 和 `max_spend_per_day_usd` 触发后立刻停机，绝不出现意外账单。

**问：怎么加一个新模型？**
答：在 `keys.json` 里加一条记录，写明 `url`、`key`、`model_id`、`alias` 与 `tier`。下一次请求时网关自动热加载。

完整问答见 [`docs/faq.md`](docs/faq.md)。

---

## 文档

- 📐 [架构详解](docs/architecture.md) —— 会话钉定、提示缓存亲和、故障转移是怎么工作的。
- ⚖️ [横向对比](docs/comparisons.md) —— vs LiteLLM、OpenRouter、Portkey、claude-code-router、one-api。
- ❓ [常见问题](docs/faq.md) —— 30+ 个关于安装、成本、安全、扩展的问题。
- 📊 [基准数据](docs/benchmarks.md) —— 成本、缓存命中、延迟的完整数据。
- 🎯 [使用场景](docs/use-cases.md) —— 6 个真实场景与命令示例。
- 🔌 [集成指南](docs/integrations.md) —— Codex CLI、Aider、Cursor 与自定义客户端。
- 🚀 [Show HN 投稿稿](docs/show-hn-post.md) —— 可直接复制粘贴的 HN 文本。
- 📣 [awesome 列表投稿](docs/awesome-list-submissions.md) —— 6 个 awesome-* 列表的 PR 模板。

---

## 路线图

- [ ] 提示缓存命中率自动调优（自动检测缓存未命中并重新分配 Key）
- [ ] OpenAI function-calling 与 Anthropic tool_use 完整双向翻译（目前是尽力实现）
- [ ] Web 仪表盘：每模型成本、缓存、延迟的实时图表
- [ ] 多用户鉴权 + 每用户预算隔离
- [ ] PyPI 包：`pip install subclaw`
- [ ] Kubernetes 部署 Helm Chart

有新功能想法？[开一个讨论](https://github.com/Akichoooo/subclaw/discussions)。

---

## 贡献

欢迎 PR，请先读 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

- 🐛 [反馈 Bug](https://github.com/Akichoooo/subclaw/issues/new?template=bug_report.md)
- 💡 [提功能建议](https://github.com/Akichoooo/subclaw/issues/new?template=feature_request.md)
- 🔒 [报告安全问题](.github/SECURITY.md)

---

## 许可证

[MIT](LICENSE) © Akichoooo

---

## 致谢

- Anthropic 团队实现的 prompt cache——整套架构都建立在这项能力之上。
- `claude-code-router` 项目，最早在 Claude Code 上提出多模型思路。
- LiteLLM 项目，向社区展示了协议翻译可以走多远。
- 每一位提交 issue、PR 或愿意试用的朋友。🙏

---

## Star 趋势

<a href="https://star-history.com/#Akichoooo/subclaw&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=Akichoooo/subclaw&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=Akichoooo/subclaw&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=Akichoooo/subclaw&type=Date" />
  </picture>
</a>

---

<div align="center">

如果 subclaw 帮你省下了 Claude 账单，**点个 Star** ⭐ —— 这会直接带来更多贡献者和更低的成本。

[⬆ 回到顶部](#subclaw)

</div>
