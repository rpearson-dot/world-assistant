from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self
from zoneinfo import ZoneInfo

import httpx
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app import tfl
from app.integrations.tfl.client import TflClient, TflError
from app.integrations.tfl.normalise import parse_departures, parse_time, select, summarise_status
from app.main import app

NOW = datetime(2026, 10, 20, 20, 0, tzinfo=UTC)  # 21:00 UK time (BST)


def rec(
    dest: str, sched: str, est: str | None = None, status: str = "OnTime", plat: str = "1"
) -> dict[str, Any]:
    return {"stationName": "Worcester Park Rail Station", "lineName": "South Western Railway",
            "destinationName": dest, "platformName": plat, "scheduledTimeOfDeparture": sched,
            "estimatedTimeOfDeparture": est or sched, "departureStatus": status}  # fmt: skip


RECORDS = [
    rec("London Waterloo", "2026-10-20T20:24:00Z"),
    rec("London Waterloo", "2026-10-20T19:50:00Z"),  # already gone
    rec("Guildford", "2026-10-20T20:22:00Z"),
    rec("London Waterloo", "2026-10-20T20:54:00Z", "2026-10-20T21:00:00Z", "Delayed"),
    rec("Dorking", "2026-10-20T20:52:00Z"),
]


def test_parse_time_zones() -> None:
    assert parse_time("2026-10-20T20:24:00Z") == datetime(2026, 10, 20, 20, 24, tzinfo=UTC)
    # no offset: read as UK local time (BST is UTC+1 in October)
    assert parse_time("2026-10-20T21:24:00") == datetime(2026, 10, 20, 20, 24, tzinfo=UTC)
    assert parse_time("0001-01-01T00:00:00") is None
    assert parse_time(None) is None and parse_time("nonsense") is None


def test_select_filters_sorts_and_drops_departed() -> None:
    deps = parse_departures(RECORDS)
    to_waterloo = select(deps, include=["waterloo"], exclude=[], now=NOW, limit=5)
    assert [d.when.strftime("%H:%M") for d in to_waterloo] == ["20:24", "21:00"]
    assert to_waterloo[1].delay_minutes == 6 and to_waterloo[1].status == "Delayed"
    away = select(deps, include=[], exclude=["Waterloo"], now=NOW, limit=5)
    assert [d.destination for d in away] == ["Guildford", "Dorking"]
    assert len(select(deps, include=[], exclude=[], now=NOW, limit=2)) == 2
    assert to_waterloo[0].as_dict(NOW)["minutes"] == 24


def test_missing_fields_become_none() -> None:
    d = parse_departures([{"destinationName": "X"}])[0]
    assert d.when is None and d.delay_minutes is None
    assert select([d], include=[], exclude=[], now=NOW, limit=5) == []  # no time: not shown


def test_status_summary() -> None:
    out = summarise_status([{"id": "tube", "name": "Tube", "modeName": "tube", "lineStatuses": [
        {"statusSeverity": 9, "statusSeverityDescription": "Minor Delays", "reason": "Signal failure"}]},
        {"id": "dlr", "lineStatuses": []}])  # fmt: skip
    assert out[0]["status"] == "Minor Delays" and out[0]["reason"] == "Signal failure"
    assert out[1]["status"] is None


async def _nosleep(_s: float) -> None:
    return None


