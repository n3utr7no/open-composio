"""MCP facade: one server, three meta-tools (progressive disclosure).

Instead of registering every catalog action as a first-class MCP tool (which
floods the client's context window), agents discover tools at runtime:

    search_tools("send a message")  ->  candidates + connection status
    get_tool_schema("github_create_issue")  ->  full JSON Schema
    execute_tool("github_create_issue", {...})  ->  result

Run with `open-composio mcp` (stdio) or `oc.serve_mcp()`.
"""

from typing import Any, Dict

from mcp.server.fastmcp import FastMCP

from .core.executor import NotConnectedError, ValidationError
from .core.policy import ApprovalRequired, PolicyDenied, is_destructive


def build_mcp_server(oc) -> FastMCP:
    """Build a FastMCP server over an embedded OpenComposio instance."""
    server = FastMCP(
        "open-composio",
        instructions=(
            "Local-first tool gateway. Call search_tools with a task description "
            "to find relevant tools, get_tool_schema to see a tool's parameters, "
            "then execute_tool to run it. Credentials stay on this machine; if a "
            "tool reports it needs a connection, tell the user to run "
            "`open-composio connect <app>`. Tools marked destructive change "
            "remote state — confirm with the user before calling them."
        ),
    )
    registry = oc.registry
    vault = oc.vault

    def _connected(app_id: str) -> bool:
        app = registry.apps[app_id]
        return app.auth_type == "none" or vault.get(oc.user_id, app_id) is not None

    @server.tool()
    def search_tools(query: str, limit: int = 8) -> list:
        """Search available tools by task description (e.g. 'create a github
        issue', 'current weather'). Returns tool names, descriptions, whether
        the app is connected, and whether the tool changes remote state."""
        results = registry.search(query, limit=min(limit, 25))
        for r in results:
            app_id, action_name = registry.resolve(r["tool"])
            schema = registry.get_action(app_id, action_name).parameters_schema
            r["connected"] = _connected(app_id)
            r["destructive"] = is_destructive(action_name, schema)
            if not r["connected"]:
                r["note"] = f"Needs connection: `open-composio connect {app_id}`"
            del r["score"]
        return results

    @server.tool()
    def get_tool_schema(tool_name: str) -> Dict[str, Any]:
        """Get the full parameter JSON Schema for one tool (use the exact
        name returned by search_tools)."""
        app_id, action_name = registry.resolve(tool_name)
        action = registry.get_action(app_id, action_name)
        return {
            "tool": tool_name,
            "app_id": app_id,
            "description": action.description,
            "parameters_schema": action.parameters_schema,
            "connected": _connected(app_id),
            "destructive": is_destructive(action_name, action.parameters_schema),
        }

    @server.tool()
    async def execute_tool(tool_name: str, params: Dict[str, Any] = None) -> Any:
        """Execute a tool by its exact name with parameters matching its
        schema (see get_tool_schema)."""
        # Raise on failure so the MCP client sees isError=true instead of a
        # "successful" call whose payload happens to describe an error.
        try:
            app_id, action_name = registry.resolve(tool_name)
        except KeyError:
            raise ValueError(
                f"Tool '{tool_name}' not found. Use search_tools to find the exact tool name."
            )
        try:
            return await oc.executor.aexecute(app_id, action_name, params or {}, oc.user_id)
        except NotConnectedError:
            raise RuntimeError(
                f"App '{app_id}' is not connected. Ask the user to run: "
                f"open-composio connect {app_id}"
            )
        except ValidationError as exc:
            raise ValueError(f"Invalid parameters for '{tool_name}': {exc}")
        except (PolicyDenied, ApprovalRequired) as exc:
            raise RuntimeError(
                f"{exc} Relay this to the user — do not attempt to work around it."
            )

    return server
