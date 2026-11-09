"""The automatic-update switch: stored flag, last-run report, token required, shown on /status."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.main import app

URL = os.environ.get("WA_TEST_DATABASE_URL")
H = {"Authorization": "Bearer s3cret"}


@pytest.fixture
def table() -> Iterator[None]:
    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS app_setting"))
        conn.execute(
            text(
                "CREATE TABLE app_setting (key text primary key, value jsonb not null,"
                " updated_at timestamptz not null default now())"
            )
        )
    yield
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS app_setting"))
    sync.dispose()


def test_token_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "s3cret")
    c = TestClient(app)
    assert c.get("/v1/auto-deploy").status_code == 401
    assert c.put("/v1/auto-deploy", json={"enabled": True}).status_code == 401
    assert c.post("/v1/auto-deploy/report", json={"result": "updated"}).status_code == 401


def test_the_report_rejects_unknown_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "s3cret")
    r = TestClient(app).post("/v1/auto-deploy/report", json={"result": "great"}, headers=H)
    assert r.status_code == 422


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
def test_switch_and_report_round_trip(monkeypatch: pytest.MonkeyPatch, table: None) -> None:
    monkeypatch.setenv("API_TOKEN", "s3cret")
    app.state.engine = create_async_engine(URL, poolclass=NullPool)  # type: ignore[arg-type]
    c = TestClient(app)
    first = c.get("/v1/auto-deploy", headers=H).json()
    assert first["enabled"] is False and first["last_run"] is None
    assert c.put("/v1/auto-deploy", json={"enabled": True}, headers=H).json()["enabled"] is True
    assert c.get("/v1/auto-deploy", headers=H).json()["enabled"] is True
    body = {"result": "rolled_back", "message": "smoke failed", "from_version": "0.5.0", "to_version": "0.6.0"}
    d = c.post("/v1/auto-deploy/report", json=body, headers=H).json()
    assert d["last_run"]["result"] == "rolled_back" and d["last_run"]["to_version"] == "0.6.0"
    assert c.put("/v1/auto-deploy", json={"enabled": False}, headers=H).json()["enabled"] is False


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
def test_update_now_is_asked_claimed_and_cleared_by_the_report(monkeypatch: pytest.MonkeyPatch, table: None) -> None:
    from datetime import UTC, datetime, timedelta

    from app import autodeploy as AD

    monkeypatch.setenv("API_TOKEN", "s3cret")
    app.state.engine = create_async_engine(URL, poolclass=NullPool)  # type: ignore[arg-type]
    c = TestClient(app)
    assert c.post("/v1/auto-deploy/request/claim", headers=H).status_code == 409  # nothing asked
    d = c.post("/v1/auto-deploy/request", headers=H).json()
    asked = d["request"]["asked"]
    assert d["pending"] is True and d["request"]["claimed"] is None
    assert c.post("/v1/auto-deploy/request", headers=H).json()["request"]["asked"] == asked  # asking again: the same one
    d = c.post("/v1/auto-deploy/request/claim", headers=H).json()
    assert d["pending"] is False and d["request"]["claimed"]
    assert c.post("/v1/auto-deploy/request/claim", headers=H).status_code == 409  # taken once
    assert c.delete("/v1/auto-deploy/request", headers=H).status_code == 409  # too late to cancel
    d = c.post("/v1/auto-deploy/report", json={"result": "updated", "to_version": "9.9.9"}, headers=H).json()
    assert d["request"] is None and d["last_run"]["result"] == "updated"
    # cancelling before it is picked up
    c.post("/v1/auto-deploy/request", headers=H)
    assert c.delete("/v1/auto-deploy/request", headers=H).json()["request"] is None
    # a claim that never reported counts as over
    old = (datetime.now(UTC) - timedelta(seconds=AD.STALE_S + 60)).isoformat()
    assert AD._ask_view({"asked": old, "claimed": old}, datetime.now(UTC)) is None
    assert AD._ask_view({"asked": old, "claimed": None}, datetime.now(UTC)) == {"asked": old, "claimed": None}


def test_status_page_has_the_switch() -> None:
    html = TestClient(app).get("/status").text
    assert 'id="au"' in html and "/v1/auto-deploy" in html and 'id="up"' in html and "/v1/auto-deploy/request" in html
