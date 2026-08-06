"""Validation, timeouts, caching, result truncation and after-hook transforms."""

import asyncio

import pytest

from open_composio import OpenComposio
from open_composio.core.executor import ValidationError, truncate_result
from open_composio.core.vault import EncryptedFileVault


@pytest.fixture
def oc(tmp_path):
    return OpenComposio(
        vault=EncryptedFileVault(directory=tmp_path), audit=False, load_upstreams=False
    )


# ------------------------------------------------------------------ validation


def test_unexpected_param_is_actionable(oc):
    @oc.tool
    def echo(msg: str) -> str:
        """Echo."""
        return msg

    with pytest.raises(ValidationError) as exc:
        oc.execute("local", "echo", {"wrong_field": 1})
    text = str(exc.value)
    assert "wrong_field" in text and "msg" in text  # names the error AND the fix


def test_missing_required_param(oc):
    @oc.tool
    def echo(msg: str) -> str:
        """Echo."""
        return msg

    with pytest.raises(ValidationError, match="msg"):
        oc.execute("local", "echo", {})


def test_wrong_type_rejected(oc):
    @oc.tool
    def add(a: int, b: int) -> int:
        """Add."""
        return a + b

    with pytest.raises(ValidationError):
        oc.execute("local", "add", {"a": "not-a-number", "b": 2})


def test_validation_can_be_disabled(tmp_path):
    oc = OpenComposio(
        vault=EncryptedFileVault(directory=tmp_path),
        audit=False,
        load_upstreams=False,
        validate=False,
    )

    @oc.tool
    def echo(msg: str) -> str:
        """Echo."""
        return msg

    with pytest.raises(TypeError):  # reaches the handler unvalidated
        oc.execute("local", "echo", {"wrong": 1})


# -------------------------------------------------------------------- timeouts


async def test_timeout(tmp_path):
    oc = OpenComposio(
        vault=EncryptedFileVault(directory=tmp_path),
        audit=False,
        load_upstreams=False,
        timeout=0.05,
    )

    @oc.tool
    async def slow() -> str:
        """Sleep forever."""
        await asyncio.sleep(5)
        return "done"

    with pytest.raises(TimeoutError, match="timed out"):
        await oc.aexecute("local", "slow", {})


# --------------------------------------------------------------------- caching


def test_cache_hit_skips_handler(oc):
    calls = []

    @oc.tool(cache_ttl=60)
    def counter(key: str) -> int:
        """Count invocations."""
        calls.append(key)
        return len(calls)

    assert oc.execute("local", "counter", {"key": "a"}) == 1
    assert oc.execute("local", "counter", {"key": "a"}) == 1  # cached
    assert len(calls) == 1
    assert oc.execute("local", "counter", {"key": "b"}) == 2  # different params
    assert len(calls) == 2

    oc.executor.invalidate_cache()
    oc.execute("local", "counter", {"key": "a"})
    assert len(calls) == 3


def test_uncached_by_default(oc):
    calls = []

    @oc.tool
    def counter(key: str) -> int:
        """Count."""
        calls.append(key)
        return len(calls)

    oc.execute("local", "counter", {"key": "a"})
    oc.execute("local", "counter", {"key": "a"})
    assert len(calls) == 2


# ------------------------------------------------------------------ truncation


def test_truncate_result_helper():
    small = {"a": 1}
    assert truncate_result(small, 1000) == small

    big = {"data": "x" * 5000}
    out = truncate_result(big, 500)
    assert out["truncated"] is True
    assert out["original_bytes"] > 500
    assert "hint" in out


def test_large_result_truncated(tmp_path):
    oc = OpenComposio(
        vault=EncryptedFileVault(directory=tmp_path),
        audit=False,
        load_upstreams=False,
        max_result_bytes=200,
    )

    @oc.tool
    def big() -> str:
        """Return a lot."""
        return "y" * 10_000

    result = oc.execute("local", "big", {})
    assert result["truncated"] is True


# ------------------------------------------------------------------ middleware


def test_after_hook_transforms_result(oc):
    @oc.tool
    def secret() -> dict:
        """Return something sensitive."""
        return {"token": "ghp_realtoken", "ok": True}

    oc.use(after=lambda ctx, result: {**result, "token": "[REDACTED]"})
    assert oc.execute("local", "secret", {}) == {"token": "[REDACTED]", "ok": True}


def test_after_hook_returning_none_keeps_result(oc):
    @oc.tool
    def value() -> int:
        """Return a number."""
        return 42

    seen = []
    oc.use(after=lambda ctx, result: seen.append(result))  # append returns None
    assert oc.execute("local", "value", {}) == 42
    assert seen == [42]


def test_before_hook_can_block(oc):
    @oc.tool
    def anything() -> str:
        """Do a thing."""
        return "ran"

    def block(ctx):
        raise PermissionError("nope")

    oc.use(before=block)
    with pytest.raises(PermissionError):
        oc.execute("local", "anything", {})


def test_before_hook_can_mutate_params(oc):
    @oc.tool
    def echo(msg: str) -> str:
        """Echo."""
        return msg

    oc.use(before=lambda ctx: ctx.params.update(msg=ctx.params["msg"].upper()))
    assert oc.execute("local", "echo", {"msg": "quiet"}) == "QUIET"
