# Example keys.json templates for subclaw

This directory contains ready-to-use `keys.json` configuration templates for common deployment scenarios. Pick the one that matches your use case, copy it to `proxy/keys.json`, and replace the `REPLACE_WITH_*` placeholders with your real API keys.

## Available templates

| File | Use case |
|---|---|
| [`keys.minimal.json`](keys.minimal.json) | First-time users. One Anthropic key, Haiku cheap model, $2/session, $10/day cap. Lowest barrier to trying subclaw. |
| [`keys.multi-provider.json`](keys.multi-provider.json) | Production setup. Anthropic (smart + cheap) + OpenAI + DeepSeek. Best for real workloads. |
| [`keys.budget-tight.json`](keys.budget-tight.json) | Hobby projects / students. DeepSeek + gpt-4o-mini only. $0.50/session, $2/day cap. |
| [`keys.high-throughput.json`](keys.high-throughput.json) | CI pipelines / large monorepos / team use. Multiple Haiku keys for parallel load. |

## How to use

```bash
# Pick a template
cp examples/keys.minimal.json proxy/keys.json

# Edit it
$EDITOR proxy/keys.json
# Replace every REPLACE_WITH_* with a real value

# Start the proxy
cd proxy && python app.py
```

## Verifying your config

After starting the proxy, hit the model discovery endpoint:

```bash
curl http://localhost:4748/models | python -m json.tool
```

You should see one entry per model in your `keys.json`. If any are missing, the JSON is malformed or one of the required fields is wrong.

## Schema reference

See [`proxy/keys.example.json`](../proxy/keys.example.json) for the full schema and field-by-field documentation.

| Field | Required | Description |
|---|---|---|
| `key` | yes | Your API key (or placeholder until you replace it) |
| `url` | yes | Base URL of the upstream API |
| `model_id` | yes | The model identifier the upstream expects |
| `alias` | yes | Human-friendly name for this entry |
| `provider` | yes | `anthropic` / `openai` / `deepseek` / `openrouter` / `custom` |
| `protocol` | yes | `anthropic` or `openai` (which protocol the worker thinks it's calling) |
| `tier` | yes | `cheap` / `balanced` / `smart` — used by the slash command |
| `fallback_models` | no | List of model aliases to try if this one returns 429/5xx |
| `capabilities.context_window` | yes | Max input tokens |
| `capabilities.max_output` | yes | Max output tokens |
| `capabilities.supports_tools` | no | Default `false` |
| `capabilities.supports_vision` | no | Default `false` |
| `limits.max_rpm` | yes | Requests per minute cap |
| `limits.max_tpm` | yes | Tokens per minute cap |
| `limits.max_concurrent` | yes | Max concurrent in-flight requests per key |
| `cost_per_1m_in` | yes | USD per 1M input tokens (used by the budget circuit breaker) |
| `cost_per_1m_out` | yes | USD per 1M output tokens |

## Adding your own template

Have a setup that's not covered? Open a PR adding it to this directory. Useful template types we'd love to see:

- **`keys.ollama-local.json`** — point at `http://localhost:11434/v1` for fully-local inference
- **`keys.openrouter-only.json`** — single OpenRouter key, model alias routes to 50+ models
- **`keys.azure-openai.json`** — Azure-hosted OpenAI endpoint
- **`keys.bedrock.json`** — AWS Bedrock with Claude
- **`keys.vertex.json`** — GCP Vertex AI with Claude

## Security note

**Never commit a real `keys.json` to git.** Only the templates in this `examples/` directory (which contain only `REPLACE_WITH_*` placeholders) should be committed. The repo's `.gitignore` should already exclude `proxy/keys.json`. If you've accidentally committed a real one, rotate the leaked keys immediately and use `git filter-repo` to scrub history.
