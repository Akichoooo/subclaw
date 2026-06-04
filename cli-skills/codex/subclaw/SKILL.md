---
name: subclaw
description: Use when Codex should delegate repo scans, reviews, drafting, or peer-review passes to cheap worker models through Claw Proxy, while showing worker/proxy status in Codex CLI.
---

# subclaw

Codex is the orchestrator. Claw Proxy can serve both Claude CLI workers and Codex CLI workers from the same model/key pool.

- Proxy: `http://localhost:4748`
- Claude worker runner: `~/.claude/scripts/run-claw-pool.sh`
- Codex worker runner: `scripts/run_codex_claw_pool.ps1`
- Status helper: `scripts/codex_subclaw_status.ps1`

## URL Compatibility

| Client | Base URL | Wire/API |
|---|---|---|
| Claude CLI workers | `http://localhost:4748` | Anthropic Messages `/v1/messages` |
| Codex CLI workers | `http://localhost:4748/v1` | OpenAI Responses `/v1/responses`, `wire_api="responses"` |
| Browser dashboard | `http://localhost:4748/ui` | proxy status UI |

Both worker types read model/key capacity from:

```text
http://localhost:4748/models
http://localhost:4748/api/status
```

## Workflow

1. Check capacity before dispatch:

```powershell
bash ~/.claude/scripts/run-claw-pool.sh --info
curl.exe -sS http://127.0.0.1:4748/api/status
curl.exe -sS http://127.0.0.1:4748/models
```

2. Pick model and concurrency.

- Use a cheap tier model for wide scans, classification, first-pass summaries.
- Use a smart tier model for harder review/audit passes.
- Do not set `-j` above the model `key_count`/capacity reported by `/models` unless the user accepts queueing.

3. Create brief files in the current repo, preferably:

```text
<workdir>/.ai_agents/subclaw-briefs/
```

Every brief should require this evidence protocol:

```markdown
[PROGRESS] <short current step>
[EVIDENCE] <file>:<line> - <fact>
[CLAIM] <conclusion> | evidence: <file:line list> | confidence: high|medium|low
[RISK] <what could be wrong or needs verification>
[ASK_ORCHESTRATOR] <specific question> only if blocked
[WORKER_DONE] status: OK|PARTIAL|FAIL
```

4. Dispatch workers.

Claude worker engine:

```powershell
bash ~/.claude/scripts/run-claw-pool.sh -j 2 -m <model-id> -w "<abs-workdir>" -d "<abs-brief-dir>" --no-tree
```

Codex worker engine:

```powershell
& "$env:USERPROFILE\.codex\skills\subclaw\scripts\run_codex_claw_pool.ps1" `
  -Workdir "<abs-workdir>" -BriefDir "<abs-brief-dir>" -Model "<model-id>" -Jobs 2
```

Use Claude engine for the established `/subclaw` pool behavior. Use Codex engine when Codex CLI workers should call Claw Proxy through the Responses API.

5. Show live status inside Codex CLI.

```powershell
& "$env:USERPROFILE\.codex\skills\subclaw\scripts\codex_subclaw_status.ps1" `
  -ProxyUrl "http://127.0.0.1:4748" -ReportsDir "<reports-dir>"
```

Watch mode:

```powershell
& "$env:USERPROFILE\.codex\skills\subclaw\scripts\codex_subclaw_status.ps1" `
  -ProxyUrl "http://127.0.0.1:4748" -ReportsDir "<reports-dir>" -Watch -IntervalSec 5 -MaxSeconds 120
```

6. Use peer review for non-trivial tasks.

- Round A workers produce evidence packets.
- Round B reviewer workers read only the Round A reports.
- Codex reads Round B first, then verifies only disputed, high-impact, or low-confidence items.

## Safety

- Default worker tools are read-only.
- Never print full API keys. Status output should show suffix only.
- If proxy is down, start it from the repository root with `docker compose up -d`, or run `python proxy/app.py` after installing requirements.
- Workers advise; Codex decides and applies final edits.
