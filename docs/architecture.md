# Architecture Deep-Dive

This document explains the three ideas that make `subclaw` worth using. If you read nothing else, read these three sections:

1. **Session affinity for prompt cache locality** (the killer feature)
2. **Multi-key rotation with budget circuit breaker**
3. **Anthropic ↔ OpenAI streaming protocol translation**

---

## 1. Session Affinity for Prompt Cache Locality

### The problem

Anthropic's [`prompt caching`](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching) charges you roughly **10% of the input token price** for cache reads. If your cache hit rate is 80%, your effective input cost drops by 72%.

But the cache is keyed by **prefix** — and the prefix is hashed on the API key + the prompt structure. **Switching API keys between requests kills the cache.**

Most "rotating" gateways solve 429 by blindly distributing requests across keys. That gives you headroom but burns the cache.

### What subclaw does differently

`claw-proxy` pins each `x-session-id` to a single API key for the entire lifetime of that session. Only a real failure (HTTP 429, 5xx, classified degraded response) triggers a re-bind, and the re-bind is sticky: subsequent requests for that session go to the new key.

```python
# proxy/app.py
def pick_key_for_session(session_id: str, model: str) -> int:
    with _session_lock:
        if session_id in _session_map:
            key_idx = _session_map[session_id]
            if _entry_matches_model(CLAW_KEYS[key_idx], model):
                _session_ttl[session_id] = time.time()
                return key_idx          # keep the same key, keep the cache warm

        # first request for this session, or stale entry — pick a key
        matching = routing_for_model(model)
        primary  = [i for i in matching if CLAW_KEYS[i].get("tier") == "primary"] or matching
        candidates = [...load-balanced list...]
        return candidates[_next_key_idx % len(candidates)]
```

`run-claw-pool.sh` injects a stable `x-session-id` per worker task so the proxy can do this correctly even when the worker process is short-lived.

### What you get

- **Cache hit rate: 70-90%** on multi-turn worker tasks (vs ~0% with naïve round-robin)
- **Effective input cost: $0.30 / 1M tokens** (was $3.00) for warm-cache reads
- **First token latency: lower** because the prefix is already in cache

---

## 2. Multi-Key Rotation with Budget Circuit Breaker

### The mechanics

The proxy maintains two state maps in memory:

```python
_session_map:  Dict[str, int]      # session_id -> key index
_key_sessions: Dict[int, set]      # key index -> set of active session_ids
_session_ttl:  Dict[str, float]    # session_id -> last-touched timestamp
_SESSION_TIMEOUT = 600              # release after 10 min of inactivity
```

When a new session arrives:

1. **Stale cleanup** — sessions idle for > 10 min are released.
2. **Sticky check** — if this session has a key and that key can serve this model, reuse it (cache locality).
3. **Load balance** — among primary-tier keys that match the model, pick the one with the fewest active sessions. Round-robin within ties.
4. **Record** — map `session_id → key_idx`, add to `key_sessions[key_idx]`, stamp TTL.

When a 429 hits:

```python
def rebind_session(session_id: str, new_idx: int):
    """429 failover: stick this session to a new key going forward."""
    with _session_lock:
        old = _session_map.get(session_id)
        if old is not None and old != new_idx:
            _key_sessions[old].discard(session_id)
        _session_map[session_id] = new_idx
        _key_sessions.setdefault(new_idx, set()).add(session_id)
```

Note that the rebind is **sticky on the new key** — the cache will re-warm there, then the next request gets the hit.

### Budget circuit breaker

Configured in `keys.json`:

```json
"global_proxy_settings": {
  "circuit_breaker": {
    "max_spend_per_session_usd": 2.0,
    "max_spend_per_day_usd": 10.0
  }
}
```

The proxy tracks `cache_read_input_tokens`, `input_tokens`, and `output_tokens` per request, multiplies by the configured `cost_per_1m_in` / `cost_per_1m_out`, and **rejects the request with HTTP 402** if the cap is hit. Visible at `GET /stats` (`cost_saved_cny`, per-key spend).

This is the part that makes "let Claude Code loose on a 200k-token repo overnight" safe to do unattended.

---

## 3. Anthropic ↔ OpenAI Streaming Protocol Translation

### Why this exists

Most cheap-model endpoints (DeepSeek, GLM, Qwen, Moonshot, OpenRouter) are **OpenAI-protocol compatible**. Anthropic-protocol endpoints are Anthropic's, plus a few Anthropic-compatible clones.

`claude` (the Claude Code CLI) speaks Anthropic protocol natively. A worker that runs `claude` cannot talk to a DeepSeek endpoint without translation. Most "openai-protocol runners" reimplement the Claude Code CLI badly and crash on tool_use, lone surrogates, GBK mojibake, etc.

The cleanest fix: run the **real** Claude Code CLI as the worker, point it at `http://localhost:4748`, and let the proxy translate.

### What gets translated

