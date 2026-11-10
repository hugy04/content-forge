"""Auth is applied at app construction, so these build their own app."""
from __future__ import annotations

import base64
import importlib

from fastapi.testclient import TestClient


def _client(monkeypatch, user: str = "", password: str = "", **env):
    monkeypatch.setenv("BASIC_AUTH_USER", user)
    monkeypatch.setenv("BASIC_AUTH_PASS", password)
    monkeypatch.setenv("FAKE_PROVIDERS", "1")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import forge.config
    forge.config.get_settings.cache_clear()
    import forge.api.app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app)


def _auth(user: str, password: str) -> dict:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_the_page_is_open_when_no_credentials_are_configured(monkeypatch, tmp_path):
    client = _client(monkeypatch, OUT_DIR=str(tmp_path / "o"), DB_PATH=str(tmp_path / "f.db"))
    assert client.get("/").status_code == 200


def test_credentials_gate_the_page_when_configured(monkeypatch, tmp_path):
    client = _client(monkeypatch, "alice", "s3cret",
                     OUT_DIR=str(tmp_path / "o"), DB_PATH=str(tmp_path / "f.db"))
    anonymous = client.get("/")
    assert anonymous.status_code == 401
    assert "Basic" in anonymous.headers.get("www-authenticate", "")

    assert client.get("/", headers=_auth("alice", "s3cret")).status_code == 200
    assert client.get("/", headers=_auth("alice", "wrong")).status_code == 401
    assert client.get("/", headers=_auth("mallory", "s3cret")).status_code == 401


def test_the_health_check_stays_open_so_uptime_monitors_still_work(monkeypatch, tmp_path):
    """A 401 on /healthz would make a gated deployment look permanently down."""
    client = _client(monkeypatch, "alice", "s3cret",
                     OUT_DIR=str(tmp_path / "o"), DB_PATH=str(tmp_path / "f.db"))
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_the_json_api_is_gated_too(monkeypatch, tmp_path):
    """Gating only the HTML page would leave the expensive endpoint open."""
    client = _client(monkeypatch, "alice", "s3cret",
                     OUT_DIR=str(tmp_path / "o"), DB_PATH=str(tmp_path / "f.db"))
    assert client.post("/v1/generate", json={"topic": "anything"}).status_code == 401
