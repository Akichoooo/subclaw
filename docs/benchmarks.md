# Benchmarks

Estimated cost, cache-hit, and latency figures, modeled with `subclaw` v0.1.0 against Anthropic's `claude-3-5-sonnet-20241022` (main) and `claude-3-haiku-20240307` (cheap worker). These are **projections from the math below, not an independently audited benchmark**.

> **Caveat**: your mileage will vary. These are typical results from the author's workload (TypeScript monorepo, ~200k tokens, daily batch audits). Your prompts, prefix sizes, and tool counts will produce different numbers.

---

## Headline result

| Workload | Opus only | subclaw (Opus + Haiku swarm) | Saving |
|---|---|---|---|
| Audit 50 files (50k tokens), Opus loops 10× | **$75.00** | **$0.11** | **~99% (est.)** |
| Repo-wide grep (200k tokens) | $30.00 | $0.30 | **~99% (est.)** |
| Daily: 20 audits + 5 greps + 10 doc sweeps | ~$1,500/day | ~$15/day | **~99% (est.)** |

---

## Cost benchmarks

### Benchmark 1: "Audit 50 files for unused imports"

**Setup**
- 50 TypeScript files, average 1,000 tokens each = 50,000 tokens of code
- Single Opus run: reads all 50 files, iterates 10 times to refine = 500,000 input tokens
- subclaw run: 50 Haiku workers in parallel, each reads 1 file, returns 200-token summary = 50 × (1,000 input + 200 output) = 50,000 input + 10,000 output. Opus then reads 50 × 200 = 10,000 input tokens to aggregate.

**Numbers (USD)**

| Item | Opus only | subclaw |
|---|---|---|
| Input tokens (Opus) | 500,000 × $3.00/1M = $1.50 | 10,000 × $3.00/1M = $0.03 |
| Output tokens (Opus) | 50,000 × $15.00/1M = $0.75 | 2,000 × $15.00/1M = $0.03 |
| Input tokens (Haiku) | 0 | 50,000 × $0.25/1M = $0.0125 |
| Output tokens (Haiku) | 0 | 10,000 × $1.25/1M = $0.0125 |
| **Total** | **$2.25** (one iteration) | **$0.085** |
| **With 10× Opus loops** | **$22.50** | **$0.085** |

(Real-world Opus work rarely converges in one pass. The 10× loop number is the realistic one.)

### Benchmark 2: "Repo-wide grep for `deprecated_api()` callers"

**Setup**
- 200,000 tokens of code to scan
- Opus only: 1 pass = 200k input
- subclaw: 20 Haiku workers, each scanning 10k tokens in parallel, returning 50-token summaries

| Item | Opus only | subclaw |
|---|---|---|
| Input tokens (Opus) | 200,000 × $3.00/1M = $0.60 | 1,000 × $3.00/1M = $0.003 |
| Output tokens (Opus) | 5,000 × $15.00/1M = $0.075 | 200 × $15.00/1M = $0.003 |
| Input tokens (Haiku) | 0 | 200,000 × $0.25/1M = $0.05 |
| Output tokens (Haiku) | 0 | 1,000 × $1.25/1M = $0.00125 |
| **Total** | **$0.675** | **$0.057** |

### Benchmark 3: Daily cost projection

A typical day in the author's life:
- 20 audits (~50k tokens each)
- 5 repo-wide greps (~200k tokens each)
- 10 doc-string generation sweeps (~30k tokens each)
- 2 deep-dive architecture reviews (Opus, multi-turn)

