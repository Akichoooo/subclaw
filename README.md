<div align="center">

# subclaw

### Multi-Model LLM Gateway for Claude Code · Codex CLI · Aider · Cursor

**Slash command + FastAPI proxy.** Session-pinned multi-key rotation, Anthropic↔OpenAI protocol translation, rate-limit failover, budget circuit breaker.
**Reduce Claude API spend by up to 98%** by dispatching heavy tasks to a swarm of cheap worker models.

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-009688.svg)
![Self-Hosted](https://img.shields.io/badge/self--hosted-100%25-success.svg)
![No SaaS](https://img.shields.io/badge/no_SaaS-lock--in--free-orange.svg)
![Anthropic Compatible](https://img.shields.io/badge/Anthropic-compatible-D97757.svg)
![OpenAI Compatible](https://img.shields.io/badge/OpenAI-compatible-412991.svg)

[Quick Start](#-quick-start) · [How It Works](#-how-it-works) · [Docs](docs/) · [中文文档](README_zh.md) · [FAQ](docs/faq.md)

</div>

---

## The Problem

If you use **Claude Code, Codex CLI, Aider, or Cursor** for serious work, you have hit at least one of these:

- 💸 **The bill.** One Opus loop on a 50k-token repo costs `$7.50`. One bad afternoon: `$150`.
- 🚦 **HTTP 429.** "Too Many Requests" — the moment you fire two agents in parallel, your single API key is rate-limited.
- 🔒 **Vendor lock-in.** You pay full price even when 80% of the work is "scan 50 files, find unused imports" — a task a `gpt-4o-mini` would handle for 1/100th the cost.
- 🧠 **Context bloat.** Your expensive model wastes 80k input tokens re-reading code it could have summarized in 200.

`/subclaw` is a **slash command + a FastAPI gateway** that fixes all four.

---

## Why subclaw

| What you get | How it works |
|---|---|
| **98% cost reduction** | Heavy work (scan, draft, find) goes to cheap models; Opus only audits the final summary. |
| **Bypass 429 rate limits** | N API keys, round-robin across worker sessions, transparent failover on throttling. |
| **Prompt cache locality** | Each session is **pinned to one key** so Anthropic's prompt cache stays warm. Up to 90% cache hit rate. |
| **Drop-in protocol translation** | Workers using Claude Code (Anthropic protocol) can hit OpenAI endpoints transparently. No code changes. |
| **Budget circuit breaker** | Per-session and per-day USD cap. Stops spending before the bill arrives. |
| **Self-hosted, no SaaS** | Single `python app.py` on `localhost:4748`. Your keys never leave your box. |
| **Generic over models** | Works with Anthropic, OpenAI, OpenRouter, any Anthropic-compatible endpoint. Tiers: `cheap` / `balanced` / `smart`. |

---

## How It Works

```
         You (Claude Code / Codex / Aider)  =  Team Lead / Supervisor
                          |
                          |  /subclaw "audit this repo"
                          v
              run-claw-pool.sh   (fans N briefs to N workers, parallel)
                          |
                          v
              claw-proxy :4748   (owns key pool, pins one key/session,
                          |        translates protocol, fails over on 429)
                          v
              Worker models (cheap / balanced / smart) — isolated context
              return CONCISE file:line evidence back to you
                          |
                          v
              You synthesize + audit the N reports → final answer
```

The killer detail: **`x-session-id` session affinity**. A worker that finishes a multi-turn task will keep hitting the same API key, so Anthropic's prompt cache stays warm. Other gateways do not do this.

---

## Quick Start

### 1. Start the gateway

```bash
git clone https://github.com/Akichoooo/subclaw.git
cd subclaw/proxy
cp keys.example.json keys.json
# Edit keys.json: drop in your API keys, model aliases, and budget caps.

python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python app.py
# claw-proxy listening on http://localhost:4748
```

### 2. Install the slash command

**For Claude Code:**
```bash
cp ./cli-skills/claude/subclaw.md ~/.claude/commands/
cp ./cli-skills/run-claw-pool.sh ~/.claude/scripts/
chmod +x ~/.claude/scripts/run-claw-pool.sh
```

**For Codex CLI / Aider / Cursor:** see [`docs/integrations.md`](docs/integrations.md).

### 3. Use it

```
/subclaw find all unused imports in the backend and provide file:line evidence
/subclaw draft unit tests for src/payments/
/subclaw audit this whole repo for security smells
```

Your main model breaks the task into N briefs, the proxy fans them to cheap models in parallel, and you read N concise reports.

---

## Use Cases

- 🧹 **Mass code auditing** — 50 files, Opus only reads 50 summaries.
- 🔍 **Repo-wide search** — "find all callers of `deprecated_api()`".
- 🧪 **Test generation** — draft 30 test files with a cheap model, Opus reviews.
- 📝 **Documentation sweep** — generate docstrings for 200 functions, batch.
- 🛡️ **Security smell scan** — "audit for hardcoded secrets" across the monorepo.
- 🔁 **Refactor planning** — cheap model proposes diffs, smart model critiques, Opus synthesizes.

See [`docs/use-cases.md`](docs/use-cases.md) for the full playbook.

---

## Benchmarks

| Scenario | Single Opus (no proxy) | subclaw (Opus + cheap swarm) | Saving |
|---|---|---|---|
| Audit 50 files (50k tokens) | `$7.50` input + 10× loop ≈ `$75.00` | Opus: `$0.10` + 50× Haiku parallel `$0.0075` ≈ `$0.11` | **~99%** |
| Repo-wide grep (200k tokens) | $30 input | $0.30 (200k input × $1.50/1M cached) | **~99%** |
| Daily budget: 20 audits/day | ~$1,500 | ~$15 | **~99%** |

See [`docs/benchmarks.md`](docs/benchmarks.md) for methodology and full data.

---

## Comparison

`subclaw` is one of several LLM gateways. Why this one?

| Feature | subclaw | LiteLLM | OpenRouter | Portkey | claude-code-router |
|---|---|---|---|---|---|
| Self-hosted, no SaaS | ✅ | ✅ | ❌ | ⚠️ partial | ✅ |
| **Session affinity for prompt cache** | ✅ core | ❌ | n/a | ❌ | ❌ |
| **Multi-key rotation w/ budget breaker** | ✅ | ❌ | n/a | ⚠️ | ❌ |
| **Anthropic↔OpenAI streaming translation** | ✅ | ✅ | n/a | ✅ | ✅ |
| **Slash command UX** (Claude Code / Codex) | ✅ | ❌ | ❌ | ❌ | ✅ |
| 429 failover with key rebinding | ✅ | ⚠️ | n/a | ✅ | ❌ |
| Generic over any model | ✅ | ✅ | ✅ | ✅ | ⚠️ partial |
| **Designed for cost-optimized swarms** | ✅ | ❌ | ❌ | ❌ | ❌ |

Full comparison: [`docs/comparisons.md`](docs/comparisons.md).

---

## FAQ

**Q: Does subclaw require me to use Claude Code?**
A: No. The slash command is one frontend. You can drive the gateway from any HTTP client that speaks Anthropic or OpenAI protocol.

**Q: How is this different from LiteLLM?**
A: LiteLLM is a 100-provider protocol router — a great library, not a cost optimizer. subclaw is built for one job: keep your expensive model's context window small by fanning cheap work to cheap keys, with session-pinned prompt cache locality and a budget circuit breaker. Use LiteLLM if you need 30 providers. Use subclaw if you need to slash your Claude bill.

**Q: How is this different from OpenRouter?**
A: OpenRouter is a SaaS. You give them your keys (or pay them), they route. subclaw is self-hosted on your box; your keys never leave. Plus, subclaw's session pinning is a unique prompt-cache optimization that OpenRouter doesn't do.

**Q: Will this work with non-Anthropic models?**
A: Yes — any OpenAI-protocol endpoint, any Anthropic-protocol endpoint, any Anthropic-compatible vendor. Configure the model in `keys.json` and assign a tier (`cheap` / `balanced` / `smart`).

**Q: Is it safe to give workers my real API key?**
A: No — that's the whole point. The gateway owns the keys. Workers carry no real credentials, only the proxy URL.

**Q: What's the budget circuit breaker?**
A: Configured in `keys.json` under `global_proxy_settings.circuit_breaker`. `max_spend_per_session_usd` and `max_spend_per_day_usd` halt further requests once hit. No surprise bills.

**Q: How do I add a new model?**
A: Add an entry to `keys.json` with the `url`, `key`, `model_id`, `alias`, and `tier`. The gateway hot-loads on the next request.

See [`docs/faq.md`](docs/faq.md) for the full list.

---

## Documentation

- 📐 [Architecture deep-dive](docs/architecture.md) — how session pinning, prompt cache locality, and failover work.
- ⚖️ [Comparisons](docs/comparisons.md) — vs LiteLLM, OpenRouter, Portkey, claude-code-router, one-api.
- ❓ [FAQ](docs/faq.md) — 30+ questions about setup, costs, security, scaling.
- 📊 [Benchmarks](docs/benchmarks.md) — full cost / cache hit / latency data.
- 🎯 [Use cases](docs/use-cases.md) — 6 real-world scenarios with command examples.
- 🔌 [Integrations](docs/integrations.md) — Codex CLI, Aider, Cursor, custom clients.
- 🚀 [Show HN post draft](docs/show-hn-post.md) — copy-paste text for HN submission.
- 📣 [Awesome list submissions](docs/awesome-list-submissions.md) — PR templates for 6 awesome-* lists.

---

## Roadmap

- [ ] Prompt-cache hit rate auto-tuning (auto-detect cache miss / rekey)
- [ ] OpenAI function-calling → Anthropic tool_use full bidirectional translation (currently best-effort)
- [ ] Web dashboard with per-model cost / cache / latency charts
- [ ] Multi-user auth + per-user budget isolation
- [ ] PyPI package: `pip install subclaw`
- [ ] Helm chart for Kubernetes deployment

Have a feature request? [Open a discussion](https://github.com/Akichoooo/subclaw/discussions).

---

## Contributing

We welcome PRs. Please read [`CONTRIBUTING.md`](CONTRIBUTING.md) first.

- 🐛 [Report a bug](https://github.com/Akichoooo/subclaw/issues/new?template=bug_report.md)
- 💡 [Request a feature](https://github.com/Akichoooo/subclaw/issues/new?template=feature_request.md)
- 🔒 [Report a security issue](.github/SECURITY.md)

---

## License

[MIT](LICENSE) © Akichoooo

---

## Acknowledgments

- The Anthropic team for prompt caching — the entire architecture hinges on `cache_read_input_tokens`.
- The `claude-code-router` project for pioneering the multi-model idea for Claude Code.
- The LiteLLM project for showing the community what's possible with protocol translation.
- Everyone who has filed an issue, opened a PR, or starred the repo. 🙏

---

## Star History

<a href="https://star-history.com/#Akichoooo/subclaw&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=Akichoooo/subclaw&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=Akichoooo/subclaw&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=Akichoooo/subclaw&type=Date" />
  </picture>
</a>

---

<div align="center">

If subclaw saved your Claude bill, **leave a star** ⭐ — it directly fuels more contributors and lower prices for everyone.

[⬆ Back to top](#subclaw)

</div>
