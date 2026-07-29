# Open-Composio: Local-First Research Report

*Date: July 27, 2026*

## 1. What this project is today

A minimal self-hosted clone of Composio's core loop:

- **`server/`** — FastAPI app exposing `GET /api/apps`, `GET /api/apps/{id}/actions`, `POST /api/connections/{id}`, `POST /api/execute/{id}/{action}`. An in-memory `ToolRegistry` (registry.py) maps `{app_id}_{action}` → async handler. Three integrations: GitHub (PAT), weather, web search. Credentials are stored **in plaintext** in `server/data/connections.json`.
- **`sdk/`** — Thin Python HTTP client; `load_as_tools()` converts actions into OpenAI-style function definitions.
- **`dashboard/`** — Vanilla JS/HTML app browser + connect UI.

It runs entirely on localhost with no cloud dependency — so it is already *accidentally* local-first. The question is whether making that the deliberate identity is worth pursuing.

## 2. Does something like this already exist?

Yes — the space is crowded, but **every existing option compromises on local-first in a specific way**:

| Project | License / openness | Catalog | The self-hosting catch |
|---|---|---|---|
| **Composio** | SDK open, **backend closed** | 250–800+ tools | Credential store & execution engine are closed-source; on free plans credentials route through Composio's cloud even with self-hosted SDKs |
| **ACI.dev** (aipotheosis-labs) | Apache 2.0, fully open | 600+ hosted | Self-hosted instances get only **3 integrations** (Brave Search, HN, Gmail) "for local testing" — the catalog is the moat, and it's cloud-gated |
| **Klavis AI / Strata** | Open-source MCP server (5.7K★, YC S25) | 300–600+ | Managed OAuth is the hosted product; Strata self-host exists but auth/multi-tenant flows lean on their cloud |
| **Nango** | Open source | 800+ APIs | Closest philosophically (integrations live in your repo, self-hostable for data residency), but aimed at SaaS product integrations, not agent tool-calling; syncs/webhooks gated on free tier |
| **Arcade.dev** | **Closed-source engine** | ~100s | "On-prem" is hybrid — OAuth tokens stay in Arcade's cloud; full self-host is enterprise-only |
| **Activepieces** | MIT (Community Edition) | 400+ pieces, all exposed as MCP | Genuinely self-hostable, but it's a workflow automation tool first; agent tool-calling is a bolt-on |
| **n8n** | Fair-code | 70+ AI nodes | Workflow engine, not a tool-calling/auth layer; fair-code license limits |
| **AgentPort** | Open source (small, Show HN) | early | Positions as "Composio with granular permissions, open source" — validates the niche, but early-stage like this repo |

**Conclusion:** the *category* exists and is well-funded, but a **fully local-first agent tool platform — open runtime, credentials never leaving your machine, uncrippled catalog — does not exist**. Nango's own competitive analysis concedes this gap: vendors either gate full self-hosting behind enterprise contracts or route credentials through their clouds.

## 3. Is it needed? (demand signals)

**Validated pains from forums/analyses:**

1. **Credential sovereignty.** Regulated industries (healthcare, finance, government) cannot pass customer credentials through third-party processors; some jurisdictions require data residency. Auth0's and Nango's writing both flag that integration platforms hold three sensitive things at once: OAuth tokens/API keys, the data those APIs return, and the code that runs against them.
2. **Documented cloud-routing complaints.** Composio setups register *their* backend callback URL and capture/store tokens even in "self-hosted" configurations — exactly the thing a local-first project eliminates.
3. **The local-agent wave.** Local CLI agents (OpenCode, Claude Code, etc.) keep provider credentials in local files by design (`~/.local/share/opencode/auth.json`); users running local models via r/LocalLLaMA-style stacks have nowhere equivalent to put *tool* credentials.
4. **HN validation.** AgentPort's Show HN pitch ("like Composio but granular permissions and open source") and ACI.dev's traction after open-sourcing both show appetite for open alternatives; ACI's self-host crippling is a recurring disappointment in its discussions.

**Honest headwinds:**

