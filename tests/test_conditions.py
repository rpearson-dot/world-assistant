"""Trip conditions: the rules that turn reports into "clear / minor / disrupted / severe", and the queries."""

from __future__ import annotations

import asyncio
import importlib.util
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app import conditions as c
from app.integrations.flood import sync as flood_sync
from app.integrations.nationalhighways.match import Corridor
from app.integrations.nationalhighways.store import upsert_corridor
from app.integrations.tfl.client import TflClient
from app.integrations.tflroads import sync as tfl_sync
from app.main import app
from tests.test_flood import item as flood_item_json

URL = os.environ.get("WA_TEST_DATABASE_URL")
VERSIONS = Path(__file__).parent.parent / "migrations" / "versions"
needs_db = pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
MIGRATIONS = ["0003_nh_storage", "0006_places_trips", "0008_trip_routes", "0021_flood", "0025_tfl_road"]
TABLES = ["trip", "place", "nh_sign_message", "nh_sign", "nh_event_corridor", "nh_event", "nh_corridor",
          "flood_warning", "flood_area", "tfl_road_disruption"]  # fmt: skip
AUTH = {"Authorization": "Bearer tok"}

# the route: from near Waterloo to near the Barbican, [lat, lon]
ROUTE = ((51.5000, -0.1300), (51.5100, -0.1100))
ON_ROUTE = [[-0.1201, 51.5051], [-0.1199, 51.5049]]  # [lon, lat], on the line
NEARBY = [[-0.1201, 51.5069], [-0.1199, 51.5071]]  # about 200 m from the line
BRUM = [[-1.9, 52.48], [-1.89, 52.49]]
FLOOD_AREA = [[[-0.14, 51.49], [-0.10, 51.49], [-0.10, 51.52], [-0.14, 51.52], [-0.14, 51.49]]]
HOUR = timedelta(hours=1)


# --- the rules, with no database ------------------------------------------------------------------


def test_levels() -> None:
    assert (c.nh_level("closure"), c.nh_level("speed")) == (3, 1)
    assert [c.tfl_level(s, False) for s in ("Minimal", "Moderate", "Serious", "Severe", None)] == [1, 1, 2, 3, 1]
    assert [c.tfl_level(s, True) for s in ("Minimal", "Moderate", "Serious", "Severe")] == [1, 2, 3, 3]
    assert [c.flood_level(n) for n in (1, 2, 3)] == [3, 2, 1]


def test_status_is_the_worst_item() -> None:
    assert c.status_of([]) == "clear"
    assert c.status_of([{"level": 1}]) == "minor"
    assert c.status_of([{"level": 1}, {"level": 2}]) == "disrupted"
    assert c.status_of([{"level": 3}, {"level": 1}]) == "severe"


def stamp(**kw: Any) -> str:
    return (datetime.now(UTC) + timedelta(**kw)).isoformat()


def test_items_split_into_during_the_journey_and_after_it() -> None:
    until = datetime.now(UTC) + HOUR
    items = [{"id": "open", "level": 1, "starts": None}, {"id": "begun", "level": 2, "starts": stamp(hours=-5)},
             {"id": "during", "level": 3, "starts": stamp(minutes=30)},
             {"id": "later", "level": 1, "starts": stamp(days=2)}, {"id": "soon", "level": 3, "starts": stamp(hours=3)}]  # fmt: skip
    now_items, later = c.split(items, until)
    assert [i["id"] for i in now_items] == ["during", "begun", "open"]  # worst first
    assert [i["id"] for i in later] == ["soon", "later"]  # soonest first


def test_the_sentence() -> None:
    assert c.sentence("Home to work", [], [], 7) == "Nothing reported on Home to work right now."
    now_items = [{"headline": f"Closure {n}", "where": "M25", "level": 3} for n in range(5)]
    said = c.sentence("Home to work", now_items, [{"starts": stamp(days=2)}], 7)
    assert "5 problems reported" in said and "and 2 more" in said and "1 planned item in the next 7 days" in said
    assert "Closure 3" not in said  # only the worst three are read out
    one = c.sentence("Trip", [{"headline": "Roadworks", "where": None, "level": 1}], [], 7)
    assert one == "Trip: 1 problem reported. Roadworks."


