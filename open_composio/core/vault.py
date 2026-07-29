"""Credential vault. Secrets never leave the machine.

Two backends:

- KeyringVault (default): secrets live in the OS credential store
  (Windows Credential Manager, macOS Keychain, Linux Secret Service).
  A plaintext index file records *which* connections exist — never the
  secrets themselves — because keyrings cannot enumerate entries.
- EncryptedFileVault (fallback / headless): a single Fernet-encrypted
  JSON blob on disk. The key comes from OPEN_COMPOSIO_VAULT_KEY or a
  generated key file with owner-only permissions.
"""

import json
import os
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


class EncryptedFileVault(BaseVault):
    name = "encrypted-file"

    def __init__(self, directory: Optional[Path] = None):
        from cryptography.fernet import Fernet

        self._dir = directory or data_dir()
        self._data_path = self._dir / "vault.enc"
        self._fernet = Fernet(self._load_or_create_key())

    def _load_or_create_key(self) -> bytes:
        env_key = os.environ.get("OPEN_COMPOSIO_VAULT_KEY")
        if env_key:
            return env_key.encode()

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
        except Exception as exc:  # wrong key, corrupt file
            raise VaultError(f"Cannot decrypt vault at {self._data_path}: {exc}") from exc

    def _save_all(self, db: Dict[str, Dict[str, Any]]) -> None:
        blob = self._fernet.encrypt(json.dumps(db).encode())
        self._data_path.write_bytes(blob)

    def get(self, user_id: str, app_id: str) -> Optional[Dict[str, Any]]:
        return self._load_all().get(user_id, {}).get(app_id)

    def set(self, user_id: str, app_id: str, auth_data: Dict[str, Any]) -> None:
        db = self._load_all()
        db.setdefault(user_id, {})[app_id] = auth_data
        self._save_all(db)

    def delete(self, user_id: str, app_id: str) -> None:
        db = self._load_all()
        if user_id in db and app_id in db[user_id]:
            del db[user_id][app_id]
            self._save_all(db)

    def list_apps(self, user_id: str) -> List[str]:
        return sorted(self._load_all().get(user_id, {}).keys())


def default_vault() -> BaseVault:
    """OS keyring when a real backend is available, encrypted file otherwise.

    Force a backend with OPEN_COMPOSIO_VAULT=keyring|file.
    """
    forced = os.environ.get("OPEN_COMPOSIO_VAULT")
    if forced == "file":
        return EncryptedFileVault()
    if forced == "keyring":
        return KeyringVault()

    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring

        if not isinstance(keyring.get_keyring(), FailKeyring):
            return KeyringVault()
    except Exception:
        pass
    return EncryptedFileVault()
