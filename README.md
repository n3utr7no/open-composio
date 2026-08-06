# open-composio

[![CI](https://github.com/n3utr7no/open-composio/actions/workflows/ci.yml/badge.svg)](https://github.com/n3utr7no/open-composio/actions/workflows/ci.yml)

**The local-first MCP tool gateway.** Point it at the MCP servers you already use, and it becomes the one endpoint your agents talk to — holding the credentials in your OS keychain, enforcing what agents may do, and logging every call. Nothing routes through anyone's cloud.

Hosted tool platforms route your OAuth tokens and API keys through their servers, and their "self-hosted" tiers are closed-source or catalog-crippled. This is the opposite: the credential broker and context governor runs on your machine, and the catalog is whatever MCP servers you mount.

## Install

```bash
pip install -e .          # from this repo (PyPI publish pending)
```

## Use from Claude Code / Claude Desktop / Cursor (MCP)

```bash
claude mcp add open-composio -- open-composio mcp
```

Agents get three meta-tools — `search_tools`, `get_tool_schema`, `execute_tool` — and discover the rest at runtime. That keeps context cost **constant** instead of proportional to your catalog:

```
 catalog size |    all tools |  meta-tools |   saving
           50 |        5,262 |         303 |   94.2%
          600 |       63,150 |         303 |   99.5%
```

<sub>`python scripts/benchmark_context.py` — a 200k context fits ~1,900 tool schemas registered directly; with meta-tools catalog size is irrelevant.</sub>

## Mount the MCP servers you already use

Rather than re-implementing integrations, proxy the ecosystem's servers. Their tools join the registry, but *your* vault holds the credentials and *your* policy governs the calls:

```bash
open-composio mcp-add github --env GITHUB_TOKEN \
    -- npx -y @modelcontextprotocol/server-github
open-composio connect github          # GITHUB_TOKEN -> OS keychain
open-composio mcp-refresh github      # discover its tools (cached after this)
open-composio mcp-list
```

## Built-in apps

```bash
open-composio apps                 # apps, connection status, vault backend, policy
open-composio connect github --verify   # prompts securely, then smoke-tests the token
```

## Use as an embedded SDK (no server at all)

```python
from open_composio import OpenComposio

oc = OpenComposio()
oc.connect("github", token="ghp_...")     # stored in OS keychain

tools = oc.get_tools("github", "web_search")   # unconnected apps excluded
tools.as_openai()                          # OpenAI function definitions
tools.as_anthropic()                       # Anthropic tool definitions

result = tools.call("github_get_user", {})          # sync callers
result = await tools.acall("github_get_user", {})   # async agent frameworks

@oc.tool(destructive=True, cache_ttl=60)
def deploy(env: str) -> str:
    """Ship it."""
    return f"deployed to {env}"            # same validation, policy, audit

oc.use(after=lambda ctx, r: {**r, "token": "[REDACTED]"})   # transform results
oc.serve_mcp()                             # one line to become an MCP server
```

## Permissions and approval

Agents shouldn't be able to silently do destructive things. Actions are marked
`x-destructive` (or inferred from their verb), and a policy decides what runs:

```bash
open-composio policy show
open-composio policy allow "github.*"           # allowlist mode
open-composio policy deny "github.delete_*"     # hard block
open-composio policy approve "github.create_issue"   # pre-approve one destructive action
```

With no policy file everything is permitted, so upgrading never breaks a working setup. Once a policy exists it is enforced identically across MCP, REST and the SDK — enforcement lives in the executor, not the transport. In code, `Policy(approval_handler=...)` lets you prompt a human per call.

## Run the dashboard + REST API

```bash
open-composio serve        # http://127.0.0.1:8000 — dashboard + /api/*
open-composio audit        # or the dashboard's Logs view
```

Binding beyond localhost requires `OPEN_COMPOSIO_API_TOKEN` (enforced).

## Security model

Credentials are stored, strongest first:

1. **OS keychain** (default when available) — Windows Credential Manager, macOS Keychain, Linux Secret Service. Secrets are guarded by your OS login.
2. **Encrypted file with a passphrase** — set `OPEN_COMPOSIO_VAULT_PASSPHRASE`; the Fernet key is derived via scrypt and only a random salt touches disk. Without the passphrase the ciphertext is useless.
3. **Encrypted file with a generated key file** (headless fallback) — `vault.enc` + `vault.key` side by side. *Know what this buys you:* it protects against accidental disclosure (greps, backups, pasted directory listings), **not** against an attacker who can read the whole data directory. Use a passphrase if that's in your threat model.

Falling back from the keychain is never silent — you get a warning explaining why, because keyring-stored connections would otherwise just "appear disconnected."

Credentials are read inside the executor and passed straight to the integration's HTTP call or the upstream process's environment. They are never serialized into a tool result, a JSON schema, or an API response, so **no LLM sees them through the gateway**. The one thing that defeats this is pasting a secret into a chat and asking an agent to run the connect command for you — type it into `open-composio connect` yourself, or use the dashboard form.

Every execution is audit-logged to `~/.open-composio/audit.jsonl` with a SHA-256 fingerprint of the params — raw parameters and results are never written.

## Execution pipeline

Every call, whatever the transport, goes through:

```
auth -> schema validation -> policy/approval -> before hooks
     -> cache -> handler (timeout) -> after hooks -> truncation -> audit
```

Params are validated against the action's JSON Schema before dispatch, so a hallucinated field yields *"unexpected property 'x'; expected 'msg'"* rather than a Python `TypeError`. Oversized results are truncated with a marker instead of blowing the agent's context. Set per-action `x-cache-ttl` to memoize read-only calls.

## Where things live

| Piece | Path |
|---|---|
| Package (core, SDK, MCP, REST, CLI, upstream) | [open_composio/](open_composio/) |
| Data dir (vault, policy, audit, upstream cache) | `~/.open-composio` (override: `OPEN_COMPOSIO_HOME`) |
| Force a vault backend | `OPEN_COMPOSIO_VAULT=keyring\|file` |
| Context benchmark | [scripts/benchmark_context.py](scripts/benchmark_context.py) |
| Tests | `pytest` (79, incl. a `search_tools` retrieval eval and real MCP proxying) |

## Roadmap

OAuth loopback flows (`gh auth login`-style), connection pooling for upstream MCP servers, declarative YAML catalog, pinned tools as first-class MCP tools, OpenTelemetry export, TypeScript SDK.
