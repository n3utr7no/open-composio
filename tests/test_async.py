"""The SDK's tool API must work inside a running event loop — that is where
every async agent framework calls it from."""

import asyncio

import pytest

from open_composio import OpenComposio
from open_composio.core.vault import EncryptedFileVault


@pytest.fixture
def oc(tmp_path):
    client = OpenComposio(
        vault=EncryptedFileVault(directory=tmp_path), audit=False, load_upstreams=False
    )

    @client.tool
    def echo(msg: str) -> str:
        """Echo a message."""
        return msg

    @client.tool
    async def aecho(msg: str) -> str:
        """Echo a message from an async handler."""
        await asyncio.sleep(0)
        return msg

    return client


async def test_acall_inside_event_loop(oc):
    tools = oc.get_tools("local")
    assert await tools.acall("local_echo", {"msg": "hi"}) == "hi"
    assert await tools.acall("local_aecho", {"msg": "hi"}) == "hi"


async def test_tool_acall_direct(oc):
    tool = oc.get_tools("local").get("local_echo")
    assert await tool.acall({"msg": "x"}) == "x"


async def test_acall_accepts_json_string_arguments(oc):
    tools = oc.get_tools("local")
    assert await tools.acall("local_echo", '{"msg": "from-json"}') == "from-json"


def test_sync_call_still_works(oc):
    tools = oc.get_tools("local")
    assert tools.call("local_echo", {"msg": "hi"}) == "hi"


async def test_aexecute_inside_event_loop(oc):
    assert await oc.aexecute("local", "echo", {"msg": "direct"}) == "direct"
