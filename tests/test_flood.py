"""Flood warnings: reading the EA's feed, storing it safely, and asking what covers a place."""

from __future__ import annotations

import asyncio
import importlib.util
import os
from collections.abc import Iterator
from datetime import UTC, datetime
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

from app.integrations.flood import sync
from app.integrations.flood.normalise import geometry_of, parse_item, parse_time
from app.main import app

URL = os.environ.get("WA_TEST_DATABASE_URL")
VERSIONS = Path(__file__).parent.parent / "migrations" / "versions"
needs_db = pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")

HOME = (51.5074, -0.1278)
# a square about 2 km across, around home; and one in Birmingham
AROUND_HOME = [[[-0.14, 51.50], [-0.11, 51.50], [-0.11, 51.52], [-0.14, 51.52], [-0.14, 51.50]]]
AROUND_BRUM = [[[-1.91, 52.47], [-1.88, 52.47], [-1.88, 52.50], [-1.91, 52.50], [-1.91, 52.47]]]


def item(area: str = "112WAF1", level: int = 2, **kw: Any) -> dict[str, Any]:
    base = {
        "description": "River Thames at Westminster", "eaAreaName": "Thames", "floodAreaID": area,
        "isTidal": True, "message": "Be prepared.", "severity": "Flood Warning", "severityLevel": level,
        "timeRaised": "2026-10-22T08:15:00", "timeSeverityChanged": "2026-10-22T08:15:00",
        "timeMessageChanged": "2026-10-22T09:00:00",
        "floodArea": {"county": "London", "notation": area, "riverOrSea": "River Thames",
                      "polygon": f"https://environment.data.gov.uk/flood-monitoring/id/floodAreas/{area}/polygon"},
    }  # fmt: skip
    return {**base, **kw}


# --- the rules, with no database ------------------------------------------------------------------


def test_an_item_becomes_a_warning_and_an_area() -> None:
    parsed = parse_item(item())
    assert parsed is not None
    w, a = parsed["warning"], parsed["area"]
    assert (w["id"], w["area"], w["severity_level"], w["is_tidal"]) == ("112WAF1", "112WAF1", 2, True)
    assert (a["label"], a["county"], a["river_or_sea"], a["ea_area"]) == (
        "River Thames at Westminster", "London", "River Thames", "Thames")  # fmt: skip
    assert a["polygon_url"].endswith("/112WAF1/polygon")


@pytest.mark.parametrize("bad", [
    {}, "nope", item(severityLevel="x"), item(severityLevel=9), item(severityLevel=None),
    {**item(), "floodAreaID": None, "floodArea": {}},
])  # fmt: skip
def test_an_item_that_cannot_be_used_is_skipped(bad: Any) -> None:
    assert parse_item(bad) is None


def test_times_without_an_offset_are_read_as_uk_local_time() -> None:
    summer = parse_time("2026-07-01T12:00:00")
    winter = parse_time("2026-01-01T12:00:00")
    assert summer == datetime(2026, 7, 1, 11, 0, tzinfo=UTC)  # BST is an hour ahead of UTC
    assert winter == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert parse_time("2026-07-01T12:00:00Z") == datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    assert parse_time("") is None and parse_time("not a time") is None and parse_time(None) is None


def test_a_boundary_comes_out_of_a_feature_or_a_collection() -> None:
    geometry = {"type": "Polygon", "coordinates": AROUND_HOME}
    feature = {"type": "Feature", "geometry": geometry}
    assert geometry_of(feature) == geometry
    assert geometry_of({"type": "FeatureCollection", "features": [feature]}) == geometry
    assert geometry_of(geometry) == geometry
    for junk in (None, [], {"type": "Point", "coordinates": [0, 0]}, {"type": "FeatureCollection", "features": []}):
        assert geometry_of(junk) is None


# --- storing it -----------------------------------------------------------------------------------


@pytest.fixture
def schema() -> Iterator[None]:
    engine = sa.create_engine(URL)  # type: ignore[arg-type]
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS flood_warning, flood_area CASCADE"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        with Operations.context(MigrationContext.configure(conn)):
            spec = importlib.util.spec_from_file_location("m0021", VERSIONS / "0021_flood.py")
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.upgrade()
    yield
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS flood_warning, flood_area CASCADE"))
    engine.dispose()


