# Integration Guides

`subclaw` is model- and CLI-agnostic at the proxy layer. The slash command is built for Claude Code; other tools can point at the proxy as if it were a normal Anthropic or OpenAI endpoint.

This page covers the most common integrations. If yours isn't here, [open an issue](https://github.com/Akichoooo/subclaw/issues/new) and we'll add it.

---

## Claude Code (canonical)

**Status**: first-class, the `/subclaw` slash command is bundled.

```bash
cp ./cli-skills/claude/subclaw.md ~/.claude/commands/
cp ./cli-skills/run-claw-pool.sh ~/.claude/scripts/
chmod +x ~/.claude/scripts/run-claw-pool.sh
```

See the [README](../README.md#-quick-start) for the full quick start.

---

## Codex CLI

Codex CLI from OpenAI supports custom base URLs.

**Setup**:

1. Find your Codex config file. Usually `~/.codex/config.toml` or `~/.config/codex/config.toml`.

2. Add:
   ```toml
   [model_providers.claw]
   name = "subclaw gateway"
   base_url = "http://localhost:4748/v1"
   env_key = "OPENAI_API_KEY"   # any non-empty value works; the proxy injects the real key
   ```

3. In Codex, set:
   ```bash
   codex --provider claw --model gpt-4o-mini
   ```

4. To make it default, edit the same config:
   ```toml
   model_provider = "claw"
   model = "gpt-4o-mini"
   ```

**Note**: Codex CLI will pass OpenAI-protocol requests to the proxy, which the proxy translates to Anthropic-protocol upstream (or to OpenAI-protocol if you've configured OpenAI-compatible backends in `keys.json`).

---

## Aider

Aider supports `--openai-api-base` for OpenAI-protocol backends.

**Setup**:

```bash
# One-shot
aider --openai-api-base http://localhost:4748/v1 --model openai/gpt-4o-mini

# Persistent, in ~/.aider.conf.yml
openai-api-base: http://localhost:4748/v1
model: openai/gpt-4o-mini
```

The `openai/` prefix tells Aider to send OpenAI-protocol requests. The proxy will route them to the cheapest matching backend in `keys.json` (or the one matching the `tier` you've asked for).

---

## Cursor

Cursor's custom-model support has varied over versions. As of the latest release, you can configure a custom OpenAI-compatible endpoint in `Cursor Settings → Models → Custom OpenAI API Key`.

**Setup**:

1. Cursor → Settings → Models → OpenAI API Key → click "Add Custom Model"
2. Base URL: `http://localhost:4748/v1`
3. API Key: any non-empty string (the proxy injects the real key)
4. Model name: `gpt-4o-mini` (or whatever's in your `keys.json` aliases)
5. Click "Verify" — it should pass if the proxy is running.

**Caveat**: Cursor's custom-model feature is somewhat limited. Tab autocomplete, in-line refactor, and chat all need to be tested individually. Report issues [here](https://github.com/Akichoooo/subclaw/issues).

---

## Raw curl

```bash
# Anthropic protocol
curl -X POST http://localhost:4748/v1/messages \
  -H "x-session-id: my-task-001" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-3-haiku-20240307",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello, world!"}]
  }'

# OpenAI protocol
curl -X POST http://localhost:4748/v1/chat/completions \
  -H "x-session-id: my-task-001" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello, world!"}]
  }'

# Streaming
curl -N -X POST http://localhost:4748/v1/messages \
  -H "x-session-id: my-task-001" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-3-haiku-20240307",
    "max_tokens": 1024,
    "stream": true,
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

The `x-session-id` header is what enables session affinity. If you omit it, the proxy falls back to `ip:port` of the TCP connection, which is less stable.

---

## Python (httpx / requests / openai SDK)

The OpenAI Python SDK works against the proxy out of the box:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:4748/v1",
    api_key="not-used-by-proxy",  # any non-empty value
)

# Without session affinity
resp = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(resp.choices[0].message.content)

# With session affinity
resp = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello!"}],
    extra_headers={"x-session-id": "my-task-001"},
)
```

For Anthropic SDK:

```python
from anthropic import Anthropic

client = Anthropic(
    base_url="http://localhost:4748",
    api_key="not-used-by-proxy",
)

resp = client.messages.create(
    model="claude-3-haiku-20240307",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello!"}],
    extra_headers={"x-session-id": "my-task-001"},
)
print(resp.content[0].text)
```

---

## Anything that speaks OpenAI or Anthropic protocol

If your tool speaks either protocol, it can talk to subclaw. The proxy is just a FastAPI app on `localhost:4748` that:

- Accepts `/v1/messages` (Anthropic) and `/v1/chat/completions` (OpenAI)
- Auto-translates between protocols
- Routes to whatever's in `keys.json`
- Pins sessions to keys (with the `x-session-id` header)
- Tracks spend and enforces the budget circuit breaker

That's the entire surface. No special SDK, no lock-in.
