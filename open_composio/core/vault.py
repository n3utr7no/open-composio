"""Credential vault. Secrets never leave the machine.

Two backends:

- KeyringVault (default): secrets live in the OS credential store
  (Windows Credential Manager, macOS Keychain, Linux Secret Service).
  A plaintext index file records *which* connections exist — never the
  secrets themselves — because keyrings cannot enumerate entries.
- EncryptedFileVault (fallback / headless): a single Fernet-encrypted
  JSON blob on disk.

EncryptedFileVault key material, strongest first:

1. OPEN_COMPOSIO_VAULT_KEY — a raw Fernet key you manage yourself.
2. OPEN_COMPOSIO_VAULT_PASSPHRASE — key derived via scrypt; only a random
   salt is stored on disk, so the ciphertext is useless without the
   passphrase.
3. A generated key file next to the ciphertext (default). This protects
   against accidental disclosure — greps, backups, pasted directory
   listings — but NOT against an attacker who can read the whole
   directory. Use a passphrase if that is in your threat model.
"""

import base64
import contextlib
import json
import os
import time
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

from .paths import data_dir

SERVICE_NAME = "open-composio"


class VaultError(Exception):
    pass


class BaseVault:
    name = "base"

    def get(self, user_id: str, app_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def set(self, user_id: str, app_id: str, auth_data: Dict[str, Any]) -> None:
        raise NotImplementedError

    def delete(self, user_id: str, app_id: str) -> None:
        raise NotImplementedError

    def list_apps(self, user_id: str) -> List[str]:
        raise NotImplementedError


class KeyringVault(BaseVault):
    name = "keyring"

    def __init__(self, index_path: Optional[Path] = None):
        import keyring  # noqa: F401 — fail fast if unavailable

        self._index_path = index_path or data_dir() / "connections_index.json"

    def _key(self, user_id: str, app_id: str) -> str:
        return f"{user_id}:{app_id}"

    def _load_index(self) -> Dict[str, List[str]]:
        try:
            return json.loads(self._index_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_index(self, index: Dict[str, List[str]]) -> None:
        self._index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")

    def get(self, user_id: str, app_id: str) -> Optional[Dict[str, Any]]:
        import keyring

        raw = keyring.get_password(SERVICE_NAME, self._key(user_id, app_id))
        return json.loads(raw) if raw else None

    def set(self, user_id: str, app_id: str, auth_data: Dict[str, Any]) -> None:
        import keyring

        keyring.set_password(SERVICE_NAME, self._key(user_id, app_id), json.dumps(auth_data))
        index = self._load_index()
        apps = set(index.get(user_id, []))
        apps.add(app_id)
        index[user_id] = sorted(apps)
        self._save_index(index)

    def delete(self, user_id: str, app_id: str) -> None:
        import keyring
        import keyring.errors

        try:
            keyring.delete_password(SERVICE_NAME, self._key(user_id, app_id))
        except keyring.errors.PasswordDeleteError:
            pass
        index = self._load_index()
        if user_id in index and app_id in index[user_id]:
            index[user_id].remove(app_id)
            self._save_index(index)

    def list_apps(self, user_id: str) -> List[str]:
        return self._load_index().get(user_id, [])


@contextlib.contextmanager
def _file_lock(path: Path, timeout: float = 5.0):
    """Cross-platform advisory lock via an O_EXCL lock file. Guards the
    read-modify-write cycle so concurrent processes don't lose updates."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            # A crashed process may have left the lock behind; steal locks
            # older than the timeout rather than deadlocking forever.
            try:
                if time.time() - lock_path.stat().st_mtime > timeout:
                    lock_path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise VaultError(f"Timed out waiting for vault lock {lock_path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        with contextlib.suppress(OSError):
            lock_path.unlink()


class EncryptedFileVault(BaseVault):
    name = "encrypted-file"

    def __init__(self, directory: Optional[Path] = None):
        from cryptography.fernet import Fernet

        self._dir = directory or data_dir()
        self._data_path = self._dir / "vault.enc"
        self._fernet = Fernet(self._load_or_create_key())

    def _derive_from_passphrase(self, passphrase: str) -> bytes:
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

        salt_path = self._dir / "vault.salt"
        if salt_path.exists():
            salt = salt_path.read_bytes()
        else:
            salt = os.urandom(16)
            salt_path.write_bytes(salt)
        kdf = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1)
        return base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))

    def _load_or_create_key(self) -> bytes:
        env_key = os.environ.get("OPEN_COMPOSIO_VAULT_KEY")
        if env_key:
            return env_key.encode()

        passphrase = os.environ.get("OPEN_COMPOSIO_VAULT_PASSPHRASE")
        if passphrase:
            return self._derive_from_passphrase(passphrase)

        key_path = self._dir / "vault.key"
        if key_path.exists():
            return key_path.read_bytes().strip()

        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        key_path.write_bytes(key)
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass  # best effort; not meaningful on Windows
        return key

    def _load_all(self) -> Dict[str, Dict[str, Any]]:
        if not self._data_path.exists():
            return {}
        try:
            plaintext = self._fernet.decrypt(self._data_path.read_bytes())
            return json.loads(plaintext)
        except Exception as exc:  # wrong key/passphrase, corrupt file
            raise VaultError(
                f"Cannot decrypt vault at {self._data_path}: {exc}. "
                "If you use OPEN_COMPOSIO_VAULT_PASSPHRASE, check it matches "
                "the one the vault was created with."
            ) from exc

    def _save_all(self, db: Dict[str, Dict[str, Any]]) -> None:
        blob = self._fernet.encrypt(json.dumps(db).encode())
        tmp_path = self._data_path.with_suffix(".tmp")
        tmp_path.write_bytes(blob)
        os.replace(tmp_path, self._data_path)

    def get(self, user_id: str, app_id: str) -> Optional[Dict[str, Any]]:
        return self._load_all().get(user_id, {}).get(app_id)

    def set(self, user_id: str, app_id: str, auth_data: Dict[str, Any]) -> None:
        with _file_lock(self._data_path):
            db = self._load_all()
            db.setdefault(user_id, {})[app_id] = auth_data
            self._save_all(db)

    def delete(self, user_id: str, app_id: str) -> None:
        with _file_lock(self._data_path):
            db = self._load_all()
            if user_id in db and app_id in db[user_id]:
                del db[user_id][app_id]
                self._save_all(db)

    def list_apps(self, user_id: str) -> List[str]:
        return sorted(self._load_all().get(user_id, {}).keys())


def default_vault() -> BaseVault:
    """OS keyring when a real backend is available, encrypted file otherwise.

    Force a backend with OPEN_COMPOSIO_VAULT=keyring|file. Falling back is
    never silent: if the keyring stops being available, connections made
    through it would otherwise just "disappear".
    """
    forced = os.environ.get("OPEN_COMPOSIO_VAULT")
    if forced == "file":
        return EncryptedFileVault()
    if forced == "keyring":
        return KeyringVault()

    reason = None
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring

        backend = keyring.get_keyring()
        if not isinstance(backend, FailKeyring):
            return KeyringVault()
        reason = "no usable keyring backend found"
    except Exception as exc:
        reason = str(exc)

    warnings.warn(
        f"OS keyring unavailable ({reason}); falling back to the encrypted-file "
        f"vault in {data_dir()}. Connections stored in the keyring will appear "
        "disconnected until it is available again. Set OPEN_COMPOSIO_VAULT=file "
        "to silence this warning, and consider OPEN_COMPOSIO_VAULT_PASSPHRASE.",
        RuntimeWarning,
        stacklevel=2,
    )
    return EncryptedFileVault()
