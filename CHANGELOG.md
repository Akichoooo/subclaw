# Changelog

All notable changes to subclaw are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