| Anthropic concept | OpenAI equivalent | Where in the code |
|---|---|---|
| `system: "..."` or `system: [{type:"text", text:"..."}]` | First message with `role: "system"` | `anthropic_to_openai` |
| `messages[].content: [{type:"text"}, {type:"tool_use"}, {type:"tool_result"}]` | Mix of `messages[].content` (text) and `messages[].role: "tool"` blocks | `anthropic_to_openai` |
| `tools[].{name, description, input_schema}` | `tools[].{type:"function", function:{name, description, parameters}}` | `anthropic_to_openai` |
| SSE events (`message_start`, `content_block_delta`, `message_delta`, `message_stop`) | OpenAI chat.completion.chunk deltas | `openai_to_anthropic_chunk` |
| Final `usage.{input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}` | `usage.{prompt_tokens, completion_tokens, prompt_tokens_details.cached_tokens}` | `openai_response_to_anthropic` |

The streaming translation is the part that breaks in homegrown runners: each OpenAI chunk must be wrapped in the right Anthropic SSE event with the right `index` and `delta.type`. The proxy handles this on a per-chunk basis (`openai_to_anthropic_chunk` is called from inside the streaming loop, not batched at the end).

### Edge cases handled in the proxy

- **Empty text blocks**: dropped before sending upstream (`sanitize_anthropic_messages`), since some OpenAI-compatible vendors 400 on them.
- **Degraded mode detection**: if the upstream returns 429 / 5xx / a body matching the degradation regex (`overloaded`, `safety classifier`, `rate.?limit`, `try again`, etc.), the proxy retries with backoff and eventually rebinds the session.
- **Lone surrogates in tool_use JSON**: serialized with `ensure_ascii=False` and validated before send.
- **Idle stream timeout**: 90s with no bytes = dead socket, abort the request.

---

## Component map

```
┌──────────────────────────────────────────────────────────────────┐
│ /subclaw (Claude Code slash command, claude/subclaw.md)          │
│   - reads task, picks model tier, decides worker count           │
└──────────────────┬───────────────────────────────────────────────┘
                   │ spawns N workers
                   ▼
┌──────────────────────────────────────────────────────────────────┐
│ run-claw-pool.sh (cli-skills/run-claw-pool.sh)                   │
│   - per worker: isolated CLAUDE_CONFIG_DIR, x-session-id header  │
│   - drives the `claude` CLI in parallel via GNU parallel / xargs │
└──────────────────┬───────────────────────────────────────────────┘
                   │ HTTP POST http://localhost:4748/v1/messages
                   │ Header: x-session-id: <stable per task>
                   ▼
┌──────────────────────────────────────────────────────────────────┐
│ claw-proxy (proxy/app.py, FastAPI on :4748)                      │
│   - pick_key_for_session (cache locality)                        │
│   - anthropic_to_openai (request translation)                    │
│   - _post_with_retry (httpx, 3 attempts, backoff)                │
│   - rebind_session on 429                                        │
│   - openai_to_anthropic_chunk (stream translation)               │
│   - /stats: cache hit rate, cost, key pool state                 │
└──────────────────┬───────────────────────────────────────────────┘
                   │ upstream HTTPS to provider
                   ▼
┌──────────────────────────────────────────────────────────────────┐
│ Upstream: Anthropic / OpenAI / OpenRouter / DeepSeek / ...       │
└──────────────────────────────────────────────────────────────────┘
```

---

## Failure modes & how the proxy handles them

| Failure | Detection | Action |
|---|---|---|
| HTTP 429 from upstream | status code | `rebind_session` to next key, retry |
| HTTP 5xx | status code | exponential backoff, retry up to 3 times |
| Degraded response (classifier error, "try again") | regex on body | treat as 5xx, retry |
| Upstream disconnects mid-stream | `_STREAM_IDLE_TIMEOUT_SEC` | abort, retry once |
| Session pinned to a key whose tier no longer matches | `_entry_matches_model` check | re-pick from scratch |
| Budget cap hit | running cost > cap in `keys.json` | HTTP 402, no upstream call |
| Worker process crashes | shell exit code | `run-claw-pool.sh` records FAIL, moves on |

---

## Performance characteristics

- **Throughput**: limited by upstream providers, not by the proxy. `uvicorn` defaults handle hundreds of req/s per worker; the proxy is stateless on the request path.
- **Latency overhead**: ~2-5ms per request (FastAPI routing + lock acquisition). Negligible compared to LLM inference (hundreds of ms).
- **Memory**: O(active sessions) + O(frozen prefixes). Each frozen prefix is the system prompt + tool list, stored as a string. Tens of MB for typical workloads.
- **Concurrency model**: single-process, single-event-loop. Sufficient for personal / small-team use. For multi-user / production, run multiple uvicorn workers behind nginx.

---

## What subclaw is *not*

To set expectations:

- **Not a stateful database.** Session state is in-memory. Restart the proxy, lose the session map. Cache locality takes a hit, then re-warms.
- **Not a multi-tenant SaaS.** No auth, no per-user isolation. If you need that, fork it or use a proper auth layer in front.
- **Not a replacement for LiteLLM.** If you need 30 different providers with custom auth, use LiteLLM. If you need 1-3 providers and want to slash your Claude bill, use subclaw.
- **Not magic.** It moves work to cheap models, but if your task fundamentally requires a frontier model's reasoning, don't cheat it with a cheap model.
