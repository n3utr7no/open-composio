# Open-Composio: Recommendations

*Date: July 27, 2026 — follows from [RESEARCH.md](RESEARCH.md)*

**Positioning: "The local-first MCP tool gateway."** One MCP server, an uncrippled catalog, credentials that never leave the machine. Not "free Composio" — the wedge is the trust model plus day-one compatibility with Claude Code, Claude Desktop, Cursor, and every other MCP client.

---

## Architecture target: library core, thin facades

The core (registry, vault, executor, catalog loader) is an **embeddable library** with no server dependency. The SDK, MCP server, and REST API are thin facades over it — the gateway is just the SDK with a transport attached.

```
open_composio/
  core/        # registry, vault, executor, catalog loader  ← the actual product
  sdk          # in-process Python API over core
  mcp          # MCP server facade over core (stdio / streamable HTTP)
  rest         # FastAPI facade over core (dashboard, remote SDK mode)
```

```
┌─────────────┐   MCP (stdio / streamable HTTP)   ┌──────────────────────────┐
│ MCP clients  │ ◄──────────────────────────────► │  open-composio core       │
│ Claude Code, │                                  │                          │
│ Cursor, ...  │   meta-tools:                    │  ┌─ Registry ──────────┐ │
└─────────────┘   search_tools                    │  │ YAML app specs +    │ │
                  get_tool_schema                 │  │ Python escape hatch │ │
┌─────────────┐   execute                         │  └─────────────────────┘ │
│  Dashboard   │ ◄────── REST (existing API) ───► │  ┌─ Vault ─────────────┐ │
└─────────────┘                                   │  │ OS keychain / age-  │ │
                                                  │  │ encrypted SQLite    │ │
┌─────────────┐   in-process import OR REST       │  └─────────────────────┘ │
│ Python SDK   │ ◄──────────────────────────────► │  ┌─ Audit log (SQLite) ┐ │
└─────────────┘                                   │  └─────────────────────┘ │
                                                  └──────────────────────────┘
```

## R1. Single MCP server with progressive disclosure  *(highest leverage)*

Do **not** register every action as a first-class MCP tool — hundreds of schemas flood the context window (the failure Strata was built to fix). Expose three meta-tools:

| Meta-tool | Behavior |
|---|---|
| `search_tools(query)` | Keyword + fuzzy (later: embedding) search over action names/descriptions. Connected apps rank first; unconnected apps returned with `needs_connection: true` so the agent can tell the user to connect them. |
| `get_tool_schema(tool_name)` | Returns the full JSON Schema for one action, loaded into context only at point of use. |
| `execute(tool_name, params)` | Routes through the existing `ToolRegistry.execute_action` path. |

**Hybrid pinning:** let users pin 5–10 favorite actions in the dashboard; pinned actions are registered as real MCP tools (best UX for the common case), everything else sits behind `search_tools`. Emit `notifications/tools/list_changed` when pins or connections change.

**Quality bar:** `search_tools` recall is the product. Every YAML spec must have a one-line, verb-first action description ("Send a message to a Slack channel"), and CI should reject specs without them. Ship an eval file of query→expected-tool pairs and test retrieval against it.

Implementation: `mcp` Python SDK (official), stdio transport first (what Claude Code/Desktop spawn), streamable HTTP second. Keep the REST API — dashboard and SDK already use it; MCP is an additional transport over the same registry.

## R2. Encrypted credential vault  *(the headline feature — do before publicizing anything)*

Replace plaintext `server/data/connections.json`:

- Primary: OS keychain via `keyring` (Windows Credential Manager/DPAPI, macOS Keychain, Linux Secret Service).
- Fallback (headless/Docker): Fernet-encrypted SQLite, key derived from a passphrase or provided via env/file.
- Store only opaque connection IDs in SQLite metadata; secrets live in the vault. Never return secrets over any API — the dashboard shows connected/disconnected only.

## R3. Local OAuth loopback flow

Implement `auth_type: oauth2` the way `gh auth login` does it: spin up a one-shot listener on `http://127.0.0.1:<port>/callback`, open the browser, capture the code, exchange for tokens, store in the vault, auto-refresh on expiry. Users bring their own OAuth client IDs (document per-app setup in each YAML spec). This directly eliminates the #1 documented competitor complaint — tokens transiting a vendor cloud.