def test_items_read_from_rows() -> None:
    nh = c.nh_item(NS(record_id="r", roads=["M25"], kind="speed", limit_mph=40, scope="planned", description="J10-J11",
                      start_time=None, end_time=None, detail=None, cause="roadworks", status="active"))  # fmt: skip
    assert (nh["headline"], nh["level"], nh["where"], nh["unplanned"]) == ("40 mph limit on M25", 1, "J10-J11", False)
    tfl = c.tfl_item(NS(id="T", sub_category="Unknown", category="Works", severity="Serious", has_closures=True,
                        scope="planned", location="A23", starts_at=None, ends_at=None, update_text=None,
                        comment="Gas", status="Active", metres=12.4))  # fmt: skip
    assert (tfl["headline"], tfl["level"], tfl["detail"], tfl["metres"]) == ("Works (road closed)", 3, "Gas", 12)
    fl = c.flood_item(NS(id="F", severity_level=2, severity="Flood Warning", label="Thames at Westminster",
                         river_or_sea="River Thames", county="London", message="Be prepared", raised_at=None))  # fmt: skip
    assert (fl["level"], fl["where"], fl["headline"]) == (2, "River Thames, London", "Flood Warning for Thames at Westminster")


# --- asking the database --------------------------------------------------------------------------


@pytest.fixture
def schema() -> Iterator[None]:
    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        for t in TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        with Operations.context(MigrationContext.configure(conn)):
            for name in MIGRATIONS:
                spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
                assert spec and spec.loader
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.upgrade()
    yield
    with sync.begin() as conn:
        for t in TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
    sync.dispose()


def tfl_item(ident: str, line: list[list[float]], **kw: Any) -> dict[str, Any]:
    base = {"id": ident, "category": "Works", "subCategory": "Utility works", "severity": "Moderate",
            "location": f"road {ident}", "startDateTime": stamp(days=-1), "endDateTime": stamp(days=10),
            "streets": [{"segments": [{"lineString": str(line)}]}]}  # fmt: skip
    return {**base, **kw}


def nh_row(rid: str, kind: str, start: str | None, end: str | None = None) -> dict[str, Any]:
    return {"rid": rid, "kind": kind, "start": start, "end": end}


async def load(engine: Any, *, tfl: list[dict[str, Any]], nh: list[dict[str, Any]], flood: bool) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO place (id, name, kind, lat, lon) VALUES ('a','A','other',51.5,-0.13),('b','B','other',51.51,-0.11)"))
    await upsert_corridor(engine, Corridor("trip-t", "T", ROUTE, buffer_m=500, roads=frozenset({"A3"})), source="route")
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO trip (id, name, from_place, to_place, enabled, corridor_id, distance_km, duration_s, roads) "
            "VALUES ('t','Home to work','a','b',true,'trip-t',2.4,600,ARRAY['A3'])"))
        await conn.execute(text(
            "INSERT INTO trip (id, name, from_place, to_place, enabled) VALUES ('u','No route','b','a',true)"))
        for r in nh:
            await conn.execute(text(
                "INSERT INTO nh_event (record_id, scope, kind, status, start_time, end_time, roads, segments, "
                "version_time, last_seen) VALUES (:rid, 'planned', :kind, 'active', CAST(:start AS timestamptz), "
                "CAST(:end AS timestamptz), ARRAY['A3'], CAST('[{\"description\":\"A3 near the river\",\"points\":[]}]' "
                "AS jsonb), now(), now())"), r)
            await conn.execute(text("INSERT INTO nh_event_corridor (record_id, corridor_id) VALUES (:rid, 'trip-t')"), r)
    if tfl:
        client = TflClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=tfl)), max_attempts=1)
        await tfl_sync.sync(engine, client)
    if flood:
        body = {"type": "Polygon", "coordinates": FLOOD_AREA}
        feed = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(
            200, json={"items": [flood_item_json("A", level=2)]} if r.url.path.endswith("/id/floods")
            else {"type": "Feature", "geometry": body})))
        await flood_sync.sync(engine, feed)


