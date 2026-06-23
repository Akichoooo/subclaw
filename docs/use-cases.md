# Use Cases

Real workflows where `subclaw` pays for itself. Each use case has: the trigger, the typical prompt, the architecture it uses, and the expected cost saving.

> Read time: ~8 minutes. Skim the bold text if you're in a hurry.

---

## 1. Mass code auditing

**Trigger**: "I just merged 30 PRs and want to spot regressions before pushing."

**Typical prompt**:
```
/subclaw audit the last 24h of merged changes in src/ for:
- unused imports
- functions > 100 lines
- any TODO without an owner
Return file:line for every finding.
```

**Architecture**: orchestrator-workers. The orchestrator (Opus) decomposes the request into 30 briefs (one per file or PR), each handled by a Haiku worker in parallel. Opus aggregates the 30 summaries and writes a single audit report.

**Why subclaw helps**:
- 30 Haiku calls in parallel = 30× faster than 30 sequential Opus calls.
- Each Haiku call is 1-2k tokens, so cost is ~$0.0003 per call × 30 = $0.01.
- Opus only reads the 30 summaries (60k tokens total at most), so its bill is ~$0.20.

**Cost saving**: 90-95% vs. running the same audit on Opus.

---

## 2. Repo-wide search

**Trigger**: "I need to find every place we use `deprecated_api()`."

**Typical prompt**:
```
/subclaw find every caller of deprecated_api() across the whole repo.
Include file path, line number, and the surrounding 5 lines of context.
Skip generated files and node_modules.
```

**Architecture**: orchestrator-workers. Opus decomposes the repo by directory into N briefs (e.g., 20 workers, one per top-level directory), each Haiku worker scans its slice.

**Why subclaw helps**:
- Reading 200k tokens to find a few string matches is the textbook case of "wrong model for the job". Haiku at $0.25/1M input is 12× cheaper than Opus for the same scan.
- Haiku is fast enough that 20 workers finish in < 30s wall-clock.

**Cost saving**: 90-95% vs. scanning on Opus.

---

## 3. Test generation

**Trigger**: "I just refactored `payments/` and the existing tests are obsolete. Generate fresh ones."

**Typical prompt**:
```
/subclaw for every function in src/payments/, generate a unit test that:
- exercises the happy path
- exercises at least one edge case
- mocks external dependencies
Save each test to tests/payments/<func_name>.test.ts
```

**Architecture**: planner-generator-evaluator, or just orchestrator-workers with `--write` permissions (or per-brief `tools:` frontmatter — see Step 6 in the skill).

**Why subclaw helps**:
- Test generation is a "draft, then refine" task. Cheap model drafts, Opus reviews.
- 30 test files generated in parallel: ~30s wall-clock.
- Opus only reviews the final 30 files (typically 50-100 lines each = ~3k tokens total).

**Cost saving**: 80-90% vs. generating on Opus directly.

---

## 4. Documentation sweep

**Trigger**: "I have 200 functions without docstrings. Generate them all."

**Typical prompt**:
```
/subclaw for every exported function in src/ that lacks a docstring,
add a JSDoc comment. Include @param, @returns, and a one-line summary
of the function's purpose based on its body.
```

**Architecture**: orchestrator-workers with `--write` permissions (or per-brief `tools:` frontmatter for finer control).

**Why subclaw helps**:
- Docstring generation is mechanical; Haiku handles it well.
- 200 functions × 1 Haiku call each, run 10 in parallel = 20 batches.
- Opus spot-checks 10% of the result for quality.

**Cost saving**: 90-95% vs. doing it on Opus.

---

## 5. Security smell scan

**Trigger**: "I'm worried about hardcoded secrets and PII in the repo."

**Typical prompt**:
```
/subclaw audit the entire repo for:
- hardcoded API keys, tokens, or passwords
- PII (emails, phone numbers, addresses) in code or comments
- SQL injection patterns (string concatenation in queries)
- unsafe deserialization (pickle, yaml.load, eval)
Skip test files and node_modules. Return file:line and a 1-sentence risk assessment.
```

**Architecture**: orchestrator-workers. Opus decomposes by category, Haiku workers scan.

**Why subclaw helps**:
- Pattern matching is exactly what cheap models are good at.
- 4 categories × 20 workers each = 80 parallel scans.
- Opus aggregates findings, deduplicates, and prioritizes.

**Cost saving**: 90-95% vs. running the audit on Opus.

---

## 6. Refactor planning

**Trigger**: "I want to migrate our REST endpoints to gRPC. What's the plan?"

**Typical prompt**:
```
/subclaw analyze src/api/ and produce a refactor plan for migrating
from REST to gRPC. For each endpoint, note:
- HTTP method + path
- Request/response schema
- Dependencies (db, cache, auth)
- Estimated complexity (low/medium/high)
Group by module.
```

**Architecture**: cross-review layering.
1. Haiku drafts the per-endpoint analysis (mechanical, fast).
2. Sonnet audits the Haiku drafts, flags omissions, adds nuance.
3. Opus reads both layers, resolves disagreements, produces the final plan.

**Why subclaw helps**:
- The three-layer review catches errors a single model would miss.
- Haiku + Sonnet cost is a fraction of the Opus-only path.
- Opus only sees the synthesized plan + the flagged disagreements.

**Cost saving**: 70-85% (lower than the other use cases because Opus does more of the synthesis work here).

---

## 7. Cross-language port

**Trigger**: "I want to translate this Python utility to TypeScript."

**Typical prompt**:
```
/subclaw translate src/utils/legacy_parser.py to TypeScript, file by file.
For each function:
1. Haiku writes the TS translation.
2. Sonnet reviews the translation against the original Python, flags bugs.
3. Opus resolves the disagreements and writes the final TS.
```

**Architecture**: planner-generator-evaluator × N. Each function is a separate mini-pipeline.

**Why subclaw helps**:
- The mechanical translation (Haiku) is cheap.
- The review (Sonnet) catches class-system differences.
- The final synthesis (Opus) only handles disagreements.

**Cost saving**: 80-90% vs. translating on Opus.

---

## Anti-patterns (use cases where subclaw *won't* help)

Be honest with yourself: not every task is a swarm task.

- **Frontier reasoning** — "design a new distributed consensus algorithm". Opus all the way. A 2-cent Haiku draft is worse than useless here.
- **One-shot Q&A** — "explain this error message". A swarm can't help. Just ask Opus directly.
- **Tasks under ~5k tokens** — the orchestration overhead (~5-10s of slash command decomposition) eats the savings. Just ask the main model.
- **Latency-critical paths** — interactive chat, autocomplete, real-time tooling. Swarm latency is 30-150s. Don't use subclaw for this.

Rule of thumb: **if the task is "think hard about X", use Opus. If the task is "do N similar mechanical things", use subclaw.**
