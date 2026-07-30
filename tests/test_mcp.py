"""Smoke tests for the MCP facade using the in-memory FastMCP call path."""

import pytest

from open_composio.mcp_server import build_mcp_server


@pytest.fixture
def server(oc):
    @oc.tool
    def shout(text: str) -> str:
        """Uppercase a piece of text loudly."""
        return text.upper()

    return build_mcp_server(oc)


async def test_meta_tools_registered(server):
    tools = await server.list_tools()
    assert {t.name for t in tools} == {"search_tools", "get_tool_schema", "execute_tool"}


async def test_search_then_schema_then_execute(server):
    results = await server.call_tool("search_tools", {"query": "uppercase text loudly"})
    payload = results[0][0].text if isinstance(results, tuple) else results[0].text
    assert "local_shout" in payload

    schema = await server.call_tool("get_tool_schema", {"tool_name": "local_shout"})
    text = schema[0][0].text if isinstance(schema, tuple) else schema[0].text
    assert "parameters_schema" in text

    result = await server.call_tool(
        "execute_tool", {"tool_name": "local_shout", "params": {"text": "hi"}}
    )
    text = result[0][0].text if isinstance(result, tuple) else result[0].text
    assert "HI" in text


async def test_execute_unknown_tool_raises_with_hint(server):
    with pytest.raises(Exception, match="search_tools"):
        await server.call_tool("execute_tool", {"tool_name": "does_not_exist", "params": {}})


async def test_execute_unconnected_app_raises_with_connect_hint(server):
    with pytest.raises(Exception, match="open-composio connect github"):
        await server.call_tool(
            "execute_tool", {"tool_name": "github_get_user", "params": {}}
        )
