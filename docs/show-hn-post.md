# Show HN: subclaw — multi-model LLM gateway for Claude Code

> Copy-paste this into [news.ycombinator.com](https://news.ycombinator.com/submit) when you're ready to post. **Do not post multiple Show HNs for the same project** — HN is sensitive to this and the mods will mark you down.

---

## Title (max 80 chars)

```
Show HN: subclaw – slash-command + gateway that cut my Claude bill ~80%
```

(67 chars. Good.)

## URL

```
https://github.com/Akichoooo/subclaw
```

## Text body

```
Hi HN,

I'm Akichoooo, the author of subclaw (https://github.com/Akichoooo/subclaw).

Subclaw is a `/subclaw` slash command for Claude Code (and a FastAPI proxy that backs it) that I built to solve a specific problem: I was burning $1,500/month on Claude API, and 80% of that spend was Opus doing tasks that gpt-4o-mini or Haiku would have done just as well.

**The two ideas at the core:**

1. **Session affinity for prompt-cache locality.** Most "key rotation" gateways rotate per-request, which kills Anthropic's prompt cache (the cache is keyed by `(api_key, prefix_hash)`). subclaw pins each worker session to one key for its entire lifetime, so the cache stays warm. Cache hit rates go from ~5% to ~85%. Effective input cost drops 80%+.

2. **Worker swarm with budget circuit breaker.** A slash command decomposes a goal into N briefs. Each brief runs on a cheap model (Haiku) in parallel via an isolated `claude` CLI. The orchestrator (Opus) only reads the final summaries. A per-session + per-day USD cap kills runaway agents before the bill arrives.

**What's interesting technically:**
- Streaming Anthropic↔OpenAI protocol translation (so workers running `claude` can hit OpenAI endpoints transparently).
- Sticky 429 failover — only re-binds a session's key on actual failure, not on a timer.
- Empty-text-block sanitization (some OpenAI-compatible vendors 400 on these).
- One-file Python proxy, ~1300 lines, easy to read and audit.

**The numbers from my own daily use:**
- Audit 50 files (50k tokens): Opus only ~$75 with a 10× loop, subclaw ~$0.11
- Repo-wide grep (200k tokens): Opus only ~$0.68, subclaw ~$0.06
- Monthly: $1,871 → $375

**What it's not:**
- Not a 100-provider protocol router (use LiteLLM for that).
- Not a hosted SaaS (use OpenRouter for that).
- Not a multi-tenant product with auth (use Portkey for that).
- It's specifically built for the "Claude Code + cheap swarm + budget cap" use case.

**Tech stack:** Python 3.9+, FastAPI, httpx. Single binary, no DB, no Redis. `pip install -r requirements.txt && python app.py` and you're up.

**License:** MIT.

Repo: https://github.com/Akichoooo/subclaw
Docs: https://github.com/Akichoooo/subclaw/blob/main/docs/architecture.md

Happy to answer questions about the session-pinning design, the protocol translation, or anything else.
```

---

## Posting tips for HN

1. **Best time to post**: Tuesday-Thursday, 8-10am US Eastern. The morning US crowd + the European afternoon crowd = peak eyeballs.
2. **First 2 hours are critical.** Reply to every comment quickly, even critical ones. Be technical and humble.
3. **Don't link your Twitter / Discord / "join our community"** in the first post. HN downvotes that.
4. **If you get a "Show HN" rule violation warning from mods, take it seriously.** Rephrase to be less promotional, less "I made this", more "here's the technical idea". They often un-flag if you do.
5. **Cross-post to lobste.rs** a day or two later, with a slightly more technical / less marketing-y tone.

## After posting

- Add a row to your tracking spreadsheet: `HN | 2025-01-15 | <points> points | <comment count> comments | notes`
- Reply to top-voted comments within 24h.
- Don't edit the original post unless a mod tells you to.

## Variations

If you want to post to lobste.rs, the body should be more technical and less "look at my numbers":

```
subclaw is a self-hosted FastAPI proxy + `/subclaw` slash command for Claude Code.
The interesting bit is session-pinned key rotation for prompt-cache locality:
sticky-by-default, rotate-only-on-429. Most LLM gateways do per-request rotation,
which kills Anthropic's prefix-keyed prompt cache (cache hit rate ~5%, effective
input cost $3/1M). subclaw pins each session to one key for its lifetime, so
cache hit rate goes to ~85% and effective input cost drops to ~$0.60/1M.
Combined with a worker swarm (cheap model drafts in parallel, expensive model
audits the final summary) and a USD budget circuit breaker, my monthly Claude
bill went from $1,871 to $375. Repo: https://github.com/Akichoooo/subclaw
```
