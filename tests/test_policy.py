import pytest

from open_composio import OpenComposio
from open_composio.core.policy import (
    ApprovalRequired,
    Policy,
    PolicyDenied,
    is_destructive,
)
from open_composio.core.vault import EncryptedFileVault


def make_client(tmp_path, policy):
    oc = OpenComposio(
        vault=EncryptedFileVault(directory=tmp_path),
        audit=False,
        policy=policy,
        load_policy_file=False,
        load_upstreams=False,
    )

    @oc.tool(destructive=True)
    def wipe(target: str) -> str:
        """Delete everything."""
        return f"wiped {target}"

    @oc.tool(destructive=False)
    def peek(target: str) -> str:
        """Read something."""
        return f"peeked {target}"

    return oc


def test_destructive_heuristic():
    assert is_destructive("create_issue")
    assert is_destructive("delete_repo")
    assert is_destructive("send_message")
    assert not is_destructive("get_user")
    assert not is_destructive("search")
    # explicit annotation always wins over the verb heuristic
    assert not is_destructive("create_issue", {"x-destructive": False})
    assert is_destructive("get_user", {"x-destructive": True})


def test_deny_blocks(tmp_path):
    oc = make_client(tmp_path, Policy(deny=["local.peek"]))
    with pytest.raises(PolicyDenied):
        oc.execute("local", "peek", {"target": "x"})


def test_allowlist_excludes_everything_else(tmp_path):
    oc = make_client(tmp_path, Policy(allow=["local.peek"]))
    assert oc.execute("local", "peek", {"target": "x"}) == "peeked x"
    with pytest.raises(PolicyDenied):
        oc.execute("local", "wipe", {"target": "x"})


def test_destructive_needs_approval(tmp_path):
    oc = make_client(tmp_path, Policy())
    with pytest.raises(ApprovalRequired):
        oc.execute("local", "wipe", {"target": "x"})
    # non-destructive is unaffected
    assert oc.execute("local", "peek", {"target": "x"}) == "peeked x"


def test_preapproved_destructive_runs(tmp_path):
    oc = make_client(tmp_path, Policy(approved=["local.wipe"]))
    assert oc.execute("local", "wipe", {"target": "x"}) == "wiped x"


def test_approval_handler_decision(tmp_path):
    granted = Policy(approval_handler=lambda a, b, p: True)
    assert make_client(tmp_path, granted).execute("local", "wipe", {"target": "x"}) == "wiped x"

    refused = Policy(approval_handler=lambda a, b, p: False)
    with pytest.raises(ApprovalRequired):
        make_client(tmp_path, refused).execute("local", "wipe", {"target": "x"})


def test_wildcards(tmp_path):
    oc = make_client(tmp_path, Policy(deny=["local.*"]))
    with pytest.raises(PolicyDenied):
        oc.execute("local", "peek", {"target": "x"})


def test_no_policy_is_permissive(tmp_path):
    oc = make_client(tmp_path, None)
    assert oc.execute("local", "wipe", {"target": "x"}) == "wiped x"


def test_roundtrip(tmp_path):
    path = tmp_path / "policy.json"
    Policy(allow=["a.b"], deny=["c.*"], approved=["d.e"]).save(path)
    loaded = Policy.load(path)
    assert loaded.allow == ["a.b"] and loaded.deny == ["c.*"] and loaded.approved == ["d.e"]
    assert Policy.load(tmp_path / "missing.json") is None
