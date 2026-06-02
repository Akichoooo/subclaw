# How subclaw Compares

`subclaw` is one of several LLM gateway / routing projects. This page is a fair, opinionated comparison of the five most common alternatives. The goal is to help you pick the right tool, not to dunk on the others — each is excellent at what it does.

---

## TL;DR

| If you want… | Use |
|---|---|
| 30+ providers, drop-in library, no opinion on cost | **LiteLLM** |
| A hosted SaaS that hides API keys and routes for you | **OpenRouter** |
| An enterprise AI gateway with governance, observability, vendor neutrality | **Portkey** |
| A no-cost-optimizer router for Claude Code only | **claude-code-router** |
| A self-hosted gateway that *slashes your Claude bill* via worker swarms, prompt-cache locality, and budget caps | **subclaw** |

---

## Detailed comparison

### 1. [LiteLLM](https://github.com/BerriAI/litellm)

**What it is**: a Python library + proxy that unifies 100+ LLM providers behind a single OpenAI-compatible interface. Maintained by BerriAI.

**Strengths**:
- 100+ providers supported (Anthropic, OpenAI, Azure, Bedrock, Vertex, Cohere, HuggingFace, Ollama, vLLM, …)
- Battle-tested in production at scale
- Drop-in `openai.ChatCompletion.create(...)` replacement
- LiteLLM Proxy server (separate from the library) does caching, rate limiting, spend tracking

**Where subclaw differs**:
- LiteLLM's proxy does rate limiting **per key**, but does not do **session affinity for prompt cache locality**. A worker doing 50 turns on a multi-message task will have its key rotated, killing the cache.
- LiteLLM's spend tracking is excellent but does not include a **circuit breaker** that auto-stops at a USD cap.
- LiteLLM has no first-party "worker swarm" pattern. It can be used to build one, but the orchestration (decomposing a task into N briefs, fanning them out, aggregating reports) is your job.
- LiteLLM is general-purpose; subclaw is opinionated for **Claude Code + cheap swarm** workflows.

**When to pick LiteLLM over subclaw**: you need more than ~5 providers, or you're building a multi-tenant product that needs deep per-user analytics.

---

### 2. [OpenRouter](https://openrouter.ai/)

**What it is**: a hosted SaaS that exposes a single OpenAI-compatible endpoint and routes to whichever underlying model you pick (Claude, GPT-4, Llama, DeepSeek, …). You pay OpenRouter; they handle auth and routing.

**Strengths**:
- Zero ops. Drop in the API key, it works.
- Single billing relationship for 50+ models.
- Useful for benchmarking: switch models without re-auth.

**Where subclaw differs**:
- **OpenRouter is a SaaS, not self-hosted.** Your requests go to their servers. For some compliance regimes (HIPAA, on-prem, etc.) this is a non-starter.
- **OpenRouter does not do session-pinned key rotation.** It's optimized for *which model*, not *which key for which conversation*. Different problem.
- **OpenRouter has no budget circuit breaker.** You set spend limits at the account level, but no per-session cap that auto-stops an agent.
- **No swarm pattern.** OpenRouter is "send one request to one model". Subclaw's strength is "fan N tasks to N cheap models, aggregate".

**When to pick OpenRouter over subclaw**: you just want to try different models with one API key, or you don't have the appetite to run your own proxy.

---

### 3. [Portkey](https://github.com/Portkey-AI/gateway)

**What it is**: an "AI gateway" SaaS (with a self-host option) targeting enterprise use cases — observability, fallbacks, load balancing, spend tracking, semantic caching, guardrails, fine-grained access control.

**Strengths**:
- The most enterprise-ready of the bunch. SOC2, audit logs, RBAC.
- Semantic cache (caches by embedding similarity, not exact prefix) can yield higher hit rates than prompt cache.
- First-class support for fallbacks, A/B tests, conditional routing.
- Nice UI for the dashboard.

**Where subclaw differs**:
- **Portkey's free/self-host tier is limited.** Full features (guardrails, semantic cache, audit logs) require a paid plan.
- **No session affinity for prompt cache** at the OpenAI-protocol level. The semantic cache is helpful but does not align with Anthropic's prefix-keyed cache.
- **Heavier**: deploying Portkey self-hosted requires Postgres + a bunch of microservices. Subclaw is one Python file.
- **No swarm / brief-decomposition pattern.** Portkey is a request router, not an orchestrator.

