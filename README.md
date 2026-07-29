# open-composio

**The local-first MCP tool gateway.** Agent tool integrations — GitHub, web search, weather, your own functions — behind one MCP server and one embeddable SDK, with credentials that **never leave your machine**. Secrets live in your OS keychain (Windows Credential Manager, macOS Keychain, Linux Secret Service), not in anyone's cloud.

Why this exists: hosted tool platforms route your OAuth tokens and API keys through their servers, and their "self-hosted" tiers are closed-source or catalog-crippled. See [RESEARCH.md](RESEARCH.md) for the landscape and [RECOMMENDATIONS.md](RECOMMENDATIONS.md) for the design.

## Install

```bash
pip install -e .          # from this repo (PyPI publish pending)
```

## Use from Claude Code / Claude Desktop / Cursor (MCP)

```bash
claude mcp add open-composio -- open-composio mcp
```

The gateway exposes three meta-tools — `search_tools`, `get_tool_schema`, `execute_tool` — so agents discover tools progressively instead of drowning the context window in schemas.

Connect an app first (credentials go to the OS keychain):

```bash
open-composio apps                 # list apps + connection status
open-composio connect github       # prompts for token, or --field token=ghp_...
```

## Use as an embedded SDK (no server at all)

```python
from open_composio import OpenComposio

oc = OpenComposio()
oc.connect("github", token="ghp_...")     # stored in OS keychain

tools = oc.get_tools("github", "web_search")
tools.as_openai()                          # OpenAI function definitions
tools.as_anthropic()                       # Anthropic tool definitions
result = tools.call("github_get_user", {})  # dispatch an LLM tool call

@oc.tool
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b                           # same executor, middleware, audit

oc.use(before=lambda ctx: print("about to run", ctx.app_id, ctx.action))
oc.serve_mcp()                             # optional: one line to become an MCP server
```

## Run the dashboard + REST API

```bash
open-composio serve        # http://127.0.0.1:8000 — dashboard + /api/*
```

Binding beyond localhost requires `OPEN_COMPOSIO_API_TOKEN` (enforced).

## Where things live

| Piece | Path |
|---|---|
| Package (core, SDK, MCP, REST, CLI) | [open_composio/](open_composio/) |
| Data dir (vault key/index, audit log) | `~/.open-composio` (override: `OPEN_COMPOSIO_HOME`) |
| Secrets | OS keychain; encrypted-file fallback (`OPEN_COMPOSIO_VAULT=file`) |
| Audit log (params hashed, never raw) | `~/.open-composio/audit.jsonl` |
| Tests | `pytest` (25 tests) |

`server/`, `sdk/`, and `dashboard/` at the repo root are the original prototype, superseded by the package (the dashboard now ships inside it). Safe to delete once you're happy.

## Roadmap

Per [RECOMMENDATIONS.md](RECOMMENDATIONS.md): OAuth loopback flows (`gh auth login`-style), declarative YAML catalog with community specs, per-connection permission allowlists, pinned tools as first-class MCP tools, TypeScript SDK.