def feed(items: list[dict[str, Any]], polygons: dict[str, Any] | None = None) -> httpx.AsyncClient:
    """A client that answers as the EA does, from memory."""
    polygons = polygons or {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/id/floods"):
            return httpx.Response(200, json={"items": items})
        for area, geometry in polygons.items():
            if path.endswith(f"/{area}/polygon"):
                return httpx.Response(200, json={"type": "Feature", "geometry": geometry})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def rows(engine: Any, sql: str) -> list[Any]:
    async with engine.connect() as conn:
        return (await conn.execute(text(sql))).all()


@needs_db
async def test_warnings_are_stored_their_boundaries_fetched_and_ended_ones_dropped(schema: None) -> None:
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        polygons = {"A": {"type": "Polygon", "coordinates": AROUND_HOME},
                    "B": {"type": "Polygon", "coordinates": AROUND_BRUM}}  # fmt: skip
        first = await sync.sync(engine, feed([item("A"), item("B", level=3), item("old", level=4), {}], polygons))
        assert first["warnings"] == 3 and first["live"] == 2 and first["skipped"] == 1
        assert first["boundaries"] == 2  # the ended one needs no shape

        assert [r[0] for r in await rows(engine, "SELECT id FROM flood_warning ORDER BY id")] == ["A", "B", "old"]
        assert (await rows(engine, "SELECT count(*) FROM flood_area WHERE geom IS NOT NULL"))[0][0] == 2

        # the next quarter hour: A has ended and left the list; B is still there and is not fetched again
        again = await sync.sync(engine, feed([item("B", level=3), item("old", level=4)], {}))
        assert again["ended"] == 1 and again["boundaries"] == 0
        assert [r[0] for r in await rows(engine, "SELECT id FROM flood_warning ORDER BY id")] == ["B", "old"]
    finally:
        await engine.dispose()


@needs_db
async def test_a_malformed_response_clears_nothing(schema: None) -> None:
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        await sync.sync(engine, feed([item("A")], {}))

        def broken(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": "try later"})

        with pytest.raises(ValueError):
            await sync.sync(engine, httpx.AsyncClient(transport=httpx.MockTransport(broken)))
        down = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
        with pytest.raises(httpx.HTTPStatusError):
            await sync.sync(engine, down)
        assert (await rows(engine, "SELECT count(*) FROM flood_warning"))[0][0] == 1  # still there

        # an empty list, though, is a real answer: no warnings anywhere
        await sync.sync(engine, feed([]))
        assert (await rows(engine, "SELECT count(*) FROM flood_warning"))[0][0] == 0
    finally:
        await engine.dispose()


@needs_db
async def test_a_boundary_that_will_not_load_is_retried_not_fatal(schema: None) -> None:
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        result = await sync.sync(engine, feed([item("A")], {}))  # the polygon URL answers 404
        assert result["boundaries"] == 0 and result["boundaries_failed"] == 1
        result = await sync.sync(engine, feed([item("A")], {"A": {"type": "Polygon", "coordinates": AROUND_HOME}}))
        assert result["boundaries"] == 1
    finally:
        await engine.dispose()


# --- asking ---------------------------------------------------------------------------------------


@needs_db
def test_what_covers_a_point(schema: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "tok")
    polygons = {"A": {"type": "Polygon", "coordinates": AROUND_HOME},
                "B": {"type": "Polygon", "coordinates": AROUND_BRUM}}  # fmt: skip

    async def load() -> None:
        engine = create_async_engine(URL)  # type: ignore[arg-type]
        try:
            await sync.sync(engine, feed([item("A", level=2), item("B", level=3), item("old", level=4)], polygons))
        finally:
            await engine.dispose()

    asyncio.run(load())
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        app.state.engine = engine
        c, h = TestClient(app), {"Authorization": "Bearer tok"}
        assert c.get("/v1/floods").status_code == 401

        live = c.get("/v1/floods", headers=h).json()
        assert [w["id"] for w in live["warnings"]] == ["A", "B"]  # most serious first; the ended one is out
        assert [w["id"] for w in c.get("/v1/floods?max_level=2", headers=h).json()["warnings"]] == ["A"]
        assert len(c.get("/v1/floods?max_level=4", headers=h).json()["warnings"]) == 3

        here = c.get(f"/v1/floods/at?lat={HOME[0]}&lon={HOME[1]}", headers=h).json()
        assert [w["id"] for w in here["warnings"]] == ["A"] and here["boundaries_missing"] == 0
        assert here["warnings"][0]["severity"] == "Flood Warning"
        nowhere = c.get("/v1/floods/at?lat=51.0&lon=-1.0", headers=h).json()
        assert nowhere["count"] == 0
        # a warning a few km away is not "here" until you widen the circle
        near = c.get("/v1/floods/at?lat=51.54&lon=-0.1278&within_km=5", headers=h).json()
        assert [w["id"] for w in near["warnings"]] == ["A"]
    finally:
        asyncio.run(engine.dispose())
