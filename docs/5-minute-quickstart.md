# 5-Minute Quickstart

> The fastest path to "subclaw is running and the /subclaw slash command works." If anything is unclear, see the [full Quick Start in the README](../README.md#-quick-start).

---

## 0. Prerequisites (already have these?)

- **Python 3.9+** (`python --version`)
- **One API key** (Anthropic, OpenAI, OpenRouter, or any Anthropic-compatible vendor)
- **Claude Code** (only needed for the `/subclaw` slash command — skip this step if you just want the proxy)

---

## 1. Clone & install (60 seconds)

```bash
git clone https://github.com/Akichoooo/subclaw.git
cd subclaw/proxy
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Configure (60 seconds)

```bash
cp examples/keys.minimal.json keys.json
# Edit keys.json: replace REPLACE_WITH_ANTHROPIC_KEY with your real key
```

If you don't have a Claude API key, OpenAI works too. Pick a different example:

```bash
# OpenAI / GPT-4o-mini only
cp examples/keys.budget-tight.json keys.json
# Or multi-provider
cp examples/keys.multi-provider.json keys.json
```

## 3. Start the proxy (10 seconds)

```bash
python app.py
```

You should see:

```
[claw-proxy] PREFIX FREEZE | fp=... | system prompt locked (...)
[claw-proxy] proxy listening on http://0.0.0.0:4748
```

## 4. Verify it works (30 seconds)

In a new terminal:

```bash
# Health check
curl http://localhost:4748/health
# → {"status":"ok"}

# List discovered models
curl http://localhost:4748/models | python -m json.tool
# → list of your configured models

# Real call
curl -X POST http://localhost:4748/v1/messages \
  -H "x-session-id: test-001" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{"model": "claude-3-haiku-20240307", "max_tokens": 64, "messages": [{"role": "user", "content": "Say hi in 5 words."}]}'
```

You should get a valid response with `"content": [{"type": "text", "text": "..."}]`.

## 5. Install the slash command (60 seconds, optional)

```bash
# In the repo root, not proxy/
cd ..
cp cli-skills/claude/subclaw.md ~/.claude/commands/
cp cli-skills/run-claw-pool.sh ~/.claude/scripts/
chmod +x ~/.claude/scripts/run-claw-pool.sh
```

Restart Claude Code. Type `/` in the prompt — you should see `/subclaw` in the command list.

## 6. First swarm (30 seconds)

In Claude Code:

```
/subclaw audit this repo for TODO comments and return file:line for each one
```

The orchestrator (your main model) decomposes the request, dispatches N workers in parallel, and you get a single synthesized report. Check the proxy's stats:

```bash
curl http://localhost:4748/stats | python -m json.tool
```

You'll see `cache_hit_rate`, `cache_read_tokens`, `cost_saved_cny`, and per-key activity.

---

## You just saved 90%+ on this task

That's the whole point. The 50,000 tokens of TODO comments in your repo would have cost ~$0.15 on Opus. With subclaw, it cost ~$0.005 (Haiku drafts) + ~$0.001 (Opus synthesis) = ~$0.006.

If you want to see the dashboard:

```bash
open http://localhost:4748/dashboard
```

---

## Next steps

- Read [the architecture deep-dive](architecture.md) to understand the design.
- Read [the comparison](comparisons.md) to see how subclaw stacks up against alternatives.
- Check [the FAQ](faq.md) when you hit a question.
- Customize [your keys.json](https://github.com/Akichoooo/subclaw/blob/main/examples/README.md) for your real workload.

If something didn't work in this quickstart, [open an issue](https://github.com/Akichoooo/subclaw/issues/new?template=bug_report.md) with the output of the failed step.
