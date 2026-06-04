# Claw Proxy - Multi-Model Agent Orchestration Gateway

Session-affinity key-rotation proxy that fronts a pool of worker models for
agent orchestration. It supports Anthropic Messages, OpenAI Chat Completions,
and Codex/OpenAI Responses on one key pool, then routes to Anthropic- or
OpenAI-protocol upstreams.

## What it does

- **Key pool ownership** - reads `keys.json`; workers authenticate to the proxy, not the upstream.
- **Session affinity** - pins one key per worker via the `x-session-id` header for prompt-cache locality.
- **Tiered failover** - `primary` keys first; escalates to `overflow` keys only on a real 429, rebinding the session to the new key.
- **Protocol translation** - `protocol: anthropic` upstreams pass through natively; `protocol: openai` upstreams are converted where possible.
- **Self-describing** - `/models` reports what is routable, so skills do not hardcode a model list.
- **Codex-compatible** - Codex CLI can use `base_url=http://localhost:4748/v1` with `wire_api=responses`.
- **Status UI** - `/ui` and `/api/status` expose routes, key pool, sessions, models, and recent worker traffic.

## Architecture

```text
Claude Code / Codex CLI / OpenAI-compatible clients
                       |
                       v
        +-------------------------------+
        |  claw-proxy  :4748            |
        |  - Key pool + session affinity|
        |  - Tiered 429 failover        |
        |  - Anthropic passthrough      |
        |    (preserves tool_use)       |
        |  - OpenAI/Responses bridge    |
        |  - Cache-hit stats            |
        +-------------+-----------------+
                      |
          +-----------+-----------+
          v                       v
   anthropic-protocol       openai-protocol
   upstream (passthrough)   upstream (converted)
```

## keys.json schema

Copy `keys.example.json` to `keys.json` and fill real keys. Each entry routes a
set of models to one upstream:

| Field | Meaning |
|---|---|
| `key` | upstream API key (kept server-side; never sent to workers) |
| `url` | upstream base; `/v1/messages` (anthropic) or `/v1/chat/completions` (openai) appended |
| `tier` | `primary` (preferred) or `overflow` (used only on 429 failover) |
| `protocol` | `anthropic` (passthrough, keeps tool_use) or `openai` (converted where possible) |
| `models` | list of model ids this key serves; omit or `[]` = wildcard |

## Endpoints

| Path | Method | Description |
|---|---|---|
| `/v1/messages` | POST | Anthropic Messages API (passthrough or converted) |
| `/v1/chat/completions` | POST | OpenAI-compatible chat entry |
| `/v1/responses` | POST | Codex/OpenAI Responses entry |
| `/v1/messages/count_tokens` | POST | Local token estimator |
| `/health` | GET | Upstream connectivity |
| `/stats` | GET | Cumulative counters + per-key session load |
| `/api/status` | GET | Dashboard/status JSON |
| `/models` | GET | Self-describing model/route table |
| `/ui` | GET | Browser dashboard |

## Local development

From the repository root:

```bash
cd proxy
pip install -r requirements.txt
cp keys.example.json keys.json   # fill real keys
python app.py                    # listens on :4748
curl -s http://localhost:4748/models | python -m json.tool
open http://localhost:4748/ui
```

## Docker

From the repository root:

```bash
docker compose build claw-proxy
docker compose up -d claw-proxy
docker ps | grep claw-proxy
```

## Used by

`/subclaw` for Claude Code and the Codex `subclaw` skill. The pool injects a
stable `x-session-id` per worker so the proxy pins each worker to one key.