**When to pick Portkey over subclaw**: you're shipping an LLM feature inside a multi-team org and need governance, RBAC, audit logs, and an enterprise contract.

---

### 4. [claude-code-router](https://github.com/musistudio/claude-code-router)

**What it is**: a TypeScript-based router that lets you point Claude Code at non-Anthropic models (DeepSeek, GLM, Ollama, etc.) without rewriting the CLI integration. Closest cousin to subclaw in spirit.

**Strengths**:
- Specifically designed for Claude Code (the same target user as subclaw).
- Lightweight, single-purpose, no SaaS.
- Good model-discovery UX.

**Where subclaw differs**:
- **No multi-key rotation.** `claude-code-router` assumes you have one key per provider. If that key gets 429'd, you wait.
- **No prompt-cache locality optimization.** Requests are routed per-call, not per-session.
- **No budget circuit breaker.**
- **No swarm / fan-out.** Each request goes to one model. subclaw's `/subclaw` slash command decomposes a goal into N briefs and fans them out.
- **Lighter feature surface overall.** Good for "let me use DeepSeek instead of Opus"; not designed for "let me slash my Claude bill."

**When to pick claude-code-router over subclaw**: you just want to swap the model Claude Code uses, and you don't have a 429 problem. It's also more mature for that single use case.

---

### 5. [one-api](https://github.com/songquanpeng/one-api) / [new-api](https://github.com/songquanpeng/new-api)

**What they are**: Go-based, self-hosted OpenAI-compatible gateways with channel-based routing (each "channel" = one provider/key). Very popular in Chinese self-hosting communities.

**Strengths**:
- Mature, large user base, well-tested.
- Web UI for managing channels, users, and quotas.
- Per-user quotas and token accounting.

**Where subclaw differs**:
- **No prompt-cache locality.**
- **No Anthropic-protocol awareness.** Speaks OpenAI-protocol only; you'd still need a separate layer to drive `claude` workers.
- **No swarm orchestration** (no brief decomposition, no fan-out).
- **No budget circuit breaker** at the request level.

**When to pick one-api over subclaw**: you're building a small SaaS where multiple users share API keys and need per-user quotas, and OpenAI-protocol is enough.

---

## What subclaw adds that none of the above do

1. **Session affinity for prompt cache locality.** Sticky key-per-session routing that preserves Anthropic's prompt cache across multi-turn worker tasks. None of the five alternatives do this — they all do per-request or per-second rotation, which is fine for chat but kills cache locality for agent workloads.

2. **Slash-command UX for Claude Code / Codex CLI / Aider.** A first-class `/subclaw` command that decomposes a goal into N briefs, dispatches them, and aggregates the reports. Pure gateways expect you to do orchestration in your own code.

3. **Budget circuit breaker.** Per-session and per-day USD cap with HTTP 402 enforcement. Other gateways track spend but don't auto-stop.

4. **Cost-optimized swarm as a first-class pattern.** subclaw is *designed* to make "expensive model + N cheap models in parallel" the easy, default workflow. Other gateways treat it as something you build on top.

5. **Single-file simplicity.** `app.py` is ~1300 lines of Python. Read it top to bottom. Portkey is a fleet of microservices; one-api/new-api is a Go binary; subclaw is "a file you can read".

---

## Decision flowchart

```
Are you OK with a SaaS holding your API keys?
├── Yes  → OpenRouter (or Portkey if you need enterprise governance)
└── No
    ├── Do you need 30+ providers?
    │   ├── Yes  → LiteLLM
    │   └── No
    │       ├── Do you only want to swap Claude Code's model?
    │       │   ├── Yes  → claude-code-router
    │       │   └── No
    │       │       ├── Do you need per-user quotas in a small SaaS?
    │       │       │   ├── Yes  → one-api / new-api
    │       │       │   └── No
    │       │       │       └── You want to slash your Claude bill
    │       │       │           via worker swarms + cache locality +
    │       │       │           budget caps. → subclaw
    │       │       │
    │       │       └── (also subclaw if you have a 429 problem)
```

---

## Contributing to this comparison

If a comparison is unfair or out of date, please open an issue or PR. We want this page to stay honest.
