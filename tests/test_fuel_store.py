"""Fuel storage, sync and queries. Need Postgres (WA_TEST_DATABASE_URL)."""

from __future__ import annotations

import importlib.util
import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.integrations.fuelfinder import sync
from app.integrations.fuelfinder.client import IncompleteError
from app.integrations.fuelfinder.normalise import parse_prices, parse_station
from app.integrations.fuelfinder.queries import along_corridor, near_point
from app.integrations.nationalhighways.match import Corridor
from app.main import app

URL = os.environ.get("WA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
FILE = Path(__file__).parent.parent / "migrations" / "versions" / "0007_fuel.py"
TABLES = ["fuel_price_change", "fuel_price", "fuel_station"]


def _migrate(fn: str) -> None:
    spec = importlib.util.spec_from_file_location("m0007", FILE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sync_engine = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync_engine.begin() as conn:
        if fn == "upgrade":
            for t in TABLES:
                conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        with Operations.context(MigrationContext.configure(conn)):
            getattr(module, fn)()
    sync_engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def schema() -> Iterator[None]:
    _migrate("upgrade")
    yield
    _migrate("downgrade")


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(URL)  # type: ignore[arg-type]
    async with eng.begin() as conn:
        for t in TABLES:
            await conn.execute(text(f"DELETE FROM {t}"))
    yield eng
    await eng.dispose()


def station(node: str, lat: float, lon: float, **kw: Any) -> dict[str, Any]:
    return {
        "node_id": node, "trading_name": f"Stn {node}", "brand_name": "Brand",
        "is_motorway_service_station": kw.get("motorway", False),
        "is_supermarket_service_station": False,
        "temporary_closure": kw.get("closed", False), "permanent_closure": False,
        "location": {"postcode": "GU1 1AA", "city": "Town", "latitude": lat, "longitude": lon},
        "amenities": [], "fuel_types": ["E10"],
    }  # fmt: skip


def price(node: str, pence: float, age_h: float = 1.0, fuel: str = "E10") -> dict[str, Any]:
    stamp = (datetime.now(UTC) - timedelta(hours=age_h)).isoformat()
    return {"node_id": node, "fuel_prices": [{"price": pence, "fuel_type": fuel,
            "price_last_updated": stamp, "price_change_effective_timestamp": stamp}]}  # fmt: skip


@pytest.fixture(autouse=True)
def small_minimums(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync, "MIN_STATIONS", 3)


async def count(engine: AsyncEngine, sql: str) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(text(sql))).scalar_one())


STATIONS = [
    station("a", 51.30, -0.50), station("b", 51.31, -0.50), station("c", 51.32, -0.50),
    station("far", 53.0, -2.0), station("shut", 51.30, -0.501, closed=True),
    station("bad", 51.31, -0.499) | {"location": {"latitude": 0.0, "longitude": 0.0}},
]  # fmt: skip


async def load(engine: AsyncEngine) -> None:
    await sync.apply_stations(engine, STATIONS)
    await sync.apply_price_records(
        engine,
        [price("a", 140.0), price("b", 135.9), price("c", 150.0, age_h=100), price("far", 120.0),
         price("shut", 100.0), price("bad", 100.0), price("ghost", 90.0)],
    )  # fmt: skip


async def test_load_filters_and_counts(engine: AsyncEngine) -> None:
    await load(engine)
    assert await count(engine, "SELECT count(*) FROM fuel_station") == 6
    assert await count(engine, "SELECT count(*) FROM fuel_price") == 6  # ghost skipped
    assert await count(engine, "SELECT count(*) FROM fuel_price_change") == 6


