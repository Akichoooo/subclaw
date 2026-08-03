---
name: subclaw
description: Use when WorkBuddy should delegate repo scans, reviews, drafting, or peer-review passes to cheap worker models through Claw Proxy. WorkBuddy orchestrates; external CLI engines (kimi/codex/claude) run as workers with live status echo.
---

# subclaw (WorkBuddy branch)

WorkBuddy is the orchestrator. WorkBuddy has NO headless worker CLI of its own, so worker pools are driven through the OTHER engines' runners (process management, which is legal for any orchestrator). Claw Proxy serves all engines from the same model/key pool.

## ROUTING RULES (obey first, before anything else)

1. Current orchestrator engine = **workbuddy**. The delegation paths described in this file are the ONLY legal paths in this engine.
2. NEVER call other engines' NATIVE subagent mechanisms: no Claude Code Task tool / `.claude/agents`, no Codex TOML agents, no Kimi `/swarm` / `--agent-file`. Those only work inside their own engines (known cross-wiring bug class).
3. Spawning external worker PROCESSES is allowed and is the only way WorkBuddy gets workers: prefer the Kimi runner (real-time streaming echo), then Codex, then Claude. Always through the runner scripts below, never hand-writing engine flags.
4. WorkBuddy's own model traffic can additionally flow through Claw Proxy via `models.json` (see below) — that routes WorkBuddy's direct chats, separate from worker pools.
5. If the user asks for another engine's subagent mechanism, explain it is unavailable here and offer the WorkBuddy path (runner dispatch).

## Endpoints

- Proxy: `http://localhost:4748` (dashboard `/ui`, status `/api/status`, discovery `/models`)
- Worker runners (pick ONE per pool; kimi recommended for live echo):
  - Kimi: `%USERPROFILE%\.kimi-code\skills\subclaw\scripts\run_kimi_claw_pool.ps1`
  - Codex: `%USERPROFILE%\.codex\skills\subclaw\scripts\run_codex_claw_pool.ps1`
  - Claude: `bash ~/.claude/scripts/run-claw-pool.sh`
- Status helper: `%USERPROFILE%\.kimi-code\skills\subclaw\scripts\kimi_subclaw_status.ps1` (works for all branches; reads `*.claw.*` / `*.codexclaw.*` / `*.kimiclaw.*` reports)

### One-time: route WorkBuddy's own models through claw-proxy (optional)

Add entries to `~/.workbuddy/models.json` (WorkBuddy uses OpenAI chat-completions wire format with full-path URLs):

```json
{
  "id": "claw-<model-id>",
  "name": "claw-<model-id>",
  "vendor": "Custom",
  "url": "http://127.0.0.1:4748/v1/chat/completions",
  "apiKey": "ck-workbuddy-<your-virtual-key>",
  "supportsToolCall": true,
  "supportsImages": false,
  "supportsReasoning": false
}
```

### One-time: client identity (recommended)

In `proxy/clients.json`, give WorkBuddy its own virtual key `ck-workbuddy-*` and use it as `apiKey` above. This enables per-agent spend attribution and optional daily budget caps in claw-proxy (`/api/status` shows them).

## Workflow

1. Check capacity before dispatch:

```powershell
curl.exe -sS http://127.0.0.1:4748/api/status
curl.exe -sS http://127.0.0.1:4748/models
```

2. Pick model and concurrency (do not exceed the model `key_count` from `/models`).

3. Create brief files: `<workdir>/.ai_agents/subclaw-briefs/*.md`. Every brief requires this evidence protocol:

```markdown
[PROGRESS] <short current step>
[EVIDENCE] <file>:<line> - <fact>
[CLAIM] <conclusion> | evidence: <file:line list> | confidence: high|medium|low
[RISK] <what could be wrong or needs verification>
[ASK_ORCHESTRATOR] <specific question> only if blocked
[WORKER_DONE] status: OK|PARTIAL|FAIL
```

Communication budget: max 50 `[PROGRESS]` lines per worker; evidence packets <= 2K tokens; full dumps stay on disk, cited by path.

4. Dispatch workers (PowerShell; kimi engine example — live streaming echo):

```powershell
& "$env:USERPROFILE\.kimi-code\skills\subclaw\scripts\run_kimi_claw_pool.ps1" `
  -Workdir "<abs-workdir>" -BriefDir "<abs-brief-dir>" -Model "<model-id>" -Jobs 2
```

While a pool runs, monitor live (separate command):

```powershell
& "$env:USERPROFILE\.kimi-code\skills\subclaw\scripts\kimi_subclaw_status.ps1" `
  -ProxyUrl "http://127.0.0.1:4748" -ReportsDir "<reports-dir>" -Watch -IntervalSec 5 -MaxSeconds 120
```

5. Read results: each task produces `<outdir>/<base>.<engine>claw.<stamp>.md` with `[META]/[OUTPUT]/[EXIT]` sections; final runner line is `POOL_DONE OUTDIR=... STAMP=...`. Read only the `[OUTPUT]` bodies.

6. Peer review for non-trivial tasks: Round A workers produce evidence packets; Round B reviewer workers read only Round A reports (anonymized when conclusions conflict); WorkBuddy reads Round B first and verifies only disputed/high-impact/low-confidence items.

7. Audit checklist (mandatory, objective checks only):

- Every `[CLAIM]` cites an `[EVIDENCE]` that exists (spot-check 2-3 paths).
- All cited paths inside Workdir.
- `[RISK]` items are either addressed or escalated — never silently dropped.
- Red flags >= 2 -> rerun with a tighter brief instead of re-verifying by hand.

## Safety

- Default workers are read-only; never enable write/bash modes without explicit user request.
- Never print full API keys; status output shows suffix only.
- If proxy is down, start it from the subclaw repository root with `docker compose up -d`, or run `python proxy/app.py` after installing requirements.
- Workers advise; WorkBuddy decides and applies final edits.
