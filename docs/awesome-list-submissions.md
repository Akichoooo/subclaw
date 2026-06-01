# Awesome-list Submissions

This page is a checklist for getting `subclaw` listed in the awesome-* repositories that matter for AI / LLM gateway discoverability. Each entry below is a target list, a suggested placement section, and a copy-paste PR description you can use.

> When you submit a PR, be polite, follow the contributor guidelines, and don't ping the maintainer. Most awesome-* lists get a few PRs a week; spam-pinging gets you ignored.

---

## 1. [awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code)

**Why**: this is the canonical list of Claude Code tools, plugins, and patterns. If `subclaw` is anywhere, it should be here.

**Suggested section**: *Routers and gateways* (or create one if it doesn't exist).

**PR title**: `Add subclaw — multi-model gateway with session-pinned prompt cache`

**PR body**:
```markdown
Adding **subclaw** under *Routers and gateways*.

- One-line description: FastAPI multi-model gateway for Claude Code with session-pinned prompt-cache locality, Anthropic↔OpenAI protocol translation, and budget circuit breaker.
- Why it belongs here: it's the only tool in this list that is purpose-built for slashing Claude API cost via worker swarms while keeping the prompt cache warm.
- Link: https://github.com/Akichoooo/subclaw
- License: MIT
```

---

## 2. [awesome-llm](https://github.com/Hannibal046/Awesome-LLM)

**Why**: the most-starred general "awesome LLM" list. Covers frameworks, training, serving, applications.

**Suggested section**: *Agent frameworks* or *LLM serving / gateways*.

**PR title**: `Add subclaw — self-hosted multi-model LLM gateway with prompt-cache locality`

**PR body**:
```markdown
Adding **subclaw** to the *LLM serving / gateways* section.

- Self-hosted FastAPI proxy
- Session-pinned multi-key rotation for prompt cache locality
- Anthropic ↔ OpenAI streaming protocol translation
- Slash command UX for Claude Code
- Per-session / per-day USD budget circuit breaker
- Repo: https://github.com/Akichoooo/subclaw
- License: MIT
```

---

## 3. [awesome-llm-tools](https://github.com/zhanghandong/awesome-llm-tools)

**Why**: focused on the LLM tooling ecosystem, including dev tools, eval, monitoring, gateways.

**Suggested section**: *LLM gateways / routers* or *Developer tools*.

**PR title**: `Add subclaw — slash command + multi-model gateway for cost reduction`

**PR body**:
```markdown
Adding **subclaw** under *LLM gateways / routers*.

A FastAPI proxy + `/subclaw` slash command that:

1. Pins each worker session to one API key → keeps Anthropic's prompt cache warm
2. Translates Anthropic protocol to/from OpenAI protocol (so workers running Claude Code can hit OpenAI endpoints transparently)
3. Has a USD budget circuit breaker (per-session and per-day)
4. Distributes work across N cheap worker models in parallel, with Opus only auditing the final report

Repo: https://github.com/Akichoooo/subclaw
License: MIT
```

---

## 4. [awesome-ai-agents](https://github.com/e2b-dev/awesome-ai-agents) (or similar)

**Why**: a list of agent frameworks and tools. `subclaw` is an orchestration tool, not an agent framework, but the positioning is right.

**Suggested section**: *Agent infrastructure* or *Tooling*.

**PR title**: `Add subclaw — agent gateway for cost-optimized multi-model orchestration`

---

## 5. [awesome-prompt-engineering](https://github.com/snwfdhmp/awesome-prompt-engineering) (if it lists tools)

**Why**: prompt caching is a prompt-engineering technique. If the list has a tools section, `subclaw` qualifies.

---

## 6. [awesome-selfhosted](https://github.com/awesome-selfhosted/awesome-selfhosted)

**Why**: the biggest self-hosted list. Strict quality bar (must be in active development, must have a real user base, etc.). The most prestigious list to get into.

**Suggested section**: *Communication* or *Office Suite* (they don't have a great category for "AI gateway" yet — propose one in the PR).

**PR title**: `Add subclaw — self-hosted multi-model LLM gateway`

**PR body**: follow the awesome-selfhosted [contribution guidelines](https://github.com/awesome-selfhosted/awesome-selfhosted/blob/master/CONTRIBUTING.md) carefully. They are strict about: active maintenance, real users, clear license, working install docs.

---

## 7. [awesome-claude](https://github.com/anthropics/awesome-claude) (if it exists) or related

**Why**: Anthropic-affiliated lists rank highest for AI search engines.

---

## Submission cadence

- **Don't submit to all 7 in one day.** Stagger by 2-3 days each. Looks less spammy.
- **Wait for any merge before submitting to the next.** Rejected PRs often cite "already submitted to similar list" — make sure each merge lands first.
- **Update each list's entry when you release a new version.** A maintained entry = higher placement.

---

## Reddit / HN / dev.to

Reddit and HN traffic is also high-intent. See:

- [`docs/show-hn-post.md`](show-hn-post.md) — copy-paste Show HN text
- [`docs/blog-post.md`](blog-post.md) — copy-paste dev.to / hashnode post

---

## Track results

Add a row to your own spreadsheet as you submit:

| List | URL | Submitted | Merged | Notes |
|---|---|---|---|---|
| awesome-claude-code | … | 2025-01-15 | 2025-01-17 | — |
| awesome-llm | … | — | — | — |
| … | | | | |
