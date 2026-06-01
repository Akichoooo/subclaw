# MCP (Model Context Protocol) Integration

> [Model Context Protocol](https://modelcontextprotocol.io) is an emerging standard for AI agents to discover and call external tools. subclaw is naturally compatible with MCP because the proxy speaks the same Anthropic Messages API that MCP uses underneath.

This page documents three patterns for using subclaw with MCP-compatible hosts (Claude Desktop, Cursor, Continue, Zed, custom agents).

---

## What is MCP, in one sentence

MCP is a JSON-RPC protocol that lets an AI host (like Claude Desktop) discover available tools from a server, call them with arguments, and stream results back. The host and server speak a standardized schema, so any MCP host can talk to any MCP server.

## Why subclaw + MCP is a natural fit

subclaw's `/v1/messages` endpoint is the same Anthropic Messages API that MCP tool calls invoke under the hood. This means:

1. **You can use subclaw as the upstream LLM for any MCP server.** The server hands off LLM calls to subclaw, which routes them with cost optimization.
2. **You can build an MCP server that exposes subclaw's models as tools.** Agents can then call models directly.
3. **You can wrap subclaw itself as an MCP server.** Then Claude Desktop can discover and use subclaw's models as a tool.

---

## Pattern 1: Expose subclaw as an MCP server

A thin MCP shim that proxies tool calls to subclaw's `/v1/messages` endpoint. Lets Claude Desktop (or any MCP host) discover and call models that subclaw routes.

### Python implementation (reference, ~80 lines)

```python
# mcp_subclaw_server.py
# pip install mcp httpx
from mcp.server import Server
from mcp.types import Tool, TextContent
import httpx, json

app = Server("subclaw")
PROXY_URL = "http://localhost:4748"

@app.list_tools()
async def list_tools():
    # Discover models from subclaw
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{PROXY_URL}/models")
        models = resp.json().get("models", [])
    return [
        Tool(
            name=f"llm_{m['alias'].replace('-', '_')}",
            description=f"Call {m['model_id']} via subclaw. {m.get('capabilities', {})}.",
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "system": {"type": "string"},
                    "max_tokens": {"type": "integer", "default": 1024},
                },
                "required": ["prompt"],
            },
        )
        for m in models
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict):
    # Map tool name back to model alias
    alias = name.removeprefix("llm_").replace("_", "-")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{PROXY_URL}/v1/messages",
            headers={"anthropic-version": "2023-06-01", "x-session-id": f"mcp-{name}"},
            json={
                "model": alias,
                "max_tokens": arguments.get("max_tokens", 1024),
                "messages": [{"role": "user", "content": arguments["prompt"]}],
                "system": arguments.get("system", ""),
            },
        )
    return [TextContent(type="text", text=resp.text)]

if __name__ == "__main__":
    app.run()
```

### Register with Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "subclaw": {
      "command": "python",
      "args": ["/path/to/mcp_subclaw_server.py"]
    }
  }
}
```

Restart Claude Desktop. The subclaw models will appear as tools in the chat.

---

## Pattern 2: Use subclaw as the upstream for an existing MCP server

Many MCP servers default to using the host's main LLM. You can override that to point at subclaw, and you get all the cost savings and cache locality for free.

### Example: override the filesystem MCP server's LLM

If you're using [`@modelcontextprotocol/server-filesystem`](https://github.com/modelcontextprotocol/server-filesystem) or similar, point its `LLM_BASE_URL` env var at subclaw:

```bash
LLM_BASE_URL=http://localhost:4748/v1 claude-desktop
```

The MCP server's internal LLM calls now go through subclaw.

---

## Pattern 3: subclaw as the "big LLM" for any agent

If you're building a custom agent (LangChain, AutoGen, CrewAI, raw loop), point its LLM client at subclaw:

```python
from langchain_anthropic import ChatAnthropic

llm = ChatAnthropic(
    base_url="http://localhost:4748",
    api_key="not-used-by-proxy",
    model="claude-3-5-sonnet-20241022",
)
```

Or for the OpenAI SDK:

```python
from openai import OpenAI
client = OpenAI(
    base_url="http://localhost:4748/v1",
    api_key="not-used-by-proxy",
)
```

The agent doesn't need to know about subclaw. It just sees a normal LLM endpoint. You get session affinity, multi-key rotation, and the budget circuit breaker transparently.

---

## Using MCP tools from within subclaw workers

subclaw workers (the `claude` CLI instances) inherit any MCP servers configured in the user's `~/.claude.json`. So if you register an MCP server globally in Claude Code, subclaw workers will have access to those tools too.

Example: register a SQLite MCP server, and a subclaw worker auditing a codebase can now query a local database as part of its task.

---

## Sub-claw as an MCP-style slash command for any agent

The `/subclaw` slash command is itself an MCP-style tool, just delivered as a Claude Code slash command instead of an MCP server. Both patterns are equivalent in capability. We may ship an MCP version in the future — see [the roadmap](../README.md#-roadmap).

---

## Troubleshooting

**The MCP server can't connect to subclaw.**
Make sure subclaw is running (`curl http://localhost:4748/health`) and that the MCP server's `PROXY_URL` env var points at the right host/port.

**The tool list is empty.**
Hit `curl http://localhost:4748/models` to see what subclaw is advertising. If that's empty, your `keys.json` is empty or malformed.

**Tool calls are slow.**
The first call warms the prompt cache. Subsequent calls in the same session should be much faster.

**Tool calls return 429.**
subclaw's 429 failover should kick in automatically. If it doesn't, you may have more concurrent MCP tool calls than your key pool can sustain. Add more keys to `keys.json` or reduce concurrency.

---

## See also

- [Model Context Protocol spec](https://modelcontextprotocol.io)
- [MCP server examples](https://github.com/modelcontextprotocol/servers)
- [Anthropic's MCP announcement](https://www.anthropic.com/news/model-context-protocol)
- [subclaw architecture deep-dive](architecture.md)
