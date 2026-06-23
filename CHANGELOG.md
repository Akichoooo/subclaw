# Changelog

All notable changes to subclaw are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Independent judge gate: orchestrator writes acceptance criteria (Step 0.5) and dispatches a read-only smart-tier judge worker (Step 7.5) that returns TRUE/PARTIAL/FALSE, so the orchestrator is no longer the sole decider of "done". Review loop hard-capped at 3 rounds; escalates to the human past the cap.
- Per-brief permission frontmatter: a brief may declare `tools:` and `permission:` in a YAML-ish frontmatter block to override the global `--tools`/`--perm`/`--bash`/`--write` for that one worker (e.g. pin a judge to `tools: Read,Glob,Grep`). Supported in both `run-claw-pool.sh` (Claude) and `run_codex_claw_pool.ps1` (Codex, mapped to `--sandbox`). Unknown tool names are dropped with a warning; the block is stripped before the worker sees the prompt.
- `GET /orchestration` endpoint on the proxy: read-only view of orchestrator task tree, worker statuses, judge verdicts + round counter, and shared mailbox. Surfaced as an "Orchestration" block on the dashboard. Configured via `ORCH_REPORTS_DIR` (path-traversal guarded). Also surfaced in `codex_subclaw_status.ps1`.
- Comprehensive README with AI-search-optimized keywords and FAQ
- Chinese documentation (`README_zh.md`) with full feature parity
- Architecture deep-dive in `docs/architecture.md`
- Competitor comparisons (LiteLLM, OpenRouter, Portkey, claude-code-router)
- Benchmark and use-case documentation
- GitHub community templates (issue, PR, code of conduct, security)
- LICENSE file (MIT)
- Contributing guide

## [0.1.0] - 2025-XX-XX

### Added
- Initial release of `claw-proxy` (FastAPI multi-model gateway)
- Session-pinned multi-key rotation with prompt cache locality
- Anthropic ↔ OpenAI streaming protocol translation
- Budget circuit breaker (per-session and per-day USD caps)
- 429 rate-limit failover with automatic key rebinding
- `/subclaw` slash command for Claude Code
- `run-claw-pool.sh` worker pool driver (proxy + direct modes)
- Live tree UI for worker status monitoring
- `/stats` and `/dashboard` observability endpoints
- `keys.example.json` configuration template

[Unreleased]: https://github.com/Akichoooo/subclaw/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Akichoooo/subclaw/releases/tag/v0.1.0
