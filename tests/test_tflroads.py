"""TfL road disruptions: reading the feed defensively, storing it safely, and asking what is near a point."""

from __future__ import annotations

import asyncio
import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.integrations.tfl.client import TflClient, TflError
from app.integrations.tflroads import sync
from app.integrations.tflroads.normalise import geometry_of, parse_item, parse_time, rank, scope_of
from app.main import app

URL = os.environ.get("WA_TEST_DATABASE_URL")
VERSIONS = Path(__file__).parent.parent / "migrations" / "versions"
needs_db = pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")

LINE = [
    [-0.1200, 51.5010],
    [-0.1190, 51.5030],
]  # [lon, lat]: Westminster Bridge Road, near home (51.5074, -0.1278)


def item(ident: str = "TIMS-1", **kw: Any) -> dict[str, Any]:
    base = {
        "id": ident, "category": "Works", "subCategory": "Utility works", "severity": "Moderate",
        "status": "Active", "location": "[A23] Westminster Bridge Road (SE1) (Lambeth)",
        "comments": "Gas main replacement", "currentUpdate": "Lane closed",
        "startDateTime": "2026-10-20T07:00:00", "endDateTime": "2026-11-20T19:00:00",
        "lastModifiedTime": "2026-10-22T08:00:00Z", "hasClosures": True, "corridorIds": ["a23"],
        "streets": [{"name": "Westminster Bridge Road", "closure": "Open",
                     "segments": [{"lineString": str(LINE)}]}],
    }  # fmt: skip
    return {**base, **kw}


# --- the rules, with no database ------------------------------------------------------------------


def test_an_item_is_read_and_its_street_lines_become_geometry() -> None:
    p = parse_item(item())
    assert p is not None
    assert (p["id"], p["scope"], p["severity"], p["has_closures"], p["corridors"]) == (
        "TIMS-1", "planned", "Moderate", True, "a23")  # fmt: skip
    assert p["geom"] == {"type": "MultiLineString", "coordinates": [LINE]}
    assert (
        p["starts_at"].isoformat() == "2026-10-20T06:00:00+00:00"
    )  # BST: naive London 07:00 is 06:00 UTC


def test_a_position_is_found_wherever_the_feed_puts_it() -> None:
    bare = {"id": "x"}
    assert geometry_of({**bare, "geography": {"type": "Point", "coordinates": [-0.12, 51.5]}}) == {
        "type": "Point", "coordinates": [-0.12, 51.5]}  # fmt: skip
    assert geometry_of({**bare, "point": "[-0.12, 51.5]"})["coordinates"] == [-0.12, 51.5]
    assert (
        geometry_of({**bare, "geometry": {"type": "LineString", "coordinates": LINE}})["type"]
        == "MultiLineString"
    )
    assert geometry_of(
        {**bare, "geography": {"type": "Polygon", "coordinates": [[*LINE, [-0.12, 51.5]]]}}
    )
    # nothing usable, or coordinates outside Britain, is no position rather than a wrong one
    for junk in (
        {},
        {"point": "nonsense"},
        {"point": "[0, 0]"},
        {"geography": {"type": "Point", "coordinates": [51.5, -0.1]}},
    ):
        assert geometry_of({**bare, **junk}) is None


def test_a_closure_on_any_street_counts_even_if_the_flag_is_missing() -> None:
    plain = item(hasClosures=False)
    plain["streets"][0]["closure"] = "Open"
    assert parse_item(plain)["has_closures"] is False  # type: ignore[index]
    closed = item(hasClosures=False)
    closed["streets"][0]["closure"] = "Full"
    assert parse_item(closed)["has_closures"] is True  # type: ignore[index]


def test_incidents_are_unplanned_and_works_are_planned() -> None:
    assert scope_of("Collisions", None) == "unplanned"
    assert scope_of("Works", "Burst water main") == "unplanned"
    assert scope_of("Works", "Planned works") == "planned"
    assert scope_of("Special Events", "Parade") == "planned"
    assert scope_of(None, None) == "planned"


def test_times_without_an_offset_are_london_time() -> None:
    assert parse_time("2026-01-10T07:00:00").isoformat() == "2026-01-10T07:00:00+00:00"  # type: ignore[union-attr]
    assert parse_time("2026-07-10T07:00:00").isoformat() == "2026-07-10T06:00:00+00:00"  # type: ignore[union-attr]
    assert parse_time("2026-07-10T07:00:00Z").isoformat() == "2026-07-10T07:00:00+00:00"  # type: ignore[union-attr]
    assert parse_time("garbage") is None and parse_time(None) is None


def test_junk_is_skipped_not_fatal() -> None:
    assert parse_item({}) is None and parse_item({"id": "  "}) is None and parse_item("x") is None  # type: ignore[arg-type]
    assert parse_item({"id": "ok"}) is not None  # no position, no times: still worth listing


