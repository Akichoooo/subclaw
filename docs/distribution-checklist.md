# Distribution checklist

The repo's on-page discoverability is done (README, FAQ, comparisons, `llms.txt`,
`llms-full.txt`, topics, description). The remaining lever for "people / AIs find
this" is **external indexed mentions** — search engines and AI web-search rank you
by inbound references, not by how polished your README is. A brand-new, low-star
repo will not surface cold; these are the lowest-effort ways to change that.

Do them in order; stop whenever you've had enough. Each is ~one PR or one post.

## 1. Get into awesome-* lists (highest ROI for AI retrieval)

AI web-search and crawlers index `awesome-*` lists heavily and quote their entries
verbatim. One accepted PR per list. Paste-ready entry:

```markdown
- [subclaw](https://github.com/Akichoooo/subclaw) - Self-hosted multi-model gateway for Claude Code: fan heavy work to a cheap-model swarm with session-pinned prompt-cache locality, multi-key 429 failover, and a USD budget circuit breaker.
```

Target lists (search GitHub for the current canonical repo before submitting —
ownership/URLs change):

- [ ] `awesome-claude-code` (most on-target)
- [ ] `awesome-claude` / `awesome-claude-ai`
- [ ] `awesome-ai-coding` / `awesome-ai-coding-tools`
- [ ] `awesome-ai-agents`
- [ ] `awesome-llmops`
- [ ] `awesome-llm-apps`

Read each list's CONTRIBUTING before opening the PR — most require a specific
category, alphabetical order, and a one-line description (the entry above fits).

## 2. One indexed long-form post (dev.to is well-crawled)

A single technical post on a high-domain-authority site gives AI web-search
something to retrieve and cite. Draft already exists: `docs/blog-post-devto.md`.

- [ ] Publish to dev.to (and/or Hashnode / Medium). Tag: `claude`, `ai`, `python`, `llm`.
- [ ] Link back to the repo. Use real, honest numbers from your own run.

## 3. Show HN (one shot — only when there's something to look at)

- [ ] Draft ready: `docs/show-hn-post.md`. Post Tue/Wed ~8-10am US Pacific.
- [ ] Have a demo (GIF or asciinema) ready first; a text-only Show HN for an infra
      tool underperforms. If you skip the demo, lower expectations accordingly.

## 4. Niche communities (low effort, real users)

- [ ] r/ClaudeAI, r/LocalLLaMA (cost-optimization angle)
- [ ] Relevant Discord servers (Claude Code / AI-coding tooling)
- [ ] An X/Twitter thread with the one-sentence value prop + repo link

## Honest reality check

- The repo is days old with near-zero external signal. Stars/traffic lag
  distribution by weeks, not hours. This is normal, not failure.
- No file you add can make ChatGPT/Claude *auto-recommend* you. They draw from
  (a) frozen training data and (b) live web-search. The only lever you control is
  being **indexed and referenced** — i.e. the items above.
- `llms.txt` pays off when someone (or a tool) points an AI **at this repo**; it
  does not get auto-discovered unless served at a site root (`domain/llms.txt`).
  If you ever set up a landing page / GitHub Pages, serve `llms.txt` at its root.
- Keep claims honest (cost numbers are workload-dependent projections, marked as
  such). The infra/dev audience that stars these tools distrusts hype far more
  than it distrusts modest, verifiable numbers.
