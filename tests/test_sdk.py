import pytest

from open_composio import NotConnectedError


def test_builtin_apps_loaded(oc):
    ids = {a["id"] for a in oc.get_apps()}
    assert {"github", "weather", "web_search"} <= ids


def test_get_tools_adapters(oc):
    tools = oc.get_tools("github")
    names = {t.name for t in tools}
    assert names == {"github_get_user", "github_create_issue"}

    openai_defs = tools.as_openai()
    assert openai_defs[0]["type"] == "function"
    assert "parameters" in openai_defs[0]["function"]

    anthropic_defs = tools.as_anthropic()
    assert "input_schema" in anthropic_defs[0]
    assert anthropic_defs[0]["name"].startswith("github_")


def test_connect_stores_and_disconnect_removes(oc):
    oc.connect("github", token="ghp_test")
    assert oc.vault.get(oc.user_id, "github") == {"token": "ghp_test"}
    apps = {a["id"]: a for a in oc.get_apps()}
    assert apps["github"]["connected"] is True

    oc.disconnect("github")
    assert oc.vault.get(oc.user_id, "github") is None


def test_connect_validates_required_fields(oc):
    with pytest.raises(ValueError, match="token"):
        oc.connect("github")


def test_execute_unconnected_app_raises(oc):
    with pytest.raises(NotConnectedError):
        oc.execute("github", "get_user", {})


def test_local_tool_decorator(oc):
    @oc.tool
    def add(a: int, b: int) -> int:
        """Add two numbers together."""
        return a + b

    tools = oc.get_tools("local")
    assert tools[0].name == "local_add"
    schema = tools[0].schema
    assert schema["properties"]["a"]["type"] == "integer"
    assert schema["required"] == ["a", "b"]
    assert schema["description"] == "Add two numbers together."

    assert tools.call("local_add", {"a": 2, "b": 3}) == 5
    assert tools.call("local_add", '{"a": 1, "b": 1}') == 2  # JSON string args


def test_local_tool_searchable(oc):
    @oc.tool(description="Multiply two numbers.")
    def mul(a: int, b: int) -> int:
        return a * b

    results = oc.registry.search("multiply numbers")
    assert results[0]["tool"] == "local_mul"


def test_middleware_via_sdk(oc):
    calls = []
    oc.use(before=lambda ctx: calls.append(ctx.app_id))

    @oc.tool
    def ping() -> str:
        return "pong"

    oc.execute("local", "ping", {})
    assert calls == ["local"]
