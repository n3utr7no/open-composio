import pytest
from fastapi.testclient import TestClient

from open_composio import OpenComposio
from open_composio.core.vault import EncryptedFileVault
from open_composio.rest import create_app


@pytest.fixture
def client(tmp_path):
    oc = OpenComposio(vault=EncryptedFileVault(directory=tmp_path))
    oc.executor._audit_path = tmp_path / "audit.jsonl"

    @oc.tool
    def greet(name: str) -> str:
        """Greet someone by name."""
        return f"hello {name}"

    return TestClient(create_app(oc))


def test_list_apps(client):
    apps = {a["id"]: a for a in client.get("/api/apps").json()["apps"]}
    assert "github" in apps
    assert apps["github"]["connected"] is False
    assert apps["github"]["requires_auth"] is True


def test_connection_lifecycle(client):
    resp = client.post("/api/connections/github", json={"token": "ghp_x"})
    assert resp.status_code == 200
    apps = {a["id"]: a for a in client.get("/api/apps").json()["apps"]}
    assert apps["github"]["connected"] is True

    assert client.delete("/api/connections/github").status_code == 200
    apps = {a["id"]: a for a in client.get("/api/apps").json()["apps"]}
    assert apps["github"]["connected"] is False


def test_connect_missing_field_is_422(client):
    assert client.post("/api/connections/github", json={}).status_code == 422


def test_execute_local_tool_and_audit(client):
    resp = client.post("/api/execute/local/greet", json={"params": {"name": "ada"}})
    assert resp.status_code == 200
    assert resp.json()["result"] == "hello ada"

    records = client.get("/api/audit").json()["records"]
    assert records[0]["app_id"] == "local"
    assert records[0]["action"] == "greet"
    assert records[0]["status"] == "ok"
    assert "ada" not in str(records)  # raw params never appear in the audit log


def test_execute_unconnected_is_401(client):
    resp = client.post("/api/execute/github/get_user", json={"params": {}})
    assert resp.status_code == 401


def test_unknown_app_is_404(client):
    assert client.get("/api/apps/nope/actions").status_code == 404


def test_bearer_token_enforced(client, monkeypatch):
    monkeypatch.setenv("OPEN_COMPOSIO_API_TOKEN", "s3cret")
    assert client.get("/api/apps").status_code == 401
    assert client.get("/api/audit").status_code == 401
    assert (
        client.get("/api/apps", headers={"Authorization": "Bearer wrong"}).status_code == 401
    )
    assert (
        client.get("/api/apps", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    )


def test_dashboard_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
