"""Mounting third-party MCP servers.

The end-to-end path is exercised against a real stdio MCP server built with
FastMCP and launched as a subprocess, so discovery and proxying are tested for
real rather than mocked.
"""

import sys
import textwrap

import pytest

from open_composio import OpenComposio
from open_composio.core.vault import EncryptedFileVault
from open_composio.upstream import UpstreamManager, _result_to_payload

FAKE_SERVER = textwrap.dedent(
    '''
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("fake-upstream")

    @server.tool()
    def shout(text: str) -> str:
        """Return the text in upper case."""
        return text.upper()

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    server.run(transport="stdio")
    '''
)


@pytest.fixture
def oc(tmp_path):
    client = OpenComposio(
        vault=EncryptedFileVault(directory=tmp_path), audit=False, load_upstreams=False
    )
    client.upstreams = UpstreamManager(
        client.registry, client.vault, client.user_id, directory=tmp_path
    )
    return client


@pytest.fixture
def fake_server(tmp_path):
    path = tmp_path / "fake_server.py"
    path.write_text(FAKE_SERVER, encoding="utf-8")
    return path


def test_add_and_list(oc):
    oc.upstreams.add("demo", "echo", ["hi"], env_keys=["DEMO_TOKEN"])
    listed = oc.upstreams.list()
    assert listed["demo"].command == "echo"
    assert listed["demo"].env_keys == ["DEMO_TOKEN"]


def test_add_rejects_colliding_app_id(oc):
    with pytest.raises(ValueError, match="collides"):
        oc.upstreams.add("github", "echo", [])


def test_remove_clears_registry(oc):
    oc.upstreams.add("demo", "echo", [])
    oc.upstreams.register(oc.upstreams.list()["demo"], [
        {"name": "t", "description": "d", "parameters_schema": {"type": "object", "properties": {}}}
    ])
    assert "demo" in oc.registry.apps
    oc.upstreams.remove("demo")
    assert "demo" not in oc.registry.apps
    assert "demo" not in oc.upstreams.list()


def test_result_payload_parsing():
    class Item:
        def __init__(self, text):
            self.text = text

    class Result:
        def __init__(self, texts):
            self.content = [Item(t) for t in texts]

    assert _result_to_payload(Result(['{"a": 1}'])) == {"a": 1}
    assert _result_to_payload(Result(["plain text"])) == "plain text"


@pytest.mark.slow
async def test_discover_and_proxy_real_server(oc, fake_server):
    oc.upstreams.add("fake", sys.executable, [str(fake_server)])

    tools = await oc.upstreams.discover("fake")
    names = {t["name"] for t in tools}
    assert {"shout", "add"} <= names

    # Discovered tools land in the registry as a normal app...
    assert "fake" in oc.registry.apps
    assert "shout" in oc.registry.apps["fake"].actions

    # ...and execute through the standard pipeline (validation, audit, policy).
    assert await oc.aexecute("fake", "shout", {"text": "hello"}) == "HELLO"
    assert await oc.aexecute("fake", "add", {"a": 2, "b": 3}) == 5

    # They are searchable through the same meta-tool surface.
    hits = [r["tool"] for r in oc.registry.search("upper case text", limit=5)]
    assert "fake_shout" in hits


@pytest.mark.slow
async def test_load_all_uses_cache_without_spawning(oc, fake_server):
    oc.upstreams.add("fake", sys.executable, [str(fake_server)])
    await oc.upstreams.discover("fake")

    # A fresh registry + manager sharing the cache dir registers from disk.
    fresh = OpenComposio(vault=oc.vault, audit=False, load_upstreams=False)
    manager = UpstreamManager(
        fresh.registry, fresh.vault, fresh.user_id, directory=oc.upstreams._dir
    )
    manager.load_all()
    assert "fake" in fresh.registry.apps
    assert "shout" in fresh.registry.apps["fake"].actions
