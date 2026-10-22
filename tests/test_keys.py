"""Saving data-source keys from the Settings page."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import secretstore
from app.config import SECRETS_DIR, get_settings
from app.main import app

H = {"Authorization": "Bearer tok"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("API_TOKEN", "tok")
    monkeypatch.setenv("WA_KEYS_DIR", str(tmp_path))
    monkeypatch.delenv("TFL_APP_KEY", raising=False)
    get_settings.cache_clear()
    return TestClient(app)


def test_a_saved_key_is_used_at_once_and_never_shown(client: TestClient, tmp_path: Path) -> None:
    r = client.put("/v1/settings/keys/tfl_app_key", json={"value": "  abc123  "}, headers=H)
    assert r.status_code == 200 and r.json() == {"name": "tfl_app_key", "set": True, "source": "settings"}
    assert (tmp_path / "tfl_app_key.txt").read_text() == "abc123"
    assert (tmp_path / "tfl_app_key.txt").stat().st_mode & 0o777 == 0o600
    assert get_settings().tfl_app_key == "abc123"  # the app reads it straight away
    listed = {k["name"]: k for k in client.get("/v1/settings/keys", headers=H).json()["keys"]}
    assert listed["tfl_app_key"] == {"name": "tfl_app_key", "set": True, "source": "settings"}
    assert "abc123" not in client.get("/v1/settings/keys", headers=H).text


def test_forgetting_a_key_falls_back(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    client.put("/v1/settings/keys/bods_api_key", json={"value": "k"}, headers=H)
    monkeypatch.setenv("BODS_API_KEY", "from-env")
    r = client.delete("/v1/settings/keys/bods_api_key", headers=H).json()
    assert r["source"] == "env" and secretstore.read_secret("bods_api_key", SECRETS_DIR) == "from-env"


def test_only_data_source_keys_can_be_written(client: TestClient, tmp_path: Path) -> None:
    for name in ("api_token", "postgres_password", "mqtt_password", "redis_password", "vault_token", "..%2Fx"):
        assert client.put(f"/v1/settings/keys/{name}", json={"value": "x"}, headers=H).status_code == 404, name
    assert client.put("/v1/settings/keys/nr_access_token", json={"value": "a\nb"}, headers=H).status_code == 422
    assert client.put("/v1/settings/keys/nr_access_token", json={"value": "x"}).status_code == 401
    assert list(tmp_path.iterdir()) == []


def test_a_missing_or_read_only_folder_is_explained(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WA_KEYS_DIR", str(tmp_path / "nope"))
    d = client.get("/v1/settings/keys", headers=H).json()
    assert d["writable"] is False and "not mounted" in d["reason"]
    r = client.put("/v1/settings/keys/tfl_app_key", json={"value": "x"}, headers=H)
    assert r.status_code == 409
