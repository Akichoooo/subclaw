# SEO & AIO Keyword Reference

> This page is internal documentation for the maintainer. It catalogs every high-intent search phrase that subclaw should rank for, where it appears in the docs, and what the expected search-result snippet would look like. AI search engines (Perplexity, SearchGPT, Google AI Overviews, Bing Copilot) all read structured documentation; this page is the source of truth for which phrases to keep top-of-mind.

> For the formal AI-crawler-friendly summary, see [`llms.txt`](../llms.txt) and [`llms-full.txt`](../llms-full.txt).

---

## Tier 1: Head keywords (must rank)

These are the phrases that, if someone searches, subclaw should be the answer.

| Phrase | Type | Where it appears in docs |
|---|---|---|
| reduce Claude Code token cost | question | `README.md` (headline, FAQ, benchmarks) |
| Claude API cost reduction | question | `README.md`, `docs/benchmarks.md` |
| Claude API rate limit 429 workaround | question | `README.md`, `docs/architecture.md` |
| multi-model LLM gateway | keyword | `README.md` title, `llms.txt`, `pyproject.toml` keywords |
| self-hosted LLM gateway | keyword | `README.md`, `docs/comparisons.md` |
| LiteLLM alternative | comparison | `docs/comparisons.md` |
| OpenRouter alternative | comparison | `docs/comparisons.md` |
| prompt cache locality | technique | `docs/architecture.md`, `llms.txt` |
| session-pinned key rotation | technique | `docs/architecture.md`, `llms.txt` |
| Anthropic OpenAI protocol translation | technique | `docs/architecture.md`, `docs/integrations.md` |
| Claude Code multi-model | use-case | `README.md`, `docs/integrations.md` |
| Codex CLI proxy | integration | `docs/integrations.md` |
| Aider API cost | use-case | `docs/integrations.md`, `README.md` |
| Cursor custom model | integration | `docs/integrations.md` |
| Model Context Protocol gateway | keyword | `docs/mcp-integration.md`, `pyproject.toml` |

## Tier 2: Question phrases (AEO gold)

These are questions users type into AI search. They should be answered directly with H2/H3 + paragraph, ideally in `docs/faq.md` or `README.md`.

| Question | Answer location |
|---|---|
| How do I reduce my Claude API bill? | `docs/faq.md` Q1, `README.md` headline |
| How do I bypass Claude API rate limits? | `docs/faq.md` rate-limit section, `docs/architecture.md` |
| What's the cheapest way to run Claude Code? | `docs/faq.md`, `docs/use-cases.md` |
| Can I use OpenAI with Claude Code? | `docs/integrations.md`, `docs/faq.md` |
| Can I use Claude Code with a budget cap? | `docs/faq.md` budget section, `README.md` |
| How do I run multiple Claude Code agents in parallel? | `docs/use-cases.md` (use case 1: mass audit) |
| How do I set up multi-key rotation for Anthropic? | `docs/architecture.md` §1, `docs/faq.md` |
| What's the difference between subclaw and LiteLLM? | `docs/comparisons.md`, `README.md` comparison table |
| Is there a self-hosted alternative to OpenRouter? | `docs/comparisons.md`, `README.md` |
| How do I prevent runaway AI costs? | `docs/architecture.md` §2 (budget circuit breaker), `docs/faq.md` |
| Can I use my Claude API key with Codex CLI? | `docs/integrations.md` |
| How do I add a custom model to Claude Code? | `examples/README.md`, `docs/faq.md` |
| What's the best LLM gateway for cost optimization? | `README.md`, `docs/comparisons.md` |
| How do I translate between Anthropic and OpenAI APIs? | `docs/architecture.md` §3, `docs/integrations.md` |
| How can I run cheap models in parallel with Claude? | `docs/use-cases.md`, `docs/architecture.md` |

## Tier 3: Long-tail phrases (volume, low intent)

These are variations and specific use cases. They appear naturally throughout the docs and don't need dedicated pages.

