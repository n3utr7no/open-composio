import pytest

from open_composio.core.registry import AppDefinition, ToolRegistry


@pytest.fixture
def registry():
    reg = ToolRegistry()
    reg.register_app(AppDefinition(id="demo_app", name="Demo", description="A demo app"))

    async def echo(params, auth_data=None):
        return {"echo": params}

    reg.register_action(
        "demo_app",
        "say_hello",
        {"description": "Send a friendly hello greeting.", "type": "object", "properties": {}},
        echo,
    )
    return reg


def test_resolve_full_name_with_underscored_app_id(registry):
    assert registry.resolve("demo_app_say_hello") == ("demo_app", "say_hello")


def test_resolve_unknown_raises(registry):
    with pytest.raises(KeyError):
        registry.resolve("nope")


def test_search_finds_by_description(registry):
    results = registry.search("hello greeting")
    assert results
    assert results[0]["tool"] == "demo_app_say_hello"


async def test_execute_action(registry):
    result = await registry.execute_action("demo_app", "say_hello", {"x": 1})
    assert result == {"echo": {"x": 1}}