async def test_only_changes_are_logged_and_missing_prices_removed(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    await load(engine)
    records = [price("a", 140.0), price("b", 130.0), price("c", 150.0), price("far", 120.0),
               price("shut", 100.0), price("bad", 100.0)]  # fmt: skip
    # reuse timestamps for unchanged rows so only b differs
    async with engine.connect() as conn:
        stored = {
            r[0]: r[1:] for r in await conn.execute(
                text("SELECT node_id, last_updated, effective FROM fuel_price")
            )
        }  # fmt: skip
    for rec in records:
        if rec["node_id"] != "b":
            lu, eff = stored[rec["node_id"]]
            rec["fuel_prices"][0]["price_last_updated"] = lu.isoformat()
            rec["fuel_prices"][0]["price_change_effective_timestamp"] = eff.isoformat()
    result = await sync.apply_price_records(engine, records)
    assert result["changed"] == 1
    assert await count(engine, "SELECT count(*) FROM fuel_price_change") == 7
    monkeypatch.setattr(sync, "MIN_PRICE_COVERAGE", 0.5)
    result = await sync.apply_price_records(engine, records[:5])
    assert result["removed"] == 1


async def test_short_pulls_are_refused(engine: AsyncEngine) -> None:
    with pytest.raises(IncompleteError):
        await sync.apply_stations(engine, STATIONS[:2])
    await sync.apply_stations(engine, STATIONS)
    with pytest.raises(IncompleteError):
        await sync.apply_price_records(engine, [price("a", 140.0)])
    assert await count(engine, "SELECT count(*) FROM fuel_price") == 0


async def test_near_point_ranks_and_filters(engine: AsyncEngine) -> None:
    await load(engine)
    hits = await near_point(engine, 51.305, -0.50, radius_km=5)
    # b is cheapest; the closed, bad-coordinate and far stations are excluded;
    # c's price is 100 hours old so dropped by the 48 h default
    assert [h.node_id for h in hits] == ["b", "a"]
    assert hits[0].pence == 135.9 and hits[0].distance_km < 2
    old_ok = await near_point(engine, 51.305, -0.50, radius_km=5, max_age_hours=None)
    assert [h.node_id for h in old_ok] == ["b", "a", "c"]
    assert await near_point(engine, 51.305, -0.50, radius_km=5, fuel="B7_STANDARD") == []
    with pytest.raises(ValueError):
        await near_point(engine, 51.3, -0.5, fuel="LPG")


async def test_along_corridor(engine: AsyncEngine) -> None:
    await load(engine)
    corridor = Corridor("c", "C", ((51.29, -0.50), (51.33, -0.50)), buffer_m=500)
    hits = await along_corridor(engine, corridor, within_km=1, max_age_hours=None)
    assert [h.node_id for h in hits] == ["b", "a", "c"]
    assert await along_corridor(engine, Corridor("x", "X", ((52.0, 0.0), (52.1, 0.0)))) == []


async def test_from_capture_and_api(engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync, "MIN_PRICE_COVERAGE", 0.5)
    sync_db = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync_db.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS raw_capture CASCADE"))
        conn.execute(text("""CREATE TABLE raw_capture (id bigint GENERATED ALWAYS AS IDENTITY
            PRIMARY KEY, run_id uuid, call_name text, page_index int, body jsonb)"""))
        run = "11111111-1111-1111-1111-111111111111"
        for name, body in (("ff_stations", STATIONS), ("ff_prices", [price("a", 140.0), price("b", 130.0),
                           price("c", 150.0), price("far", 120.0)])):  # fmt: skip
            conn.execute(
                text("INSERT INTO raw_capture (run_id, call_name, page_index, body) "
                     "VALUES (:r, :n, 0, CAST(:b AS jsonb))"),
                {"r": run, "n": name, "b": __import__("json").dumps(body)},
            )  # fmt: skip
    try:
        out = await sync.load_from_capture(engine)
        assert out["stations"]["stations"] == 6
        assert out["prices"]["incoming"] == 4
    finally:
        with sync_db.begin() as conn:
            conn.execute(text("DROP TABLE raw_capture"))
        sync_db.dispose()

    monkeypatch.setenv("API_TOKEN", "tok")
    app.state.engine = engine
    client = TestClient(app)
    auth = {"Authorization": "Bearer tok"}
    r = client.get("/v1/fuel/near?lat=51.305&lon=-0.5&radius_km=5", headers=auth)
    assert r.status_code == 200
    assert r.json()["stations"][0]["node_id"] == "b"
    assert client.get("/v1/fuel/near?lat=51.3&lon=-0.5&fuel=LPG", headers=auth).status_code == 422
    assert client.get("/v1/fuel/near?lat=51.3", headers=auth).status_code == 422  # lat without lon
    assert client.get("/v1/fuel/summary", headers=auth).json()["stations"] == 6
    assert client.get("/v1/fuel/near?lat=51.3&lon=-0.5").status_code in (401, 503)


def test_parsers_roundtrip() -> None:
    s = parse_station(station("a", 51.3, -0.5))
    assert s and s.coords_ok and s.open_for_business
    assert parse_prices(price("a", 140.0))[0].pence == 140.0


def test_fuel_page_loads_without_token() -> None:
    r = TestClient(app).get("/fuel")
    assert r.status_code == 200
    assert "Cheapest fuel" in r.text