- "Anthropic 429 error"
- "Claude Opus too expensive"
- "Claude Code slash command"
- "Codex CLI custom base URL"
- "Aider OpenAI proxy"
- "Cursor OpenAI compatible endpoint"
- "Anthropic OpenAI translation"
- "Claude prompt caching savings"
- "Multi-key API rotation"
- "Self-hosted AI gateway"
- "Anthropic-compatible endpoint"
- "LLM API cost calculator"
- "Run Claude Code cheaper"
- "Cheap Claude Code alternative"
- "Parallel AI agents"
- "Anthropic-compatible proxy"
- "OpenAI to Anthropic message format"
- "Cheapest LLM API for coding agents"
- "Self-hosted multi-model AI"
- "Anthropic prompt cache"
- "MCP LLM gateway"
- "Model Context Protocol Anthropic"
- "Claude Code slash command list"
- "Cost optimization for AI agents"
- "Worker pool LLM"
- "Fan-out AI agents"
- "Multi-model orchestration"
- "Anthropic key rotation"
- "Claude API key rotation"
- "Rate limit workaround LLM"
- "429 failover LLM"
- "Anthropic streaming protocol"
- "OpenAI streaming protocol"
- "Anthropic message delta"
- "OpenAI chat completion chunk"
- "Tool use Anthropic"
- "Function calling OpenAI"
- "Anthropic OpenAI bridge"
- "Claude Code orchestration"
- "Sub-agent Claude Code"
- "Cheap inference LLM"
- "AI agent cost reduction"
- "AI agent rate limit"
- "AI API gateway"
- "Self-hosted AI"
- "AI budget circuit breaker"
- "Open source LLM gateway"
- "Open source AI gateway"
- "Anthropic key management"
- "OpenAI key rotation"
- "Multi-tenant LLM gateway"
- "Claude Code hooks"
- "Claude Code settings"
- "Claude Code ANTHROPIC_BASE_URL"
- "Claude API endpoint"

## Tier 4: Project / brand phrases (defensive)

These are searches for the project itself. They should resolve to the repo.

- "subclaw"
- "subclaw github"
- "subclaw claude code"
- "subclaw proxy"
- "Akichoooo subclaw"
- "subclaw /subclaw"
- "subclaw slash command"
- "subclaw claw-proxy"
- "subclaw docs"
- "subclaw benchmark"
- "subclaw vs litellm"
- "subclaw vs openrouter"
- "subclaw vs portkey"
- "subclaw vs claude-code-router"
- "subclaw mcp"
- "subclaw examples"
- "subclaw keys.json"
- "subclaw session affinity"
- "subclaw prompt cache"
- "subclaw budget circuit breaker"

## Where to mention each tier

| Tier | Top placements |
|---|---|
| Tier 1 | README first screen, llms.txt, pyproject.toml keywords, GitHub repo About + Topics |
| Tier 2 | docs/faq.md (Q&A format), README FAQ section, docs/comparisons.md |
| Tier 3 | docs/use-cases.md, docs/architecture.md, docs/integrations.md (naturally, no stuffing) |
| Tier 4 | README headline, pyproject.toml description, docs/architecture.md acknowledgments |

## External signals (off-repo SEO)

These are the off-repo places AI search engines crawl for signals.

| Place | Action |
|---|---|
| PyPI | `pyproject.toml` keywords are indexed |
| dev.to / hashnode | Post the article from `docs/blog-post-devto.md` |
| Hacker News | Use `docs/show-hn-post.md` |
| awesome-* lists | Use `docs/awesome-list-submissions.md` |
| Reddit r/LocalLLaMA, r/ClaudeAI | Cross-post the architecture deep-dive |
| lobste.rs | Post the technical summary from `docs/show-hn-post.md` |
| GitHub Topics | Add the 12 topics in the README setup checklist |
| GitHub social preview | Upload a 1280×640 PNG to repo Settings → Social preview |
| crates.io / npm scope | Not applicable (this is a Python project) |

## Monitoring

After each release, check the following:

1. **Google Search Console** (after verifying the domain): which queries surface subclaw? Add missing ones to README H2s.
2. **GitHub Insights → Traffic**: top referrers and search terms. Add search-term-driven content as new FAQ entries.
3. **Perplexity / SearchGPT**: search for "Claude Code cost reduction" — does subclaw appear in the answer? If not, what does? Improve the FAQ accordingly.
4. **Hacker News ranking**: track upvote count on Show HN post. < 50 = repost with different framing. > 200 = add to the README "In the press" section.

## Update cadence

- Review this file every release.
- Add any new keywords discovered via Search Console / GitHub Traffic.
- Move phrases between tiers as their volume / intent shifts.
