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


def test_keys_are_copied_into_the_vault_then_forgotten_here(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    from app import keys

    doc = {"version": 3, "data": {"pollen_api_key": "already-there"}}
    writes: list[dict[str, object]] = []

    def vault(req: httpx.Request) -> httpx.Response:
        token = req.headers.get("X-Vault-Token")
        if req.method == "GET":
            return httpx.Response(200, json={"data": {"data": doc["data"], "metadata": {"version": doc["version"]}}})
        if token != "admin":
            return httpx.Response(403, json={"errors": ["permission denied"]})
        body = __import__("json").loads(req.content)
        writes.append(body)
        assert body["options"]["cas"] == doc["version"]  # never over a change made meanwhile
        doc["data"], doc["version"] = body["data"], doc["version"] + 1  # type: ignore[operator]
        return httpx.Response(200, json={"data": {"version": doc["version"]}})

    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "vault_token").write_text("app-login")
    (secrets / "tfwm_app_id.txt").write_text("from-a-file")
    monkeypatch.setattr(keys, "SECRETS_DIR", secrets)
    monkeypatch.setenv("WA_SECRETS_BACKEND", "vault")
    monkeypatch.setenv("WA_VAULT_ADDR", "http://vault.test:8200")
    monkeypatch.setattr(secretstore.VaultBackend, "_client", lambda self: httpx.Client(transport=httpx.MockTransport(vault)))
    secretstore.clear_cache()
    client.put("/v1/settings/keys/gridserve_api_key", json={"value": "gs-key"}, headers=H)
    listed = client.get("/v1/settings/keys", headers=H).json()
    assert listed["backend"] == "vault"

    refused = client.post("/v1/settings/keys/to-vault", json={"token": "read-only"}, headers=H)
    assert refused.status_code == 403 and "refused that token for writing" in refused.json()["detail"]
    assert (tmp_path / "gridserve_api_key.txt").exists()  # nothing forgotten

    r = client.post("/v1/settings/keys/to-vault", json={"token": "admin"}, headers=H).json()
    assert r["copied"] == ["gridserve_api_key", "tfwm_app_id"] and r["removed"] == ["gridserve_api_key"]
    assert doc["data"] == {"pollen_api_key": "already-there", "gridserve_api_key": "gs-key", "tfwm_app_id": "from-a-file"}
    assert not (tmp_path / "gridserve_api_key.txt").exists()  # the vault is where it comes from now
    secretstore.clear_cache()
    assert secretstore.resolve("gridserve_api_key", secrets) == ("gs-key", "vault")
    assert "admin" not in client.get("/v1/settings/keys", headers=H).text
    secretstore.clear_cache()
