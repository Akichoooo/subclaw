---
description: Dispatch scan/review/draft/patch tasks to cheap worker models via the claw-proxy multi-model gateway (saves Claude tokens). Claude is the orchestrator — decides scope, model tier, team size, dispatches, verifies. Generic over models; works in any directory. Usage:/subclaw <inline task> | /subclaw file=<path-to-task.md>
allowed-tools: Bash(powershell*), Bash(bash*), Bash(curl*), Read, Write, Edit, Glob, Grep, TaskCreate, TaskUpdate
argument-hint: "<inline task description> | file=<path-to-task.md>"
author: jy
---
<!-- Author by: jy -->


# /subclaw — Multi-model subagent orchestrator (global)

Goal: **cheap worker models do the heavy reading and drafting; Claude curates the task, picks the right model tier, dispatches a team, then verifies.** Save Claude tokens; keep quality at the Claude-validated bar.

This is a **global** Claude Code slash command — it works in any working directory and is **generic over models**: it discovers what's routable at runtime from the gateway, so it is not tied to any single vendor.

You (Claude) are the **orchestrator / Team Lead**. The user gives you a goal; you decide scope, which model tier per step, how many workers, how they communicate, and you verify the output.

---

## The architecture in one picture

```
        You (Claude)  =  Team Lead / Supervisor
              |
              | decompose goal → N briefs, assign model tier per brief
              v
   ~/.claude/scripts/run-claw-pool.sh   (fans briefs across keys, parallel)
              |
              v
   claw-proxy :4748   (owns key pool, session-pins one key/worker,
              |        translates protocol, fails over on 429)
              v
   Worker models (cheap/balanced/smart) — isolated context each,
   return CONCISE SUMMARIES (not full transcripts) back to you
              |
              v
        You synthesize + audit the N reports → final answer
```