def test_severity_ranks() -> None:
    assert [rank(s) for s in ("Minimal", "moderate", "Serious", "SEVERE", "odd", None)] == [
        1,
        2,
        3,
        4,
        0,
        0,
    ]


# --- storing it -----------------------------------------------------------------------------------


@pytest.fixture
def schema() -> Iterator[None]:
    engine = sa.create_engine(URL)  # type: ignore[arg-type]
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS tfl_road_disruption CASCADE"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        with Operations.context(MigrationContext.configure(conn)):
            spec = importlib.util.spec_from_file_location("m0025", VERSIONS / "0025_tfl_road.py")
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.upgrade()
    yield
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS tfl_road_disruption CASCADE"))
    engine.dispose()


def client(body: Any, status: int = 200) -> TflClient:
    return TflClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body)),
        max_attempts=1,
    )


async def rows(engine: Any, sql: str) -> list[Any]:
    async with engine.connect() as conn:
        return (await conn.execute(text(sql))).all()


@needs_db
async def test_disruptions_are_stored_updated_and_ended_ones_dropped(schema: None) -> None:
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        first = await sync.sync(engine, client([item("A"), item("B", streets=[]), {"nope": 1}]))
        assert first == {"disruptions": 2, "ended": 0, "skipped": 1, "positioned": 1}
        assert [
            r[0] for r in await rows(engine, "SELECT id FROM tfl_road_disruption ORDER BY id")
        ] == ["A", "B"]
        assert (
            await rows(engine, "SELECT count(*) FROM tfl_road_disruption WHERE geom IS NOT NULL")
        )[0][0] == 1

        again = await sync.sync(engine, client([item("A", severity="Severe")]))  # B has ended
        assert again["ended"] == 1
        assert (await rows(engine, "SELECT id, severity FROM tfl_road_disruption"))[0] == (
            "A",
            "Severe",
        )
    finally:
        await engine.dispose()


@needs_db
async def test_a_bad_response_clears_nothing(schema: None) -> None:
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        await sync.sync(engine, client([item("A")]))
        with pytest.raises(TflError):
            await sync.sync(engine, client({"message": "try later"}))
        with pytest.raises(TflError):
            await sync.sync(engine, client({}, status=503))
        assert (await rows(engine, "SELECT count(*) FROM tfl_road_disruption"))[0][0] == 1

        # an empty list is believed while little is stored, but not over a populated table
        await sync.sync(engine, client([]))
        assert (await rows(engine, "SELECT count(*) FROM tfl_road_disruption"))[0][0] == 0
        await sync.sync(engine, client([item(f"D{i}") for i in range(25)]))
        with pytest.raises(TflError):
            await sync.sync(engine, client([]))
        assert (await rows(engine, "SELECT count(*) FROM tfl_road_disruption"))[0][0] == 25
    finally:
        await engine.dispose()


# --- asking ---------------------------------------------------------------------------------------


@needs_db
def test_what_is_near_a_point(schema: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "tok")

    async def load() -> None:
        engine = create_async_engine(URL)  # type: ignore[arg-type]
        try:
            await sync.sync(
                engine,
                client(
                    [
                        item("works"),
                        item(
                            "crash",
                            category="Collisions",
                            subCategory="",
                            severity="Serious",
                            hasClosures=False,
                        ),
                        item("over", endDateTime="2026-01-01T00:00:00"),
                        item(
                            "far",
                            streets=[
                                {"segments": [{"lineString": str([[-1.9, 52.48], [-1.89, 52.49]])}]}
                            ],
                        ),
                    ]
                ),
            )
        finally:
            await engine.dispose()

    asyncio.run(load())
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        app.state.engine = engine
        c, h = TestClient(app), {"Authorization": "Bearer tok"}
        assert c.get("/v1/roads/tfl").status_code == 401

        everything = c.get("/v1/roads/tfl", headers=h).json()
        ids = [d["id"] for d in everything["disruptions"]]
        assert ids[0] == "crash" and set(ids) == {
            "crash",
            "works",
            "far",
        }  # unplanned first; the ended one is out
        assert everything["stored"] == 4
        assert [
            d["id"] for d in c.get("/v1/roads/tfl?scope=unplanned", headers=h).json()["disruptions"]
        ] == ["crash"]
        assert {d["id"] for d in c.get("/v1/roads/tfl?closures_only=true", headers=h).json()["disruptions"]} == {
            "works", "far"}  # fmt: skip

        near = c.get("/v1/roads/tfl/at?lat=51.5074&lon=-0.1278&radius_m=1000", headers=h).json()
        assert {d["id"] for d in near["disruptions"]} == {"works", "crash"}
        tight = c.get("/v1/roads/tfl/at?lat=51.5074&lon=-0.1278&radius_m=100", headers=h).json()
        assert tight["count"] == 0
    finally:
        asyncio.run(engine.dispose())
