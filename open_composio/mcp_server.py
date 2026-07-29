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

from .core.executor import NotConnectedError


def build_mcp_server(oc) -> FastMCP:
    """Build a FastMCP server over an embedded OpenComposio instance."""
    server = FastMCP(
        "open-composio",
        instructions=(
            "Local-first tool gateway. Call search_tools with a task description "
            "to find relevant tools, get_tool_schema to see a tool's parameters, "
            "then execute_tool to run it. Credentials stay on this machine; if a "
            "tool reports it needs a connection, tell the user to run "
            "`open-composio connect <app>`."
        ),
    )
    registry = oc.registry
    vault = oc.vault

    def _connected(app_id: str) -> bool:
        app = registry.apps[app_id]
        return app.auth_type == "none" or vault.get(oc.user_id, app_id) is not None

    @server.tool()
    def search_tools(query: str) -> list:
        """Search available tools by task description (e.g. 'create a github
        issue', 'current weather'). Returns tool names, descriptions, and
        whether the app is connected and ready to use."""
        results = registry.search(query, limit=8)
        for r in results:
            r["connected"] = _connected(r["app_id"])
            if not r["connected"]:
                r["note"] = f"Needs connection: `open-composio connect {r['app_id']}`"
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
        }

    @server.tool()
    async def execute_tool(tool_name: str, params: Dict[str, Any] = None) -> Any:
        """Execute a tool by its exact name with parameters matching its
        schema (see get_tool_schema)."""
        try:
            app_id, action_name = registry.resolve(tool_name)
        except KeyError as exc:
            return {"error": str(exc), "hint": "Use search_tools to find the exact tool name."}
        try:
            result = await oc.executor.aexecute(app_id, action_name, params or {}, oc.user_id)
            return {"status": "success", "result": result}
        except NotConnectedError as exc:
            return {
                "error": str(exc),
                "needs_connection": app_id,
                "hint": f"Ask the user to run: open-composio connect {app_id}",
            }
        except Exception as exc:
            return {"error": str(exc)}

    return server
