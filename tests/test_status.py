"""Auth tests run anywhere. The data tests need WA_TEST_DATABASE_URL (see test_nh_store)."""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.integrations.nationalhighways.store import save_events, upsert_corridor
from app.jobs import tracked
from app.main import app
from tests.test_nh_store import NEAR, _event

URL = os.environ.get("WA_TEST_DATABASE_URL")
VERSIONS = Path(__file__).parent.parent / "migrations" / "versions"
MIGRATIONS = ["0002_raw_capture", "0003_nh_storage", "0004_job_run", "0007_fuel"]
TABLES = ["fuel_price_change", "fuel_price", "fuel_station", "job_run", "raw_capture", "nh_sign_message", "nh_sign", "nh_event_corridor",
          "nh_event", "nh_corridor"]  # fmt: skip

client = TestClient(app)


def test_status_closed_when_no_token_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_TOKEN", raising=False)
    assert client.get("/v1/status").status_code == 503


def test_status_rejects_missing_or_wrong_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "s3cret")
    assert client.get("/v1/status").status_code == 401
    assert client.get("/v1/status", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/v1/status", headers={"Authorization": "Basic s3cret"}).status_code == 401


def test_status_page_loads_without_token() -> None:
    r = client.get("/status")
    assert r.status_code == 200
    assert "World Assistant status" in r.text


def _migrate(direction: str) -> None:
    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    names = MIGRATIONS if direction == "upgrade" else list(reversed(MIGRATIONS))
    with sync.begin() as conn:
        if direction == "upgrade":
            for t in TABLES:
                conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        with Operations.context(MigrationContext.configure(conn)):
            for name in names:
                spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
                assert spec and spec.loader
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                getattr(module, direction)()
    sync.dispose()


@pytest.fixture
def schema() -> Iterator[None]:
    _migrate("upgrade")
    yield
    _migrate("downgrade")


class _Redis:
    async def get(self, _key: str) -> str:
        return "2026-10-20T17:00:00+00:00"


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_status_reports_jobs_events_and_corridors(
    schema: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_TOKEN", "s3cret")
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        await upsert_corridor(engine, NEAR)
        await save_events(engine, [_event("a")], [NEAR], "unplanned", datetime.now(UTC))

        async def ok() -> dict[str, int]:
            return {"records": 1}

        async def boom() -> None:
            raise RuntimeError("upstream fell over")

        await tracked(engine, "nh_poll_live", ok)
        with pytest.raises(RuntimeError):
            await tracked(engine, "nh_poll_planned", boom)

        app.state.engine = engine
        app.state.redis = _Redis()
        auth = {"Authorization": "Bearer s3cret"}
        body = client.get("/v1/status", headers=auth).json()

        latest = {j["job"]: j for j in body["jobs_latest"]}
        assert latest["nh_poll_live"]["status"] == "ok"
        assert latest["nh_poll_live"]["result"] == {"records": 1}
        assert latest["nh_poll_planned"]["status"] == "error"
        assert "upstream fell over" in latest["nh_poll_planned"]["error"]
        assert body["events_total"] == 1
        assert body["corridors"][0]["id"] == "near"
        assert body["corridors"][0]["active_events"] == 1
        assert body["version"] and body["commit"]
        assert body["fuel"]["stations"] == 0
        assert body["worker_heartbeat"].startswith("2026-10-20")

        listed = client.get("/v1/status/events?corridor=near&status=active", headers=auth).json()
        assert [e["record_id"] for e in listed] == ["a"]
        assert listed[0]["direction"] == "clockwise"
        assert client.get("/v1/status/events?corridor=other", headers=auth).json() == []
        assert len(client.get("/v1/status/jobs?limit=5", headers=auth).json()) == 2
    finally:
        await engine.dispose()
