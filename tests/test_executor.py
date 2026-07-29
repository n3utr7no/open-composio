import json

import pytest

from open_composio.core.executor import Executor, NotConnectedError, PermissionDenied
from open_composio.core.registry import AppDefinition, ToolRegistry
from open_composio.core.vault import EncryptedFileVault


@pytest.fixture
def setup(tmp_path):
    reg = ToolRegistry()
    reg.register_app(AppDefinition(id="open", name="Open", description="no auth"))
    reg.register_app(
        AppDefinition(id="locked", name="Locked", description="needs key", auth_type="api_key")
    )

    async def echo(params, auth_data=None):
        return {"params": params, "auth": auth_data}

    schema = {"description": "echo", "type": "object", "properties": {}}
    reg.register_action("open", "echo", schema, echo)
    reg.register_action("locked", "echo", schema, echo)

    vault = EncryptedFileVault(directory=tmp_path)
    executor = Executor(reg, vault, audit_path=tmp_path / "audit.jsonl")
    return reg, vault, executor, tmp_path


async def test_no_auth_app_executes(setup):
    _, _, executor, _ = setup
    result = await executor.aexecute("open", "echo", {"a": 1})
    assert result["params"] == {"a": 1}
    assert result["auth"] is None


async def test_locked_app_requires_connection(setup):
    _, _, executor, _ = setup
    with pytest.raises(NotConnectedError):
        await executor.aexecute("locked", "echo", {})


async def test_locked_app_gets_auth_from_vault(setup):
    _, vault, executor, _ = setup
    vault.set("default_user", "locked", {"key": "k"})
    result = await executor.aexecute("locked", "echo", {})
    assert result["auth"] == {"key": "k"}


async def test_before_middleware_can_deny(setup):
    _, _, executor, _ = setup

    def deny(ctx):
        raise PermissionDenied(f"{ctx.app_id}.{ctx.action} blocked")

    executor.use(before=deny)
    with pytest.raises(PermissionDenied):
        await executor.aexecute("open", "echo", {})


async def test_after_middleware_sees_result(setup):
    _, _, executor, _ = setup
    seen = []
    executor.use(after=lambda ctx, result: seen.append((ctx.action, result)))
    await executor.aexecute("open", "echo", {"x": 2})
    assert seen and seen[0][0] == "echo"


async def test_audit_log_written_without_raw_params(setup):
    _, _, executor, audit_dir = setup
    await executor.aexecute("open", "echo", {"secret_param": "hunter2"})
    lines = (audit_dir / "audit.jsonl").read_text().strip().splitlines()
    record = json.loads(lines[-1])
    assert record["status"] == "ok"
    assert record["app_id"] == "open"
    assert "hunter2" not in json.dumps(record)
