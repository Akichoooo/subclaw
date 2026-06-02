# Frequently Asked Questions

> 30+ questions about installation, costs, security, scaling, and integration. If your question isn't here, open an [issue](https://github.com/Akichoooo/subclaw/issues/new).

---

## Setup & installation

### How do I install subclaw?

```bash
git clone https://github.com/Akichoooo/subclaw.git
cd subclaw/proxy
cp keys.example.json keys.json    # then edit keys.json
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python app.py
```

The proxy listens on `http://localhost:4748` by default. See the [README](../README.md#-quick-start) for the full quick start.

### What Python version do I need?

Python 3.9 or newer. Tested on 3.9, 3.10, 3.11, 3.12, 3.13.

### Does it run on Windows?

Yes. The `run-claw-pool.sh` script is bash, but if you have Git Bash, WSL, or any POSIX shell on Windows it works. The proxy (`app.py`) is pure Python and works on Windows, macOS, and Linux.

### Do I need Docker?

No. Docker is optional. The README mentions `docker-compose up -d` as a convenience; running natively is the canonical path.

### Can I install via `pip install subclaw`?

Not yet — packaging for PyPI is on the [roadmap](../README.md#-roadmap). For now, clone the repo and `pip install -r requirements.txt`.

### How do I upgrade?

```bash
cd subclaw
git pull
pip install -r requirements.txt --upgrade
# If keys.json schema changed, see CHANGELOG.md
```

The proxy hot-reloads `keys.json` on the next request, so you don't need to restart it for config-only changes. Code changes do require a restart.

---

## Configuration

### What goes in `keys.json`?

A list of model entries (one per API key + model combination) and a `global_proxy_settings` block. See [`keys.example.json`](../proxy/keys.example.json) for the full schema.

```json
{
  "keys": [
    {
      "key": "sk-ant-...",
      "url": "https://api.anthropic.com",
      "model_id": "claude-3-5-sonnet-20241022",
      "alias": "sonnet-main",
      "tier": "smart",
      "fallback_models": ["gpt-4o"],
      "capabilities": {
        "context_window": 200000,
        "max_output": 8192,
        "supports_tools": true
      },
      "limits": {
        "max_rpm": 50, "max_tpm": 50000, "max_concurrent": 5
      },
      "cost_per_1m_in": 3.0, "cost_per_1m_out": 15.0
    }
  ],
  "global_proxy_settings": {
    "circuit_breaker": {
      "max_spend_per_session_usd": 2.0,
      "max_spend_per_day_usd": 10.0
    },
    "security": {
      "mask_secrets_in_payload": true,
      "forbidden_paths": ["**/.env", "**/*.pem"]
    },
    "features": { "auto_prompt_caching": true }
  }
}
```

### What does the `tier` field do?

`cheap` / `balanced` / `smart` is a label the slash command uses to pick the right model for the right job. Examples:

- `tier: cheap` — for scan, grep, draft tasks. Use cheap models here.
- `tier: balanced` — for synthesis, planning, medium-complexity.
- `tier: smart` — for final audit, complex reasoning. Use Opus here.

The proxy itself treats `tier` as a hint for load-balancing priority (primary first, overflow second).

### How do I add a new model?

Append an entry to `keys.json` with the required fields. The next request picks it up automatically (hot reload). Use `bash ~/.claude/scripts/run-claw-pool.sh --info` to confirm it shows up in the discovered list.

### Can I use OpenRouter as a backend?

Yes. Point `url` at `https://openrouter.ai/api/v1` and use the OpenAI protocol in your worker. The proxy translates the worker's Anthropic-protocol request to OpenAI-protocol on the fly.

### Can I use Ollama or local models?

Yes for the proxy; for the worker side, the worker must speak Anthropic protocol. Ollama speaks OpenAI protocol, so go through the proxy translation. (You can also use `claude-code-router` to make a local Anthropic-protocol wrapper if you want zero translation.)

---

## Cost & performance

### How much can I realistically save?

If your workload is mostly "scan, search, draft" (which is most of what subclaw is good at), expect **60-90%** input cost reduction compared to running everything on Opus.

Real numbers are in [`benchmarks.md`](benchmarks.md). Quick example:

- Audit 50 files (50k tokens) on Opus only: ~$7.50 + 10× loop = ~$75.
- Same audit with subclaw: Opus only reads the 200-token summaries, plus 50× Haiku parallel for the scan = ~$0.11.

### How is the cache hit rate?

70-90% on multi-turn worker tasks (vs 0% with naïve round-robin). The proxy reports `cache_hit_rate` in `GET /stats`.

### How is the latency overhead?

~2-5ms per request on the proxy. Negligible compared to LLM inference. Streaming adds ~1-2ms per chunk.

### Will my main model (Opus) get billed for the worker's context?

No. The worker talks to the cheap model (e.g., Haiku) via the proxy. The main model only sees the worker's final report (~200 tokens). Your Opus bill is bounded by the orchestrator's own context, not the workers'.

---

## Security

### Are my API keys safe?

The keys live only in `keys.json` on the machine running the proxy. Workers never see the real key — they only see `http://localhost:4748` as their base URL. The proxy injects the real key when forwarding upstream.

### Does the proxy log request bodies?

By default, no. If you turn on debug logging, you should know that request bodies (which may contain user data, code snippets, etc.) will be written to stdout. Don't run debug logging in production.

### What's `mask_secrets_in_payload`?

If set to `true`, the proxy will not log or echo the `Authorization` header or the `key` field from `keys.json`. Recommended.

### What's `forbidden_paths`?

A list of glob patterns the proxy will reject if a worker tries to read them. Currently the proxy doesn't read files itself, but the slash command can be configured to skip these paths in worker briefs.

### Should I run the proxy on a public network?

**No, not without auth.** The proxy has no built-in auth (yet — see [roadmap](../README.md#-roadmap)). If you need to expose it, put it behind nginx with a basic-auth layer, or use a firewall. For multi-user / multi-tenant, use Portkey or a similar enterprise gateway.

### How do I report a security issue?

Privately, per the [security policy](../.github/SECURITY.md). Do not file a public GitHub issue for security bugs.

---

## How it works

### What is "session affinity"?

Each worker task generates a stable `x-session-id` header. The proxy pins that session to a specific API key. Subsequent requests with the same `x-session-id` go to the same key. This keeps Anthropic's prompt cache warm.

When a 429 hits, the proxy *re-binds* the session to a different key (the cache will re-warm on the new key, but the connection stays sticky going forward).

### Why not just round-robin keys?

Round-robin gives you throughput but kills cache locality. Anthropic's prompt cache is keyed by `(api_key, prefix_hash)`. Switch keys → cache miss → you pay full input price on every request.

subclaw's design is: **sticky-by-default, rotate only on failure**. This gets you 90% of round-robin's throughput at 10× the cache hit rate.

### How is the worker actually run?

`run-claw-pool.sh` spawns N `claude` CLI processes in parallel (using GNU parallel or xargs), each with an isolated `CLAUDE_CONFIG_DIR` so they don't inherit your personal Claude Code config. Each worker is told via env var to point at `http://localhost:4748` instead of the real Anthropic API.

### What's the streaming protocol translation?

Workers using Claude Code speak Anthropic's protocol. Cheap-model endpoints usually speak OpenAI's protocol. The proxy translates request body and per-chunk streaming events in both directions. See [`architecture.md`](architecture.md#3-anthropic--openai-streaming-protocol-translation) for details.

### What happens when the budget cap is hit?

The proxy returns HTTP 402 with a JSON body explaining which cap was hit. The worker's `claude` CLI surfaces this as a tool result / error, and the orchestrator can decide whether to abort, retry with a different model, or escalate.

### Can I disable the budget cap?

Yes — set `max_spend_per_session_usd: 0` and `max_spend_per_day_usd: 0` in `keys.json`. We do not recommend this.

---

## Integration

### Does it work with Codex CLI?

Yes. Codex CLI supports custom base URLs. Set `OPENAI_BASE_URL=http://localhost:4748/v1` and the proxy will translate Anthropic-protocol requests to OpenAI-protocol for the upstream.

### Does it work with Aider?

Yes. Aider supports `--openai-api-base` and per-model base URLs. Point it at the proxy and the same translation applies.

### Does it work with Cursor?

Cursor's custom model setup is limited; check Cursor's docs for the latest. As a workaround, run subclaw on a machine accessible from the Cursor host and point Cursor at that URL.

### Can I drive the proxy from raw curl?

Yes. The proxy exposes the standard `/v1/messages` (Anthropic) and `/v1/chat/completions` (OpenAI) endpoints. Example:

```bash
curl -X POST http://localhost:4748/v1/messages \
  -H "x-session-id: my-task-001" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{"model": "claude-3-haiku-20240307", "max_tokens": 1024, "messages": [{"role": "user", "content": "Hello"}]}'
```

The `x-session-id` header is what enables the session affinity. Without it, the proxy falls back to the connection's `ip:port` (less stable across retries).

---

## Troubleshooting

### I get HTTP 429 from the proxy

This means upstream is rate-limiting. The proxy should automatically rebind your session to another key. If you keep getting 429, you have more concurrent workers than your key pool can sustain. Reduce `-j N` or add more keys.

### `python app.py` exits immediately with no error

Run with `LOG_LEVEL=DEBUG python app.py` to see the actual error. Most commonly: malformed `keys.json` (try a JSON validator) or the port is already in use (change `PROXY_PORT` in `app.py`).

### Workers all timeout

The default per-task timeout is 900 seconds (15 min). Long enough for most tasks. If you need more, pass `-T 1800` to `run-claw-pool.sh`. Also check `_STREAM_IDLE_TIMEOUT_SEC` in `app.py` (90s default).

### Cache hit rate is 0%

Either:
- Your system prompt changes every request (the cache is prefix-keyed — change the prefix, miss the cache).
- You have only one API key, so there's no rotation to "miss" (cache locality is automatic when there's only one choice).
- Your workers' `x-session-id` is regenerating per request (check `run-claw-pool.sh` — it should inject a stable session ID per task).

### The slash command doesn't appear in Claude Code

Check that `subclaw.md` is in `~/.claude/commands/` (or wherever your Claude Code config dir is). Restart Claude Code. The slash command should appear in the command list (`/`).

### Workers get 5xx errors

The proxy retries 5xx up to 3 times with exponential backoff. If you see persistent 5xx, your upstream provider is genuinely down or overloaded. Check the provider's status page. The proxy will eventually surface the error to the worker.

### The proxy is using a lot of memory

Each active session is small (~hundreds of bytes), but the frozen-prefix cache stores system prompts and tool specs as strings. If you have hundreds of unique system prompts in flight at once, expect tens of MB. For most workloads this is fine.

---

## Contributing

### How do I report a bug?

Use the [bug report template](https://github.com/Akichoooo/subclaw/issues/new?template=bug_report.md).

### How do I request a feature?

Use the [feature request template](https://github.com/Akichoooo/subclaw/issues/new?template=feature_request.md). Explain the problem first, then the proposed solution.

### Where's the code of conduct?

[`.github/CODE_OF_CONDUCT.md`](../.github/CODE_OF_CONDUCT.md).

### What's the license?

[MIT](../LICENSE).

---

## Have a question not answered here?

Open a [GitHub discussion](https://github.com/Akichoooo/subclaw/discussions). We respond within a few days.
