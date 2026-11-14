"""Clients: a token per client with only the access it needs, revocable on its own, and the audit log."""

from __future__ import annotations

import importlib
import os
import time
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from app.clients import Client, allowed

URL = os.environ.get("WA_TEST_DATABASE_URL")


def test_access_levels_and_paths() -> None:
    read = Client(1, "HA", "read", ())
    assert allowed(read, "GET", "/v1/fuel") is None
    assert allowed(read, "POST", "/v1/places/home") == "HA may only read"
    asst = Client(2, "Claude", "assistant", ())
    assert allowed(asst, "POST", "/v1/ask") is None and allowed(asst, "POST", "/mcp") is None
    assert allowed(asst, "PUT", "/v1/ocpp/mode") == "Claude may only read and ask the assistant"
    charger = Client(3, "Charger plan", "full", ("/v1/ocpp",))
    assert allowed(charger, "PUT", "/v1/ocpp/mode") is None
    assert allowed(charger, "GET", "/v1/ocppx") == "Charger plan may use only /v1/ocpp"
    assert allowed(charger, "GET", "/v1/places") == "Charger plan may use only /v1/ocpp"


@pytest.fixture
def db() -> Iterator[str]:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mig = importlib.import_module("migrations.versions.0052_clients_audit")
    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS audit_log; DROP TABLE IF EXISTS api_client"))
        with Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()
    yield URL  # type: ignore[misc]
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS audit_log; DROP TABLE IF EXISTS api_client"))
    sync.dispose()


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
def test_a_client_is_made_used_limited_revoked_and_audited(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app import clients
    from app.main import app

    monkeypatch.setenv("API_TOKEN", "main-secret")
    monkeypatch.setattr("app.auth.is_local", lambda *a: False)  # as if from outside the home network
    monkeypatch.setattr("app.auth.tailnet_page_fetch", lambda *a: False)
    app.state.engine = create_async_engine(db, poolclass=NullPool)

    class Redis:
        async def get(self, k: str) -> str:
            return '{"problems": []}'

    app.state.redis = Redis()
    clients.forget()
    c = TestClient(app)
    main = {"Authorization": "Bearer main-secret"}
    made = c.post("/v1/clients", json={"name": "Home Assistant", "access": "read"}, headers=main).json()
    tok = {"Authorization": "Bearer " + made["token"]}
    assert made["token"].startswith("wac_") and c.post("/v1/clients", json={"name": "Home Assistant"}, headers=main).status_code == 409
    assert c.get("/v1/clients", headers=tok).status_code == 403  # a read client cannot manage clients
    assert c.get("/v1/health/watch", headers=tok).status_code == 200  # it can read
    r = c.put("/v1/ocpp/mode", json={"mode": "off"}, headers=tok)
    assert r.status_code in (403, 404) and (r.status_code == 404 or "may only read" in r.json()["detail"])
    assert c.get("/v1/clients", headers={"Authorization": "Bearer wac_wrong"}).status_code == 401
    listed = c.get("/v1/clients", headers=main).json()["clients"]
    assert listed[0]["hint"] == made["token"][-4:] and "token_hash" not in listed[0]
    assert c.post(f"/v1/clients/{made['id']}/revoke", headers=main).status_code == 200
    assert c.get("/v1/health/watch", headers=tok).status_code == 401  # revoked: refused at once
    time.sleep(0.5)  # the audit log is written in the background
    log = c.get("/v1/audit", headers=main).json()["entries"]
    who = {(e["client"], e["method"], e["status"]) for e in log}
    assert ("main token", "POST", 200) in who and ("no token", "GET", 401) in who and ("Home Assistant", "GET", 403) in who