## R4. Declarative catalog

- Apps defined as YAML: id, name, auth config, actions with method/URL/params schema/response mapping. A generic HTTP executor interprets them; Python modules (like the current `apps/github.py`) remain the escape hatch for complex logic.
- `catalog/` directory ships with the install; `open-composio add <app>` pulls a spec from the community repo; a static JSON index on GitHub Pages gives searchable discovery with zero hosted infrastructure.
- Migrate the three existing apps to YAML as the proof, keeping GitHub's custom logic as the escape-hatch example.

## R5. Permissions + audit log

- Per-connection action allowlists ("read issues yes, create issues no"), enforced in `execute`, editable in the dashboard. AgentPort's traction shows granular permissions resonate.
- Append-only SQLite log of every execution: timestamp, tool, params hash, caller, result status. Viewable in the dashboard.

## R6. Distribution & hygiene

- Package as `pyproject.toml`; `pipx install open-composio && open-composio serve` and a single Docker image. Local-first dies if setup exceeds a minute.
- Claude Code/Cursor/Claude Desktop config snippets in the README (`claude mcp add open-composio -- open-composio mcp`).
- Bearer-token auth on the REST API before anything binds beyond 127.0.0.1; drop `allow_origins=["*"]` to the dashboard origin.
- Tests (registry, executor, vault roundtrip, an MCP integration test), a README stating the local-first thesis in the first paragraph.

## R7. SDK-first embedded mode

Composio's SDK is a thin client that requires their cloud; an SDK where the registry, vault, and executor run **in-process** requires nothing at all — `pip install open-composio`, no daemon, no port, no deployment. This is the purest form of the local-first pitch, and it falls out of the library-core architecture above.

**SDK surface:**

- **One API, two modes.** `OpenComposio()` runs everything in-process; `OpenComposio(base_url="http://127.0.0.1:8000")` talks to a shared local server (the current `sdk/client.py`). Same methods either way — devs start embedded, graduate to the server only when multiple agents or the dashboard need shared state.
- **Framework adapters as the main export.** `oc.get_tools("github", "slack")` returning tools with `.as_openai()`, `.as_anthropic()` (Anthropic tool-runner compatible), `.as_langchain()`, `.as_vercel()`. This is the README copy-paste that drives adoption.
- **In-SDK auth flows.** `oc.connect("github")` runs the loopback OAuth flow (R3) or prompts for a key from the developer's own script, storing to the vault (R2). The dashboard becomes optional.
- **`@oc.tool` decorator** to register local Python functions alongside catalog apps, flowing through the same execute/permissions/audit path.
- **Execution middleware.** `oc.use(before=..., after=...)` hooks — the single interception point where permissions, audit logging (R5), and human-in-the-loop approval (`confirm=True` on dangerous actions) live, regardless of which facade invoked the tool.
- **`oc.serve_mcp()`** — one line turns any embedded setup into an MCP server (R1), so SDK and gateway are the same product with different transports.

**Known trade-offs:** in-process mode has no shared state across agents (credentials are fine — each process hits the OS keychain — but pins and audit logs fragment per process); TypeScript parity matters since most agent devs are in TS — ship Python first, treat a TS port as a later milestone rather than splitting effort now.

---

## Sequencing

| Phase | Ships | Why this order |
|---|---|---|
| **1. Trust core** | Library-core restructure, R2 vault, R6 hygiene/packaging | Plaintext creds contradict the entire pitch; the core split costs little now and makes SDK/MCP/REST all first-class later. |
| **2. MCP gateway + SDK** | R1 meta-tools + stdio, R7 embedded SDK basics (`get_tools` + adapters, `@oc.tool`), README quickstarts | This is the demo: `pipx install` → connect GitHub → Claude Code uses it in under 2 minutes; the SDK adapters are the same demo for framework devs. |
| **3. Auth depth** | R3 OAuth loopback, token refresh | Unlocks the apps people actually want (Gmail, Slack, Notion). |
| **4. Catalog engine** | R4 YAML specs + community repo, R5 permissions/audit | Scale via PRs, not your own hands. |

**Non-goals:** hosted/cloud offering, multi-tenant SaaS auth, competing on catalog count with Composio/ACI, workflow-builder UI. Every one of these re-introduces the compromises this project exists to remove.