Patterns this skill supports (pick per task, don't run them all):
- **Orchestrator-workers** — decompose at runtime into N independent briefs, fan out, synthesize. (default)
- **Cross-review layering** — cheap model drafts → smarter model audits → Claude finalizes only the disagreements.
- **MoA (Mixture of Agents)** — for a hard ambiguous problem, ask 3 different cheap models the *same* question in parallel; Claude aggregates the cross-agreeing parts.
- **Planner-Generator-Evaluator** — one worker plans, one generates, one critiques; Claude ratifies.

---

## Step −1 — Capability discovery (don't hardcode models)

**Always start by checking gateway capacity, cost, and capabilities.** Do not guess models or concurrency limits. Run this native command to discover what's available:

```bash
bash ~/.claude/scripts/run-claw-pool.sh --info
```

This returns a human-readable list of models, their **Tier**, **Capacity (concurrent workers)**, **Cost**, and **Capabilities** (like tool_use). 
- **CRITICAL RULE**: Do not spawn more concurrent jobs (`-j N`) than the maximum Capacity of the model you select! If the model says "Capacity: 4", your `-j` must be `<= 4`. Proxy queueing will handle minor spikes, but don't overload it intentionally.
- If the command fails, the gateway is down — start it (`python app.py` in the proxy dir).

---

## Step 0 — Pick the model tier by task difficulty (step-level routing)

This is where the savings come from: **route each step to the cheapest model that can do it.** Don't send a classification job to your most expensive worker, and don't send an architecture audit to your cheapest.

| Tier | Use for | Why |
|---|---|---|
| **cheap** (fast/shallow model) | wide scans, search, classification, "find all X", first-pass drafts, translation, summarization | high volume, low logic density — cheapest token rate, accuracy good enough |
| **balanced** (default model) | most review/audit work, doc rewrites, patch proposals | the workhorse; deep enough for cited findings |
| **smart** (the strongest routable worker) | the audit pass *over* a cheap worker's output, ambiguous cross-file reasoning, security-sensitive review | spend the better model only where logic density is high |
| **Claude (you)** | reconcile worker layers, resolve disagreements, apply edits to production code, final judgment | the most expensive — reserve for synthesis & decisions only |

**Map "tier" → real model id** by reading `/models`: the cheapest passthrough model = `cheap`, the `default_model` = `balanced`, the strongest passthrough model = `smart`. Pass the chosen id to the runner with `-m <id>`.

**Step-level routing example** (one goal, three steps, three tiers):
1. *cheap* worker: "list every file that imports `LegacyAuth`" → mechanical scan.
2. *balanced* worker: "for each hit, assess whether the import is dead" → judgment.
3. *smart* worker (or Claude): "audit the 3 ambiguous ones" → high stakes only.

---

## Step 1 — Decide if subclaw is even the right tool

| Signal | Delegate to a worker? |
|---|---|
| Read ≥10 files and write a report | **Yes** |
| Doc rewrite / consolidation / translation | **Yes** |
| "Find unused / redundant / stale X" across a codebase | **Yes** |
| Patch proposal where a unified-diff-in-markdown is OK | **Yes** |
| UI copy / layout / product language review | **Yes** |
| Hard ambiguous bug, multiple plausible root causes | **Yes — MoA mode** (ask 3 cheap models, aggregate) |
| ≤3 file reads, 1 small edit | **No** — just do it in Claude |
| Must produce a final commit on production code | **No** — Claude edits; a worker at most drafts a proposal first |
| Cross-file reasoning Claude already grasped this session | **No** — context transfer would cost more than it saves |
| User explicitly said "use subclaw" / "/subclaw" | **Yes**, even if borderline |

If **No**, do the task yourself and tell the user one sentence why you skipped subclaw. If **Yes**, continue.

---

## Step 2 — Choose the engine + transport

| Engine | When | Script |
|---|---|---|
| **Pool (preferred)** | 1+ tasks, especially anything that shards into independent sub-jobs. Drives workers through the `claude` CLI → native UTF-8 (no GBK mojibake / "Failed to parse JSON"), multi-key parallel. | `bash ~/.claude/scripts/run-claw-pool.sh` |

### Transport: claw-proxy (default) vs --direct
By default the pool points at **claw-proxy** (`http://localhost:4748`), which:
- owns the key pool (`keys.json`) — workers carry **no real key**,
- pins one key per worker via the `x-session-id` header the runner injects → **prompt-cache locality** (cache-hit input is ~50× cheaper than a miss),
- transparently **fails over to backup models on 500/429** and rotates keys,
- enforces **spend limits (Circuit Breaker)** to protect your wallet against runaway swarms,
- translates protocol so Anthropic-protocol passthrough preserves `tool_use` (workers keep Read/Glob/Grep).

Start the proxy first (`python app.py` in the proxy dir, default `D:\Docker Project\ai-proxy\claw-proxy`). If it's down, pass `--direct` to fall back to per-worker keys from `~/.claude/scripts/claw-keys.tsv`.

### Pool dispatch
```bash
# read-only audit / scan (SAFE DEFAULT: Read,Glob,Grep, no shell, no edits)
bash ~/.claude/scripts/run-claw-pool.sh -j 5 -T 900 --budget 1.0 -w <abs-workdir> -d <dir-of-task.md>
# or pass briefs positionally:
bash ~/.claude/scripts/run-claw-pool.sh -j 5 -w <abs-workdir> brief1.md brief2.md ...
```
Flags: `-m <model-id>` (tier selection — from `/models`), `--budget <usd>` (max spend per session), `--direct` (bypass proxy), `--proxy-url URL`, `--bash` (workers may run shell), `--write` (shell+Edit+Write).

**SAFETY — read before `--bash`/`--write`:** default is read-only on purpose. `--bash`/`--write` give *every* parallel worker autonomous shell/edit power with `bypassPermissions` — use only on trusted tasks in a known-good workdir, and prefer having the worker emit a **proposal** that Claude applies (Step 8) over letting workers mutate production code directly.

**Reading pool output:** each task → `<outdir>/<base>.ccmimo.<stamp>.md` with `[META]/[OUTPUT]/[EXIT]` sections. Outdir = `<workdir>/.ai_agents/reports` if present else `./mimo-reports`. Final stdout line: `POOL_DONE OK=<n> FAIL=<n> TIMEOUT=<n>`. Read only the `[OUTPUT]` body per report, then audit per Step 6.

---

## Step 3 — Decide team shape (dynamic teaming)

Don't fix the team size — **let the task decide N.** As Team Lead you choose:

| Goal shape | Team | N |
|---|---|---|
| Single cohesive scan | one worker | 1 |
| Goal shards by area ("backend + dll + frontend + docs") | one worker per shard, parallel | = number of shards |
| Review 12 files | group by area into a few briefs | 2–4 |
| Hard ambiguous bug | **MoA**: same question to 3 different cheap models | 3 |
| Draft → audit pipeline | cheap drafts, smart audits same artifact | 2 (sequential) |

**Sweet spot is 2–4 workers.** More than that, coordination + synthesis cost grows and you lose the savings. One giant serial brief is usually worse than 3 focused ones: slower, and easier for a worker to lose the thread.

**Token economics of teaming:** each worker has an isolated context (no shared history), so N workers ≈ N× the worker-side tokens — but worker tokens are cheap and *parallel*. The expensive resource is *your* context. So the rule is: **workers return concise summaries, not transcripts** (Step 5), and you only read the `[OUTPUT]` bodies.

---

## Step 4 — Agent-to-agent communication (when workers must coordinate)

Workers get **isolated context** — they can't see each other by default. When a task genuinely needs coordination (e.g. one worker's finding feeds another's audit), use a **shared directory** as the mailbox, not Claude's context:

- Create `<workdir>/.ai_agents/shared/` and tell each brief to **write its findings there** and **read peers' findings from there**.
- Pattern: worker A writes `shared/findings-backend.md`; worker B's brief says "read `shared/findings-backend.md` before auditing the API contract."
- **Challenge/critique:** give a second worker the first's output path and the instruction "find what's wrong with this — cite file:line." Disagreement between workers is signal: it's exactly where *you* spend verification (Step 6).

Keep coordination shallow. Deep multi-hop agent chatter burns tokens and drifts. Two hops (produce → critique) is usually the max worth orchestrating; beyond that, synthesize yourself.

---

## Step 5 — Control what comes back (return_format)

**The #1 token-waste failure: a worker dumps thousands of lines of code/logs back as its result.** Pin the return shape in every brief:

| return_format | Use when | Worker returns |
|---|---|---|
| `concise_summary` (default) | refactors, multi-file edits, scans | "Done: refactored utils.py, 3 funcs extracted, tests pass" + file:line citations — **not the code** |
| `full_diff` | you'll apply the patch | a unified diff in markdown, ≤N lines |
| `findings_list` | audits | one line per finding, each citing file:line + a [safe]/[needs-confirmation]/[do-not-delete] tag |
| `boolean` | a yes/no gate | just `true`/`false` + one sentence |

If a worker **edits files** (with `--write`), it must write the changes via its own file tools and return only a `concise_summary` — never echo the whole file back. That's the difference between saving tokens and triggering a token avalanche.

---

## Step 6 — Curate context for each brief (the Claude value-add)

Each worker gets a **fresh, isolated context** — no shared session memory. Give the **minimum** that lets it succeed.

### Budget rules
1. **Cheaper to point than to paste.** Background in a file → give the *path*, not contents (the worker's own Read runs at its cheap input rate).
2. **Inline only what's small and load-bearing.** API contracts, schemas, exact signatures (≤20 lines). Everything else → path.
3. **State the negative space.** Tell it what to ignore (`node_modules`, `.next`, `.venv`, build artifacts, vendored code).
4. **Carry forward prior report paths.** Continuing earlier work? Write `Prior report: <path>` — don't paraphrase.

### Brief skeleton
```markdown
# <Title>

## Goal
<one short paragraph — why this matters>

## Model tier
<cheap | balanced | smart>  (Claude maps to a real -m id from /models)

## Scope
- Focus: <path glob 1>
- Focus: <path glob 2>

## Out of scope
- node_modules, .next, .venv, build artifacts, vendored code
- <anything else irrelevant>

## Anchors (read these first to orient)
- <path/to/key/file>

## Constraints
- <invariant or rule the worker must respect>

## Shared dir (if coordinating)
- Write findings to .ai_agents/shared/<name>.md
- Read peer findings from .ai_agents/shared/<peer>.md

## Prior report (if continuing)
- <path>

## Output contract
- return_format: <concise_summary | full_diff | findings_list | boolean>
- Each finding cites file:line.
- End with "Recommendations" — each tagged [safe]/[needs-confirmation]/[do-not-delete]/[needs-tests].
- Total length ≤ <N> words.

## Progress reporting protocol (CRITICAL)
**You MUST report progress and completion actively:**

1. **Progress heartbeat** — Every time you complete a major step, output:
   ```
   [PROGRESS] <step-name> - <completed/total>
   ```
   Example: `[PROGRESS] Scanning backend files - 45/120`

2. **Completion marker** — When you finish the task, output:
   ```
   [WORKER_DONE]
   status: <OK|PARTIAL|FAIL>
   summary: <one-sentence summary of what you accomplished>
   evidence: <key file:line citations proving your findings>
   [/WORKER_DONE]
   ```
   
   - `OK`: task fully completed
   - `PARTIAL`: made progress but hit blockers (explain in summary)
   - `FAIL`: could not complete (explain why)

3. **Why this matters**: The orchestrator (main Claude) monitors these markers in real-time. Without them, your work may be killed by timeout even if you finished early, or the orchestrator won't know you're stuck and need help.

**Example output:**
```
Reading project structure...
[PROGRESS] Initial scan - 1/3

Found 45 Java files in backend/src/
[PROGRESS] File enumeration - 2/3

Analyzing dependencies...
[PROGRESS] Dependency analysis - 3/3

[WORKER_DONE]
status: OK
summary: Found 12 unused imports across 8 files, all safe to remove
evidence: backend/src/main/java/com/sentinel/service/AuthService.java:15, CompileService.java:23, RateLimitService.java:8
[/WORKER_DONE]
```
```

Show the user the **brief path** in your reply, not the brief content.

---

## Step 7 — Audit the workers' output (quality gate — Claude value-add)

Read only the `[OUTPUT]` body of each report. Apply this checklist:

### Mandatory checks (all must pass)
1. **Format compliance** — output matches the `return_format` you asked for?
2. **Path reality** — spot-check 2–3 cited paths with Glob. Cited path doesn't exist → red flag.
3. **Symbol reality** — Grep one or two cited function/class names. Hallucinated symbol → red flag.
4. **Scope compliance** — all cited paths inside `WorkingDir`? Outside → critical.
5. **No silent skips** — a focus glob named but never appearing → flag it.

### Where MoA / cross-review pays off
When you ran multiple workers on overlapping ground: **where they agree, trust it — don't re-audit.** Where they **disagree** is exactly where you spend your verification budget. That's the whole point of model diversity: cheap workers surface the candidates, you adjudicate only the contested ones.

### Severity
- **0 red flags** → trust it.
- **1 red flag** → fix-in-place: re-Grep/re-Read just the suspicious item.
- **≥2 red flags** → rerun with a tighter brief (narrower scope, more anchors), or escalate to Claude (do it directly).

### Hard rule
If you re-read more than 2 source files Claude-side to verify a worker, **stop**: the brief was too vague (rerun tighter) or the task wasn't right for subclaw. Don't burn the savings re-validating.

---

## Step 8 — Reply to user

```
✅ /subclaw done — <slug>
team:    <N workers, tiers used>
reports: <outdir>
status:  OK=<n> FAIL=<n> TIMEOUT=<n>
audit:   <pass | 1 flag fixed | rerun tighter>

summary (≤3 bullets):
  - <bullet>
  - <bullet>

next: <one concrete suggestion — apply / refine / accept / escalate>
```

Do NOT paste full report bodies. The user opens the files for detail. **If your reply re-summarizes the whole report, you wasted the savings** — keep it ≤500 tokens.

---

## Step 9 — Apply step (only if user asks)

If user says "apply" / "patch it" / "go": **Claude (not the worker) makes the edits.** The worker's job stops at proposal. Apply with Edit/Write, run lint/typecheck, then report. If the proposal touches files outside the original scope, refuse silently and ask the user first.

---

## Failure modes you'll actually hit

| Symptom | Likely cause | Fix |
|---|---|---|
| `/models` unreachable | proxy down | start `python app.py`, or `--direct` |
| STATUS=EMPTY | bootstrap prompt malformed / auth | read `[STDERR_TAIL]`; don't retry blindly |
| STATUS=TIMEOUT | brief too broad | split into narrower briefs, rerun |
| Hallucinated paths | brief too vague | add explicit `## Anchors` + tighter `## Scope` |
| Worker dumped whole file as result | missing `return_format` | pin `concise_summary`, rerun |
| All workers agree but wrong | over-trusted agreement on a vague brief | the agreement was on the wrong frame — re-anchor and rerun |
| Model not found | stale model id | re-read `/models`; never hardcode |

---

## 💰 Token economics — why we route through cheap workers

> **Worker models** (cheap/balanced tier, via claw-proxy)
> typically **15–30× cheaper** than Claude Opus per token, with cache-hit input cheaper still.
>
> **Claude Opus 4.x**
> in: ~$15/1M · out: ~$75/1M — reserve for **synthesis and decisions only**.

**Savings target:** heavy reading on cheap workers, Claude reply ≤500 tokens. Step-level routing (cheap for scan/classify, smart only for the audit pass) is where the 40–80% savings live. If your Claude reply re-summarizes the whole worker report → you wasted the savings.
