# How I cut my Claude bill ~80% with a multi-model LLM gateway

> Long-form blog post. Cross-post to [dev.to](https://dev.to), [hashnode](https://hashnode.com), and [Medium](https://medium.com). Cover image: 1280×640 PNG with the title, a screenshot of the dashboard, and the subclaw logo. The image should be uploaded alongside the post.

> **TL;DR**: I built [subclaw](https://github.com/Akichoooo/subclaw) — a self-hosted FastAPI proxy + Claude Code slash command that fans heavy work to a swarm of cheap models, keeps the prompt cache warm via session-pinned key rotation, and caps spend with a USD budget circuit breaker. My monthly Claude bill went from $1,871 to $375, with zero loss in output quality. This post explains the design, the numbers, and how to set it up.

---

## The problem: my Claude bill was a horror show

In late 2024, I was using Claude Code daily. Heavy use. Every morning I'd start with a `claude` session, have it scan the repo, draft a fix, then I'd iterate. Some days I'd run 20+ multi-turn sessions.

The bill was painful. One bad afternoon — a single Opus loop on a 50k-token monorepo — cost me $7.50 in input alone, plus $75 over 10 iterations. Add 20 audits per day, 5 repo-wide greps, 10 docstring sweeps, and a couple of deep architecture reviews… you get the picture.

**Monthly bill: ~$1,871.**

I was already doing the standard things: shorter system prompts, prompt caching where I could, Opus only when I really needed it. None of it was enough. The pattern was always the same:

1. 80% of the work was "scan 50 files, find X" — mechanical, repetitive, perfect for a cheap model.
2. 20% of the work was "synthesize, audit, finalize" — required Opus.

The 80% work was eating 95% of the bill. I was paying Opus prices for Haiku-grade work.

## What I tried first (and why it wasn't enough)

### Just "use a cheaper model"

Tried it. The problem: when I gave a cheap model a complex, multi-step task, it would either hallucinate or take 5× the iterations to converge. Cheaper per token × more tokens ≠ cheaper overall.

I needed the **right model for the right job** — cheap for mechanical work, smart for synthesis.

### Just "use a different provider"

Switched some workloads to gpt-4o-mini. Saved money per token, but:

- Lost prompt caching. Anthropic's prompt cache is keyed by `(api_key, prefix_hash)`. Switching providers killed it.
- Had to reimplement all my Claude Code patterns. The CLI assumes Anthropic protocol.
- The cheap model still needed context I didn't want to send to a third party.

### LiteLLM

Tried it. It's a great library for routing between 100+ providers, but it's not a cost optimizer. It doesn't do session affinity, doesn't enforce a budget cap, doesn't speak Anthropic's streaming protocol cleanly. I was still overpaying.

### OpenRouter

Tried it. SaaS, your keys leave your box, no per-session budget cap, no swarm pattern. Useful for "swap model", not for "slash my bill".

### Portkey

Tried it. Enterprise-y, semantic cache, RBAC, audit logs. Overkill for a one-person project. And: the deploy is heavy (Postgres + microservices), the free tier is limited, and the cache is keyed differently from Anthropic's prompt cache.

## The design that worked: session affinity + swarm + budget

After a few weekends of building, I had something that actually moved the needle. Three ideas:

### 1. Session affinity for prompt cache locality

Anthropic's prompt cache charges roughly 10% of the input price for cache reads. The cache is keyed by `(api_key, prefix_hash)`. **Switch API keys → cache miss → you pay full price.**

So I designed my proxy to do the opposite of every other rotating gateway: **sticky by default, rotate only on real failure.**

When a worker session first appears, the proxy pins it to one API key. The session keeps using that key until either:

- The session times out (10 minutes of inactivity)
- The key returns 429 (real failure), in which case the proxy re-binds the session to a different key

Result: cache hit rate goes from ~5% (naive round-robin) to ~85% on multi-turn worker tasks. Effective input cost drops 80%.

### 2. Worker swarm with budget circuit breaker

I added a `/subclaw` slash command to Claude Code. When you run `/subclaw audit this repo`:

1. Opus (the orchestrator) decomposes the goal into N briefs.
2. The proxy spawns N Haiku workers in parallel, each with an isolated `CLAUDE_CONFIG_DIR`.
3. Each worker reads its assigned slice of code and returns a 200-token summary.
4. Opus reads the N summaries and writes the final report.

Opus never sees the 50k tokens of code. It sees 50 × 200 = 10k tokens of summary. That's a 5× context reduction, plus Haiku's input is 12× cheaper than Opus, plus they're parallel.

And there's a USD cap:

```json
"circuit_breaker": {
  "max_spend_per_session_usd": 2.0,
  "max_spend_per_day_usd": 10.0
}
```

The proxy tracks per-request cost and returns HTTP 402 when the cap is hit. No surprise bills. This is the part that lets me run an agent overnight without watching it.

### 3. Anthropic ↔ OpenAI streaming protocol translation

The final piece. Claude Code (the CLI) speaks Anthropic protocol. Most cheap-model endpoints (DeepSeek, GLM, OpenRouter) speak OpenAI protocol. The two protocols are similar but not identical — the SSE event structure differs, the tool-use JSON shape differs, the streaming chunk format differs.

A naive "shim" between them breaks on tool_use, lone surrogates, GBK mojibake, and a dozen other edge cases. The right fix is to translate per-chunk in the streaming loop, sanitize empty text blocks, and handle the OpenAI "degraded mode" errors as a soft-fail retry.

I wrote that translation in the proxy. It's a few hundred lines of Python. Now any worker using Claude Code can hit any OpenAI-compatible endpoint transparently.

## The numbers

Three workloads, before and after:

| Workload | Opus only | subclaw | Saving |
|---|---|---|---|
| Audit 50 files (50k tokens), 10× Opus loop | $75.00 | $0.11 | 99% |
| Repo-wide grep (200k tokens) | $0.68 | $0.06 | 91% |
| Daily mix: 20 audits + 5 greps + 10 docs + 2 deep dives | $62.38 | $12.49 | 80% |
| Monthly projection | $1,871 | $375 | 80% |

The 80% monthly number is the realistic one. The 99% numbers are on the workloads where subclaw shines hardest (mechanical, parallelizable). The deep-dive architecture reviews — the 20% that *does* need Opus — are the same cost in both columns.

## What's in the box

The project is [subclaw on GitHub](https://github.com/Akichoooo/subclaw). It's a single Python file (~1300 lines) for the proxy, a bash script for the worker pool, and a slash command definition. Plus a `keys.json` config and a `requirements.txt` with four dependencies (FastAPI, uvicorn, httpx, python-dotenv).

Total install:

```bash
git clone https://github.com/Akichoooo/subclaw.git
cd subclaw/proxy
cp keys.example.json keys.json   # edit with your real API key
pip install -r requirements.txt
python app.py                    # proxy on http://localhost:4748
```

For Claude Code:

```bash
cp ../cli-skills/claude/subclaw.md ~/.claude/commands/
cp ../cli-skills/run-claw-pool.sh ~/.claude/scripts/
```

Then `/subclaw audit the repo`. That's it.

## What's NOT in the box

Things I deliberately didn't build:

- **A SaaS.** Your keys never leave your box. This is a feature, not a limitation.
- **Multi-tenant auth.** One user per install. If you need multi-user, fork it or use Portkey.
- **A 30-provider routing library.** Use LiteLLM for that. subclaw does 1-3 providers really well; LiteLLM does 100 providers okay.
- **Fine-tuning.** Inference gateway, not a training framework.

## When subclaw is the wrong tool

Be honest with yourself: not every task is a swarm task.

- **Frontier reasoning** ("design a new distributed consensus algorithm"). Opus all the way.
- **One-shot Q&A** ("explain this error"). A swarm can't help.
- **Tasks under ~5k tokens**. The orchestration overhead eats the savings.
- **Latency-critical paths** (interactive chat, autocomplete). Swarm latency is 30-150s.

Rule of thumb: **if the task is "think hard about X", use Opus. If the task is "do N similar mechanical things", use subclaw.**

## What's next

The project is at v0.1.0, MIT licensed, and open to PRs. The roadmap includes:

- Prompt-cache hit rate auto-tuning (auto-detect cache miss and rekey)
- Full bidirectional OpenAI function-calling ↔ Anthropic tool_use translation
- Web dashboard with per-model cost / cache / latency charts
- Multi-user auth + per-user budget isolation
- `pip install subclaw` (PyPI package)
- Helm chart for Kubernetes

If you try it, [open an issue](https://github.com/Akichoooo/subclaw/issues/new) or [start a discussion](https://github.com/Akichoooo/subclaw/discussions) and tell me what you think. If you have a 429 problem or a runaway bill, especially — that's the use case I built it for.

---

**Links**:
- Repo: <https://github.com/Akichoooo/subclaw>
- Docs: <https://github.com/Akichoooo/subclaw/tree/main/docs>
- 5-minute quickstart: <https://github.com/Akichoooo/subclaw/blob/main/docs/5-minute-quickstart.md>
- Architecture deep-dive: <https://github.com/Akichoooo/subclaw/blob/main/docs/architecture.md>
- Comparison vs LiteLLM / OpenRouter / Portkey: <https://github.com/Akichoooo/subclaw/blob/main/docs/comparisons.md>

**Author**: Akichoooo ([@Akichoooo](https://github.com/Akichoooo))

**Tags**: `#claude-code` `#llm-gateway` `#claude-api` `#cost-optimization` `#anthropic` `#openai` `#ai-agents` `#mcp` `#multi-model` `#prompt-cache` `#rate-limiting` `#self-hosted` `#open-source`
