import pytest

from open_composio.core.vault import EncryptedFileVault, VaultError


def test_roundtrip(tmp_path):
    vault = EncryptedFileVault(directory=tmp_path)
    vault.set("u1", "github", {"token": "secret123"})
    assert vault.get("u1", "github") == {"token": "secret123"}
    assert vault.list_apps("u1") == ["github"]

    vault.delete("u1", "github")
    assert vault.get("u1", "github") is None
    assert vault.list_apps("u1") == []


def test_secrets_not_plaintext_on_disk(tmp_path):
    vault = EncryptedFileVault(directory=tmp_path)
    vault.set("u1", "github", {"token": "supersecrettoken"})
    blob = (tmp_path / "vault.enc").read_bytes()
    assert b"supersecrettoken" not in blob


def test_persists_across_instances(tmp_path):
    EncryptedFileVault(directory=tmp_path).set("u1", "github", {"token": "t"})
    assert EncryptedFileVault(directory=tmp_path).get("u1", "github") == {"token": "t"}


def test_wrong_key_raises(tmp_path, monkeypatch):
    EncryptedFileVault(directory=tmp_path).set("u1", "github", {"token": "t"})
    (tmp_path / "vault.key").unlink()  # force a fresh key next time
    with pytest.raises(VaultError):
        EncryptedFileVault(directory=tmp_path).get("u1", "github")
