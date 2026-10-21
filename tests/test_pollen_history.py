from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Self

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app import pollen, pollen_history
from app.integrations.pollen.client import parse_forecast
from app.main import app
from tests.test_pollen import RAW, _FakeRedis

URL = os.environ.get("WA_TEST_DATABASE_URL")
VERSIONS = Path(__file__).parent.parent / "migrations" / "versions"
MIGRATIONS = ["0003_nh_storage", "0006_places_trips", "0013_pollen_history"]
TABLES = ["pollen_reading", "trip", "place", "nh_sign_message", "nh_sign", "nh_event_corridor", "nh_event",
          "nh_corridor"]  # fmt: skip
TODAY = date(2026, 10, 21)


def test_readings_take_today_and_the_risk() -> None:
    rows = pollen_history.readings(parse_forecast(RAW, TODAY))
    assert rows == [("1", "Birch", 0.0), ("2", "Grasses", 3.0), ("_risk", "Allergy risk", 6.0)]


def test_shape_aligns_days_and_totals() -> None:
    first, last = TODAY - timedelta(days=2), TODAY
    rows = [
        ("h", first, "1", "Birch", 1.0), ("h", first, "2", "Grasses", 2.0),
        ("h", last, "1", "Birch", 4.0), ("h", last, "_risk", "Allergy risk", 7.0),
        ("h", first - timedelta(days=9), "1", "Birch", 4.0),  # outside the window: ignored
    ]  # fmt: skip
    h = pollen_history.shape(rows, first, last)
    assert h["days"] == ["2026-10-19", "2026-10-20", "2026-10-21"]
    assert h["total"] == [3.0, None, 4.0] and h["days_recorded"] == 2
    assert h["allergy_risk"] == [None, None, 7.0]
    assert [a["name"] for a in h["allergens"]] == ["Birch", "Grasses"]  # highest peak first
    assert h["allergens"][0]["values"] == [1.0, None, 4.0]


@pytest.fixture
def schema() -> Iterator[None]:
    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn:
        for t in TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        with Operations.context(MigrationContext.configure(conn)):
            for name in MIGRATIONS:
                spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
                assert spec and spec.loader
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.upgrade()
        conn.execute(text("INSERT INTO place (id, name, lat, lon) VALUES ('home', 'Home', 51.5074, -0.1278)"))
    yield
    with sync.begin() as conn:
        for t in TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
    sync.dispose()


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_record_replaces_same_day_prunes_and_reads_back(schema: None) -> None:
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        fc = parse_forecast(RAW, TODAY)
        assert await pollen_history.record(engine, "home", TODAY, fc) == 3
        later = {**fc, "allergens": [{**fc["allergens"][1], "levels": [4, 0, 0, 0]}]}
        await pollen_history.record(engine, "home", TODAY, later)  # same day: replaced
        old = TODAY - timedelta(days=pollen_history.KEEP_DAYS + 1)
        await pollen_history.record(engine, "home", old, fc)
        await pollen_history.prune(engine, TODAY)
        h = await pollen_history.history(engine, "home", TODAY, days=90)
        assert len(h["days"]) == 90 and h["days"][-1] == "2026-10-21"
        assert h["days_recorded"] == 1  # the old day was pruned
        grasses = next(a for a in h["allergens"] if a["id"] == "2")
        assert grasses["values"][-1] == 4.0
        assert h["total"][-1] == 4.0  # birch 0 + grasses 4
    finally:
        await engine.dispose()


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_sync_job_stores_for_each_place_and_warms_cache(
    schema: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    calls = {"n": 0}

    class Fake:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

        async def forecast(self, lat: float, lon: float, today: date, **kw: Any) -> dict[str, Any]:
            calls["n"] += 1
            return parse_forecast(RAW, today)

    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO place (id, name, lat, lon) VALUES ('work', 'Work', 51.5075, -0.1279)"))
        await conn.execute(text("CREATE TABLE IF NOT EXISTS job_run (id serial primary key, job text, started_at timestamptz default now(), finished_at timestamptz, status text, detail text, error text)"))
    monkeypatch.setenv("POLLEN_API_KEY", "k")
    monkeypatch.setattr(pollen_history, "PollenClient", Fake)

    async def fake_tracked(engine: Any, name: str, work: Any) -> Any:
        return await work()

    class NoLimit:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def acquire(self, *a: Any, **k: Any) -> None:
            return None

    monkeypatch.setattr("app.jobs.tracked", fake_tracked)
    monkeypatch.setattr("app.pollen_budget.PollenLimiter", NoLimit)
    redis = _FakeRedis()
    try:
        out = await pollen_history.pollen_sync({"engine": engine, "redis": redis})
        assert out == {"places": 2, "values": 6} and calls["n"] == 1  # two places, one spot: one fetch
        assert len(redis.d) == 1
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS job_run"))
        await engine.dispose()


def test_history_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "s3cret")

    async def fake_place(request: Any, place: str) -> tuple[str, float, float]:
        return "Home", 51.3, -0.2

    async def fake_history(engine: Any, place: str, today: date, days: int) -> dict[str, Any]:
        return {"days": ["2026-10-21"], "total": [3.0], "allergy_risk": [None], "allergens": [], "days_recorded": 1}

    monkeypatch.setattr(pollen, "_place", fake_place)
    monkeypatch.setattr(pollen, "history", fake_history)
    c = TestClient(app)
    assert c.get("/v1/pollen/history?place=home").status_code == 401
    r = c.get("/v1/pollen/history?place=home&days=30", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200 and r.json()["total"] == [3.0] and r.json()["place"] == "Home"
    assert c.get("/v1/pollen/history?place=home&days=91", headers={"Authorization": "Bearer s3cret"}).status_code == 422
