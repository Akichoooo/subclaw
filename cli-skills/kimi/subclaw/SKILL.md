---
name: subclaw
description: Use when Kimi Code should delegate repo scans, reviews, drafting, or peer-review passes to cheap worker models through Claw Proxy, with live streaming progress echo in Kimi CLI.
---

# subclaw (Kimi Code branch)

Kimi Code is the orchestrator. Claw Proxy can serve Claude CLI, Codex CLI and Kimi Code workers from the same model/key pool.

## ROUTING RULES (obey first, before anything else)

1. Current orchestrator engine = **kimi**. The delegation paths described in this file are the ONLY legal paths in this engine.
2. NEVER call other engines' NATIVE subagent mechanisms: no Claude Code Task tool / `.claude/agents`, no Codex TOML agents (`~/.codex/agents/*.toml`). Using them inside Kimi causes hard errors (known cross-wiring bug class).
3. Spawning any engine CLI as an EXTERNAL worker process (`kimi -p`, `claude -p`, `codex exec`) is allowed — but only through this skill's runner scripts, never by hand-writing engine-specific flags.
4. Kimi-native delegation alternatives (use only inside Kimi): `/coder`, `/explore`, `/plan` builtin subagents, or `/swarm <task>` for lightweight parallel sharding.
5. If the user asks for another engine's subagent mechanism, explain it is unavailable in this engine and offer the Kimi equivalent.

## Endpoints

- Proxy: `http://localhost:4748`
- Kimi worker runner: `scripts/run_kimi_claw_pool.ps1`
- Status helper: `scripts/kimi_subclaw_status.ps1` (same output contract as the codex helper)

| Client | Base URL | Wire/API |
|---|---|---|
| Claude CLI workers | `http://localhost:4748` | Anthropic Messages `/v1/messages` |
| Codex CLI workers | `http://localhost:4748/v1` | OpenAI Responses `/v1/responses` |
| Kimi Code (orchestrator + workers) | `http://127.0.0.1:4748/v1` | OpenAI Chat Completions via `[providers.claw] type="openai"` in config.toml |
| Browser dashboard | `http://localhost:4748/ui` | proxy status UI |

### One-time Kimi config (config.toml)

```toml
[providers.claw]
type = "openai"
base_url = "http://127.0.0.1:4748/v1"
api_key = "proxy-managed"

[models."claw/<model-id>"]
provider = "claw"
model = "<model-id>"
max_context_size = 131072
capabilities = ["tool_use"]
```

WARNING: kimi reads `OPENAI_BASE_URL` / `OPENAI_API_KEY` env vars with HIGHER priority than config.toml. The runner clears them for workers; clear them in your own shell too if the orchestrator mis-routes.

## Workflow

1. Check capacity before dispatch:

```powershell
curl.exe -sS http://127.0.0.1:4748/api/status
curl.exe -sS http://127.0.0.1:4748/models
```

2. Pick model and concurrency.

- Cheap tier for wide scans, classification, first-pass summaries.
- Smart tier for harder review/audit passes.
- Do not set `-Jobs` above the model `key_count` reported by `/models` unless the user accepts queueing.

3. Create brief files in the current repo, preferably:

```text
<workdir>/.ai_agents/subclaw-briefs/
```

Every brief must require this evidence protocol:

```markdown
[PROGRESS] <short current step>
[EVIDENCE] <file>:<line> - <fact>
[CLAIM] <conclusion> | evidence: <file:line list> | confidence: high|medium|low
[RISK] <what could be wrong or needs verification>
[ASK_ORCHESTRATOR] <specific question> only if blocked
[WORKER_DONE] status: OK|PARTIAL|FAIL
```

Communication budget: max 50 `[PROGRESS]` lines per worker; converge to `[WORKER_DONE]` or `[ASK_ORCHESTRATOR]` beyond that. Evidence packets <= 2K tokens; full dumps stay on disk, cited by path.

4. Dispatch workers (Kimi engine — streaming echo built in):

```powershell
& "$env:USERPROFILE\.kimi-code\skills\subclaw\scripts\run_kimi_claw_pool.ps1" `
  -Workdir "<abs-workdir>" -BriefDir "<abs-brief-dir>" -Model "<model-id>" -Jobs 2
```

The runner starts each worker as `kimi -p "<prompt>" --output-format stream-json`, tails the JSONL live and mirrors tool-call events + `[PROGRESS]` markers into `worker_NNN.status.json` in real time (no end-of-run blackout).

5. Show live status inside Kimi CLI:

```powershell
& "$env:USERPROFILE\.kimi-code\skills\subclaw\scripts\kimi_subclaw_status.ps1" `
  -ProxyUrl "http://127.0.0.1:4748" -ReportsDir "<reports-dir>" -Watch -IntervalSec 5 -MaxSeconds 120
```

6. Use peer review for non-trivial tasks.

- Round A workers produce evidence packets.
- Round B reviewer workers read only the Round A reports (anonymized if conclusions conflict).
- Kimi reads Round B first, then verifies only disputed, high-impact, or low-confidence items.

7. Audit checklist (mandatory, objective checks only):

- Every `[CLAIM]` cites an `[EVIDENCE]` that exists (spot-check 2-3 paths).
- All cited paths inside Workdir.
- `[RISK]` items are either addressed or escalated — never silently dropped.
- Red flags >= 2 -> rerun with a tighter brief instead of re-verifying by hand.

## Safety

- Default worker permission is `-p` auto with the brief demanding read-only behavior; do not add write tools unless the user asks.
- Never print full API keys. Status output shows suffix only.
- If proxy is down, start it from the repository root with `docker compose up -d`, or run `python proxy/app.py` after installing requirements.
- Workers advise; Kimi decides and applies final edits.