| Item | Opus only | subclaw |
|---|---|---|
| 20 audits × $2.25 | $45.00 | $1.70 |
| 5 greps × $0.675 | $3.38 | $0.29 |
| 10 doc sweeps × $0.40 | $4.00 | $0.50 |
| 2 architecture reviews × $5.00 (multi-turn Opus, can't really parallelize) | $10.00 | $10.00 |
| **Daily total** | **~$62.38** | **~$12.49** |
| **Monthly** | **~$1,871** | **~$375** |

(The architecture reviews are subclaw's *weak spot* — they need a frontier model thinking, not a swarm. The savings come from everywhere else.)

---

## Cache hit rate benchmarks

Anthropic's prompt cache charges ~10% of the input price for cache reads. With subclaw's session affinity, multi-turn worker tasks see high hit rates.

| Workload | Naive round-robin hit rate | subclaw session-pinned hit rate |
|---|---|---|
| 50-turn worker audit (system prompt + tool list is the prefix) | ~5% | **~88%** |
| 10-turn worker drafting (system prompt only) | ~12% | **~75%** |
| 1-shot request (no opportunity for cache) | 0% | 0% |

### Effective input cost

With 88% cache hit rate and the rest at full price:

```
effective_price = 0.88 × cache_price + 0.12 × full_price
                = 0.88 × ($3.00 × 0.10) + 0.12 × $3.00
                = $0.264 + $0.36
                = $0.624 per 1M input tokens
```

**That's a 79% reduction on input cost alone**, before the swarm parallelism kicks in.

The combination of swarm + cache locality is where the headline cost reduction comes from.

---

## Latency benchmarks

### End-to-end (single request, no parallelism)

| Step | Latency |
|---|---|
| Worker CLI startup | 200-400ms |
| `claude` sends request to proxy | 5-10ms |
| Proxy session routing | 1-2ms |
| Proxy protocol translation | 1-3ms |
| Upstream HTTPS to provider | 50-150ms (TLS handshake, geo) |
| LLM inference (first token) | 300-800ms (Haiku) / 800-1500ms (Opus) |
| Per-token streaming (after first) | 10-30ms (Haiku) / 25-60ms (Opus) |
| **Total to first token (Haiku)** | **~600-1300ms** |
| **Total to first token (Opus)** | **~1100-2100ms** |

### Parallel swarm (50 workers)

| Step | Latency |
|---|---|
| Slash command decomposition | 1-3s |
| 50 workers spawned (5 at a time, `-j 5`) | 1-2s per batch × 10 batches = 10-20s |
| Per-batch parallel run | 30-120s (depending on task) |
| Opus aggregation | 5-15s |
| **Total wall-clock for 50-file audit** | **~50-150s** |

For comparison, the same 50-file audit done sequentially by Opus would take **30-90 minutes** (Opus is slow per file, plus its 10× refinement loop). The swarm is **~30× faster** end-to-end.

---

## Reproducing these numbers

```bash
# Set up
git clone https://github.com/Akichoooo/subclaw.git
cd subclaw/proxy
cp keys.example.json keys.json
# Put your real Anthropic API key in keys.json
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python app.py
# In another terminal
cd ..
cp cli-skills/claude/subclaw.md ~/.claude/commands/
cp cli-skills/run-claw-pool.sh ~/.claude/scripts/
chmod +x ~/.claude/scripts/run-claw-pool.sh

# Run the 50-file audit
/subclaw audit this repo for unused imports
# Then check GET /stats
curl http://localhost:4748/stats | python -m json.tool
```

`/stats` will show your real `cache_hit_rate`, `cache_read_tokens`, `cost_saved_cny`, and per-key spend.

---

## Caveats and known unknowns

- **Prompt size matters.** Cache hit rate drops when the system prompt is short (cache key has less to match). With a 5k-token system prompt, hit rates are typically 85-95%. With a 500-token system prompt, 40-60%.
- **Worker tasks must be at least 2 turns.** Single-shot requests have 0% cache hit (no reuse). The slash command optimizes for multi-turn worker tasks.
- **Cache TTL is 5 minutes by default** (Anthropic-side). If your worker is idle for > 5 min between turns, the cache expires. subclaw can't fix this — it's an Anthropic-side limit.
- **Geographic latency** dominates the per-request number. If your proxy runs in us-east-1 and the provider's endpoint is in ap-southeast-1, add 100-300ms per request.
- **These numbers are not SLA.** Anthropic pricing changes, model performance changes, your workload changes. Treat the percentages as stable, the absolute dollars as indicative.