async def test_client_key_retry_and_errors() -> None:
    seen: list[httpx.Request] = []
    responses = [httpx.Response(503), httpx.Response(200, json=[{"a": 1}])]

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        return responses.pop(0)

    c = TflClient("KEY", transport=httpx.MockTransport(handler), sleep=_nosleep)
    assert await c.arrival_departures("910GWORCSPK", ["south-western-railway"]) == [{"a": 1}]
    assert len(seen) == 2
    assert seen[0].url.params["app_key"] == "KEY"
    assert seen[0].url.params["lineIds"] == "south-western-railway"
    assert seen[0].url.path == "/StopPoint/910GWORCSPK/ArrivalDepartures"
    for code, word in ((404, "not found"), (401, "app key")):
        bad = TflClient(
            transport=httpx.MockTransport(lambda _r, c=code: httpx.Response(c)), sleep=_nosleep
        )
        with pytest.raises(TflError, match=word):
            await bad.get("/x")
    nokey = TflClient(
        transport=httpx.MockTransport(
            lambda r: (
                (_ for _ in ()).throw(AssertionError)
                if "app_key" in r.url.params
                else httpx.Response(200, json={"matches": [{"id": "1", "name": "N"}]})
            )
        )
    )
    assert await nokey.search_stops("x") == [{"id": "1", "name": "N"}]


def test_page_loads_without_token() -> None:
    r = TestClient(app).get("/trains")
    assert r.status_code == 200 and "<h1>Trains</h1>" in r.text


# ---- database + API ------------------------------------------------------------------------

URL = os.environ.get("WA_TEST_DATABASE_URL")
VERSIONS = Path(__file__).parent.parent / "migrations" / "versions"


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, VERSIONS / file)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def schema() -> Iterator[None]:
    m10, m12 = _load("m0010", "0010_tfl_boards.py"), _load("m0012", "0012_board_source.py")
    eng = sa.create_engine(URL)  # type: ignore[arg-type]
    with eng.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS tfl_board"))
        with Operations.context(MigrationContext.configure(conn)):
            m10.upgrade()
            m12.upgrade()
    yield
    with eng.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        m12.downgrade()
        m10.downgrade()
    eng.dispose()


class _FakeClient:
    calls = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    async def arrival_departures(self, station: str, lines: list[str]) -> list[dict[str, Any]]:
        type(self).calls += 1
        base = datetime.now(UTC).replace(second=0, microsecond=0)
        mk = lambda dest, mins: rec(dest, (base + timedelta(minutes=mins)).isoformat())
        return [mk("London Waterloo", 10), mk("Guildford", 5), mk("London Waterloo", 40)]

    async def search_stops(self, q: str, modes: str = "", limit: int = 10) -> list[dict[str, Any]]:
        return [{"id": "910GWORCSPK", "name": "Worcester Park", "modes": ["national-rail"]}]


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_boards_and_departures_api(schema: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "tok")
    monkeypatch.setattr(tfl, "_client", lambda _request: _FakeClient())
    tfl._cache.clear()
    _FakeClient.calls = 0
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        app.state.engine = engine
        c = TestClient(app)
        h = {"Authorization": "Bearer tok"}
        board = {"name": "To Waterloo", "station": "910GWORCSPK", "lines": ["south-western-railway"],
                 "include": ["Waterloo"], "max_rows": 2}  # fmt: skip
        assert c.put("/v1/tfl/boards/to-waterloo", json=board, headers=h).status_code == 200
        away = {**board, "name": "From Waterloo", "include": [], "exclude": ["Waterloo"]}
        assert c.put("/v1/tfl/boards/from-waterloo", json=away, headers=h).status_code == 200
        assert (
            c.put("/v1/tfl/boards/bad", json={**board, "station": "x;y"}, headers=h).status_code
            == 422
        )
        assert (
            c.put("/v1/tfl/boards/bad", json={**board, "lines": []}, headers=h).status_code == 422
        )
        assert len(c.get("/v1/tfl/boards", headers=h).json()) == 2

        d = c.get("/v1/tfl/boards/to-waterloo/departures", headers=h).json()
        assert [x["destination"] for x in d["departures"]] == ["London Waterloo"] * 2
        assert d["departures"][0]["minutes"] in (9, 10)
        a = c.get("/v1/tfl/boards/from-waterloo/departures", headers=h).json()
        assert [x["destination"] for x in a["departures"]] == ["Guildford"]
        assert _FakeClient.calls == 1  # second board reused the cached response
        assert c.get("/v1/tfl/boards/nope/departures", headers=h).status_code == 404
        assert c.get("/v1/tfl/stations?q=worcester", headers=h).json()[0]["id"] == "910GWORCSPK"
        assert c.get("/v1/tfl/boards").status_code in (401, 503)
        assert c.delete("/v1/tfl/boards/to-waterloo", headers=h).status_code == 204
        assert c.delete("/v1/tfl/boards/to-waterloo", headers=h).status_code == 404
    finally:
        await engine.dispose()


