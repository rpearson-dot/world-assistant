"""The MQTT broker on the Settings page: shown, changed, encrypted or not, tested, and back to .env."""

from __future__ import annotations

import os
import ssl
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app import mqtt_config
from app.config import get_settings
from app.main import app
from app.mqtt_config import MqttConf, explain, from_env, merge

URL = os.environ.get("WA_TEST_DATABASE_URL")
H = {"Authorization": "Bearer tok"}
needs_db = pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("API_TOKEN", "tok")
    monkeypatch.setenv("WA_MQTT_HOST", "192.168.1.10")
    monkeypatch.setenv("WA_MQTT_USER", "envuser")
    monkeypatch.setenv("WA_KEYS_DIR", str(tmp_path))
    monkeypatch.delenv("MQTT_PASSWORD", raising=False)
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def table() -> Iterator[None]:
    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS app_setting"))
        conn.execute(text("CREATE TABLE app_setting (key text primary key, value jsonb not null,"
                          " updated_at timestamptz not null default now())"))  # fmt: skip
    yield
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS app_setting"))
    sync.dispose()


def test_saved_settings_win_over_env(env: Path) -> None:
    base = from_env()
    assert (base.host, base.port, base.user, base.tls) == ("192.168.1.10", 1883, "envuser", False)
    saved = merge(base, {"host": "mqtt.example.com", "port": 8883, "user": "wa", "tls": True})
    assert (saved.host, saved.port, saved.user, saved.tls) == ("mqtt.example.com", 8883, "wa", True)
    assert merge(base, None) == base and merge(base, {"host": ""}).active is False


@needs_db
def test_change_the_broker_and_go_back(env: Path, table: None) -> None:
    app.state.engine = create_async_engine(URL, poolclass=NullPool)  # type: ignore[arg-type]
    c = TestClient(app)
    first = c.get("/v1/settings/mqtt", headers=H).json()
    assert first["source"] == "env" and first["host"] == "192.168.1.10" and first["tls"] is False
    r = c.put("/v1/settings/mqtt", headers=H,
              json={"host": "mqtt.example.com", "port": 8883, "user": "wa", "tls": True, "password": "s3cret"}).json()  # fmt: skip
    assert (r["source"], r["host"], r["port"], r["tls"], r["password_set"]) == ("settings", "mqtt.example.com", 8883, True, True)
    assert r["password_source"] == "settings" and "s3cret" not in c.get("/v1/settings/mqtt", headers=H).text
    assert (env / "mqtt_password.txt").read_text() == "s3cret" and get_settings().mqtt_password == "s3cret"
    # turning encryption off keeps the rest
    off = c.put("/v1/settings/mqtt", headers=H, json={"host": "mqtt.example.com", "port": 1883, "user": "wa", "tls": False}).json()
    assert off["tls"] is False and off["password_set"] is True  # no password given: the saved one stays
    back = c.delete("/v1/settings/mqtt", headers=H).json()
    assert back["source"] == "env" and back["host"] == "192.168.1.10" and not (env / "mqtt_password.txt").exists()


def test_bad_input_is_refused(env: Path) -> None:
    c = TestClient(app)
    for host in ("mqtt://broker", "broker:1883", "a b", "x" * 300):
        assert c.post("/v1/settings/mqtt/test", headers=H, json={"host": host}).status_code == 422, host
    assert c.post("/v1/settings/mqtt/test", headers=H, json={"host": "b", "port": 70000}).status_code == 422
    assert c.post("/v1/settings/mqtt/test", json={"host": "b"}).status_code == 401  # TestClient is not local


def test_a_connection_test_says_what_is_wrong(env: Path) -> None:
    c = TestClient(app)
    r = c.post("/v1/settings/mqtt/test", headers=H, json={"host": "127.0.0.1", "port": 1, "tls": False}).json()
    assert r["ok"] is False and "nothing is listening on 127.0.0.1:1" in r["error"]
    assert c.post("/v1/settings/mqtt/test", headers=H, json={"host": ""}).json()["error"] == "no server given"


def test_failures_in_words() -> None:
    conf = MqttConf("mqtt.example.com", 8883, "wa", True)
    wrong_name = ssl.SSLCertVerificationError("certificate verify failed: Hostname mismatch, certificate is not valid for 'x'")
    assert "not for mqtt.example.com" in explain(wrong_name, conf)
    assert "not trusted" in explain(ssl.SSLCertVerificationError("certificate verify failed: self-signed certificate"), conf)
    assert "TLS port" in explain(ssl.SSLError("[SSL: WRONG_VERSION_NUMBER] wrong version number"), conf)
    assert "user name or password" in explain(Exception("Connection Refused: not authorised."), conf)
    assert "could not be found" in explain(OSError("[Errno -2] Name or service not known"), conf)


@pytest.mark.asyncio
async def test_the_worker_uses_the_saved_broker(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def saved(engine: object) -> tuple[dict[str, object], str]:
        return {"host": "mqtt.example.com", "port": 8883, "user": "wa", "tls": True}, "2026-10-22T18:00:00+00:00"

    monkeypatch.setattr(mqtt_config, "_saved", saved)
    conf, source = await mqtt_config.load(object())
    assert source == "settings" and conf.tls and conf.port == 8883 and conf.user == "wa"