@needs_db
def test_conditions_on_a_trip(schema: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "tok")
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        asyncio.run(load(
            engine,
            tfl=[tfl_item("on", ON_ROUTE, hasClosures=True, severity="Serious"),
                 tfl_item("near", NEARBY), tfl_item("brum", BRUM), tfl_item("noimpact", ON_ROUTE, severity="No impact"),
                 tfl_item("soon", ON_ROUTE, startDateTime=stamp(days=3), endDateTime=stamp(days=4)),
                 tfl_item("ended", ON_ROUTE, endDateTime=stamp(days=-1))],
            nh=[nh_row("c1", "closure", stamp(hours=-2)), nh_row("s1", "speed", stamp(days=2), stamp(days=3)),
                nh_row("old", "closure", stamp(days=-9), stamp(days=-8))],
            flood=True,
        ))
        app.state.engine = engine
        cl = TestClient(app)
        assert cl.get("/v1/trips/t/conditions").status_code == 401
        assert cl.get("/v1/trips/nope/conditions", headers=AUTH).status_code == 404
        assert cl.get("/v1/trips/u/conditions", headers=AUTH).status_code == 409  # no route yet

        r = cl.get("/v1/trips/t/conditions", headers=AUTH).json()
        assert r["trip"]["name"] == "Home to work" and r["trip"]["duration_min"] == 10
        now_ids = [i["id"] for i in r["now"]["items"]]
        assert set(now_ids) == {"c1", "on", "A"}  # the nearby, distant, ended and later ones are not "now"
        assert r["now"]["status"] == "severe" and r["now"]["by_source"] == {"nh": 1, "tfl": 1, "flood": 1, "ukpn": 0}
        assert now_ids[0] in ("c1", "on")  # the worst first
        assert [i["id"] for i in r["planned"]["items"]] == ["s1", "soon"]
        assert "3 problems reported" in r["summary"] and "2 planned items" in r["summary"]

        wide = cl.get("/v1/trips/t/conditions?buffer_m=400", headers=AUTH).json()
        assert "near" in {i["id"] for i in wide["now"]["items"]}

        # leaving in three days: what starts by then is "now"; floods are not claimed that far ahead
        later = cl.get("/v1/trips/t/conditions", headers=AUTH, params={"when": stamp(days=3, hours=1)}).json()
        ids = {i["id"] for i in later["now"]["items"]}
        assert "soon" in ids and "A" not in ids and "c1" in ids
        assert any("Flood warnings are not shown" in n for n in later["caveats"])

        everything = cl.get("/v1/conditions", headers=AUTH).json()
        by = {t["trip"]["id"]: t for t in everything["trips"]}
        assert by["t"]["now"]["status"] == "severe" and "no route yet" in by["u"]["error"]

        assert cl.get("/v1/trips/t/conditions?days=1", headers=AUTH).json()["planned"]["items"] == []

        # the assistant: one trip by id, or all of them
        one = cl.post("/v1/ask/road_conditions", json={"trip": "t"}, headers=AUTH).json()
        assert one["text"].startswith("Home to work: 3 problems reported")
        every = cl.post("/v1/ask/road_conditions?format=text", headers=AUTH).text
        assert every.startswith("Home to work: 3 problems") and "No route" not in every  # the unrouted one is left out
        assert cl.post("/v1/ask/road_conditions", json={"trip": "nope"}, headers=AUTH).status_code in (404, 422)
    finally:
        asyncio.run(engine.dispose())


@needs_db
def test_a_clear_route_says_so(schema: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "tok")
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        asyncio.run(load(engine, tfl=[tfl_item("brum", BRUM)], nh=[], flood=False))
        app.state.engine = engine
        r = TestClient(app).get("/v1/trips/t/conditions", headers=AUTH).json()
        assert r["now"]["status"] == "clear" and r["now"]["items"] == []
        assert r["summary"] == "Nothing reported on Home to work right now."
    finally:
        asyncio.run(engine.dispose())


def test_home_assistant_gets_a_status_and_a_planned_count_for_each_routed_trip() -> None:
    from app import ha_publish

    routed = {"trip": {"id": "home-to-work", "name": "Home to work"}, "summary": "s",
              "now": {"status": "minor", "count": 1, "items": [{"source": "tfl", "headline": "Works", "level": 1}]},
              "planned": {"days": 7, "count": 1, "items": [{"source": "nh", "headline": "x", "starts": "2026-10-25T01:00:00+00:00"}]}}  # fmt: skip
    d = ha_publish.trips_device([routed, {"trip": {"id": "u"}, "error": "no route yet"}])
    by = {s.key: s for s in d.sensors}
    assert list(by) == ["home_to_work_conditions", "home_to_work_planned"]
    assert by["home_to_work_conditions"].state == "minor" and by["home_to_work_conditions"].attributes["problems"] == 1
    assert by["home_to_work_planned"].state == 1
    assert by["home_to_work_planned"].attributes["next_starts"] == "2026-10-25T01:00:00+00:00"
    with pytest.raises(ValueError):
        ha_publish.trips_device([{"trip": {"id": "u"}, "error": "no route yet"}])