class _FakeRail:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    async def departures(
        self, crs: str, rows: int = 15, to_crs: str | None = None
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        hhmm = lambda mins: (
            (now + timedelta(minutes=mins)).astimezone(ZoneInfo("Europe/London")).strftime("%H:%M")
        )
        return {"locationName": "Stoneleigh", "trainServices": [
            {"std": hhmm(8), "etd": "On time", "platform": "2", "destination": [{"locationName": "London Waterloo"}]},
            {"std": hhmm(20), "etd": "Cancelled", "isCancelled": True, "destination": [{"locationName": "Epsom"}]},
        ]}  # fmt: skip

    async def search_stations(self, q: str) -> list[dict[str, Any]]:
        return [{"stationName": "Stoneleigh", "crsCode": "SNL"}]


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_national_rail_board(schema: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "tok")
    monkeypatch.setattr(tfl, "_rail_client", lambda _request: _FakeRail())
    tfl._cache.clear()
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        app.state.engine = engine
        c = TestClient(app)
        h = {"Authorization": "Bearer tok"}
        board = {
            "name": "From Stoneleigh",
            "source": "nationalrail",
            "station": "SNL",
            "include": ["Waterloo"],
        }
        assert c.put("/v1/tfl/boards/snl", json=board, headers=h).status_code == 200
        assert (
            c.put(
                "/v1/tfl/boards/bad", json={**board, "station": "Stoneleigh"}, headers=h
            ).status_code
            == 422
        )
        assert (
            c.put("/v1/tfl/boards/bad", json={**board, "source": "tfl"}, headers=h).status_code
            == 422
        )  # no lines
        d = c.get("/v1/tfl/boards/snl/departures", headers=h).json()
        assert d["source"] == "nationalrail" and [x["destination"] for x in d["departures"]] == [
            "London Waterloo"
        ]
        assert d["departures"][0]["platform"] == "2"
        found = c.get("/v1/tfl/stations?q=stoneleigh&source=nationalrail", headers=h).json()
        assert found[0]["id"] == "SNL"
        listed = c.get("/v1/tfl/boards", headers=h).json()
        assert listed[0]["source"] == "nationalrail"
    finally:
        await engine.dispose()


async def test_cache_serves_stale_on_error_and_coalesces(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from app.integrations.rail.rdm import RailError

    tfl._cache.clear()
    tfl._locks.clear()
    calls = 0
    fail = False

    async def fetch() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        if fail:
            raise RailError("rail request failed: HTTP 500")
        return f"v{calls}"

    # concurrent callers share one upstream call
    results = await asyncio.gather(*(tfl._cached_aged("k", 30.0, fetch) for _ in range(5)))
    assert calls == 1 and {r[0] for r in results} == {"v1"}

    # expire the entry; the provider now fails: the old answer is served, with its age
    t, v = tfl._cache["k"]
    tfl._cache["k"] = (t - 100, v)
    fail = True
    value, age = await tfl._cached_aged("k", 30.0, fetch)
    assert value == "v1" and age >= 100

    # too old to serve: the error surfaces
    tfl._cache["k"] = (t - 10_000, v)
    with pytest.raises(RailError):
        await tfl._cached_aged("k", 30.0, fetch)
    # nothing cached at all: the error surfaces
    with pytest.raises(RailError):
        await tfl._cached_aged("other", 30.0, fetch)
