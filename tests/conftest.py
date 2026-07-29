import pytest

from open_composio import OpenComposio
from open_composio.core.vault import EncryptedFileVault


@pytest.fixture
def vault(tmp_path):
    return EncryptedFileVault(directory=tmp_path)


@pytest.fixture
def oc(vault, tmp_path):
    client = OpenComposio(vault=vault, audit=False)
    client.executor._audit_path = tmp_path / "audit.jsonl"
    return client