- **MCP commoditization.** Every major SaaS is shipping its own MCP server. The long-term value is not "a registry of API wrappers" but the *aggregation layer*: one endpoint, one credential vault, one audit log, progressive tool disclosure (Klavis's Strata exists precisely because dumping 600 tool schemas into a context window fails).
- **Catalog is a treadmill.** Composio/ACI have teams maintaining hundreds of integrations. A solo local-first project cannot win on catalog breadth — it must win on *trust model* and on making integrations declaratively easy to add (community-contributed YAML/OpenAPI specs, not hand-written Python per app).

**Verdict: yes, needed — as "the local-first MCP tool gateway," not as "free Composio."** The wedge is the trust model (credentials never leave the machine) plus MCP-native exposure so it works with Claude Code, Cursor, etc. on day one.

## 4. Recommended direction (local-first ideas)

Ranked by leverage:

1. **Expose the registry as an MCP server** (stdio + streamable HTTP). This makes every integration instantly usable from Claude Code/Desktop, Cursor, and any MCP client — distribution for free, no SDK adoption needed. Keep the REST API for the dashboard.
2. **Encrypted local credential vault.** Replace plaintext `connections.json` with OS keychain (Windows DPAPI / macOS Keychain / Secret Service via `keyring`) or an age/Fernet-encrypted SQLite file. This is the headline feature — the current plaintext JSON undermines the entire pitch.
3. **Local OAuth loopback flow.** Support `auth_type: oauth2` properly with a `localhost` redirect URI (the way `gh auth login` works). Users bring their own OAuth client IDs; the token never touches anyone else's server. This directly attacks the #1 documented complaint about competitors.
4. **Declarative integrations.** Define apps as YAML (or generated from OpenAPI specs) instead of Python modules — auth config, endpoints, param schemas. Hand-written Python stays as an escape hatch. This is the only way catalog growth scales via community PRs.
5. **Progressive tool disclosure.** One meta-tool (`search_tools` / `execute`) so agents with small context windows don't drown in schemas — table stakes now that Strata popularized it.
6. **Audit log + permission scoping.** Per-connection allowlists of actions ("this agent may read GitHub issues but not create them") and an append-only local log of every execution. AgentPort's traction shows granular permissions resonate.
7. **Single-command run.** `pipx install open-composio && open-composio serve` or one Docker image, SQLite, zero config. Local-first dies if setup takes more than a minute.

### Immediate hygiene fixes in the current code

- `connections.py` plaintext JSON → encrypted store (see #2).
- `main.py` `allow_origins=["*"]` + unauthenticated API on 127.0.0.1 — fine for localhost dev, but add a bearer token before anyone binds it to 0.0.0.0.
- `auth_type: "oauth2"` is declared in the model but unimplemented.
- No tests, no packaging (`pyproject.toml`), no README.

## 5. Sources

- [Nango: Best self-hosted API integration platforms for AI agents](https://nango.dev/blog/best-self-hosted-api-integration-platforms-for-ai-agents/)
- [Nango: Composio alternatives](https://nango.dev/blog/composio-alternatives/)
- [ACI.dev GitHub (aipotheosis-labs/aci)](https://github.com/aipotheosis-labs/aci) · [ACI open-sourcing discussion](https://github.com/aipotheosis-labs/aci/discussions/254)
- [Klavis AI GitHub](https://github.com/Klavis-AI/klavis) · [YC launch](https://www.ycombinator.com/launches/NSs-klavis-ai-open-source-mcp-integrations-for-ai-applications)
- [Infrabase: 31 Composio alternatives](https://infrabase.ai/alternatives/composio)
- [Scalekit: Composio alternatives for tool calling](https://www.scalekit.com/blog/composio-alternatives)
- [Auth0: Handling third-party access tokens securely in AI agents](https://auth0.com/blog/third-party-access-tokens-secure-ai-agents/)
- [Composio pricing](https://composio.dev/pricing)
- Hacker News (Algolia): AgentPort Show HN ("Composio but granular permissions, open source"), Rowboat, Mercury threads referencing Composio
