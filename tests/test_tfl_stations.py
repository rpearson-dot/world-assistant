"""TfL stations: parsing StopPoint answers, storing them, the API, the assistant tool, the graph and the pages."""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.graph import model
from app.integrations.tflstations.normalise import alert_kind, alerts, stations

NOW = datetime(2026, 10, 23, 15, tzinfo=UTC)
URL = os.environ.get("WA_TEST_DATABASE_URL")
H = {"Authorization": "Bearer tok"}


def _prop(cat: str, key: str, value: str) -> dict[str, str]:
    return {"category": cat, "key": key, "value": value}


WATERLOO = {
    "naptanId": "940GZZLUWLO", "stopType": "NaptanMetroStation", "commonName": "Waterloo Underground Station",
    "hubNaptanCode": "HUBWAT", "lat": 51.5036, "lon": -0.1143, "modes": ["tube"],
    "lines": [{"id": "jubilee", "name": "Jubilee"}, {"id": "northern", "name": "Northern"}],
    "additionalProperties": [_prop("Facility", "Lifts", "4"), _prop("Facility", "Toilets", "yes"),
                             _prop("Zone", "Zone", "1"), _prop("Accessibility", "AccessViaLift", "Yes")],
    "children": [
        {"naptanId": "940GZZLUWLO1", "stopType": "NaptanMetroAccessArea", "commonName": "Waterloo", "lat": 0, "lon": 0,
         "children": [
             {"naptanId": "940GZZLUWLO-E1", "stopType": "NaptanMetroEntrancePoint", "commonName": "Waterloo Road entrance",
              "lat": 51.5033, "lon": -0.1130},
             {"naptanId": "940GZZLUWLO-P1", "stopType": "NaptanMetroPlatform", "commonName": "Jubilee line platform",
              "lines": [{"name": "Jubilee"}], "lat": 51.5035, "lon": -0.1140,
              "additionalProperties": [_prop("Direction", "Towards", "Stratford")]},
         ]},
        {"naptanId": "940GZZLUWLO-E2", "stopType": "NaptanMetroEntrancePoint", "commonName": "Concourse entrance",
         "lat": 51.5031, "lon": -0.1135},
    ],
}  # fmt: skip
WIMBLEDON_HUB = {
    "naptanId": "HUBWIM", "stopType": "TransportInterchange", "commonName": "Wimbledon", "lat": 51.4214, "lon": -0.2064,
    "children": [
        {"naptanId": "940GZZLUWIM", "stopType": "NaptanMetroStation", "commonName": "Wimbledon Underground Station",
         "lat": 51.4214, "lon": -0.2064, "modes": ["tube"], "lines": [{"name": "District"}]},
        {"naptanId": "940GZZCRWIM", "stopType": "NaptanMetroStation", "commonName": "Wimbledon Tram Stop",
         "lat": 51.4213, "lon": -0.2063, "modes": ["tram"], "lines": [{"name": "Tram"}]},
    ],
}  # fmt: skip
NOWHERE = {"naptanId": "940GZZLUXXX", "stopType": "NaptanMetroStation", "commonName": "No position", "lat": 0, "lon": 0}
BUS = {"naptanId": "490000254W", "stopType": "NaptanPublicBusCoachTram", "commonName": "Waterloo", "lat": 51.5, "lon": -0.11}

DISRUPTIONS = [
    {"atcoCode": "940GZZLUWLO", "stationAtcoCode": "940GZZLUWLO", "commonName": "Waterloo Underground Station",
     "description": "Waterloo: No step free access between platform and street level due to a faulty lift.",
     "fromDate": "2026-10-22T06:00:00Z", "toDate": "2026-10-30T23:59:00Z"},
    {"atcoCode": "940GZZLUWIM", "commonName": "Wimbledon Underground Station",
     "description": "The escalator to the District line is out of service.", "toDate": "2026-11-02T00:00:00Z"},
    {"atcoCode": "940GZZLUOLD", "description": "Lift closed.", "toDate": "2026-10-01T00:00:00Z"},  # over
    {"atcoCode": "940GZZLUWLO", "description": ""},  # nothing said
]  # fmt: skip


def test_stations_entrances_platforms_and_facilities() -> None:
    rows, parts = stations({"stopPoints": [WATERLOO, WIMBLEDON_HUB, NOWHERE, BUS]}, NOW)
    by = {r["id"]: r for r in rows}
    assert set(by) == {"940GZZLUWLO", "940GZZLUWIM", "940GZZCRWIM"}  # no bus stop, nothing without a position
    w = by["940GZZLUWLO"]
    assert (w["name"], w["hub"], w["zone"], w["lines"]) == ("Waterloo", "HUBWAT", "1", ["Jubilee", "Northern"])
    assert w["facilities"]["Facility"] == {"Lifts": "4", "Toilets": "yes"} and w["step_free"] == "AccessViaLift: Yes"
    assert (w["entrances"], w["platforms"]) == (2, 1)
    plat = next(p for p in parts if p["kind"] == "platform")
    assert (plat["towards"], plat["lines"]) == ("Stratford", ["Jubilee"])
    assert by["940GZZLUWIM"]["hub"] == by["940GZZCRWIM"]["hub"] == "HUBWIM"
    assert (by["940GZZCRWIM"]["name"], by["940GZZLUWIM"]["name"]) == ("Wimbledon Tram", "Wimbledon")
    assert stations([WATERLOO], NOW)[0][0]["id"] == "940GZZLUWLO"  # a plain list works too


def test_closures_now() -> None:
    got = alerts(DISRUPTIONS, NOW)
    assert [(a["station_id"], a["kind"]) for a in got] == [("940GZZLUWLO", "lift"), ("940GZZLUWIM", "escalator")]
    assert got[0]["ends_at"] == datetime(2026, 10, 30, 23, 59, tzinfo=UTC) and got[0]["name"] == "Waterloo"
    assert alerts(DISRUPTIONS + DISRUPTIONS, NOW) == got  # the same closure twice is one
    assert (alert_kind("Step-free access unavailable"), alert_kind("Entrance closed"), alert_kind("Busy")) == (
        "step_free", "entrance", "other")
    assert alerts({"not": "a list"}, NOW) == []


def test_the_graph_has_stations_lines_entrances_closures_and_hubs() -> None:
    rows, parts = stations([WATERLOO, WIMBLEDON_HUB], NOW)
    closures = [{**a, "name": "Waterloo"} for a in alerts(DISRUPTIONS, NOW)]
    g = model.build(place_rows=[{"id": "home", "name": "Home"}], trip_rows=[], corridor_rows=[], event_rows=[],
                    event_corridors=[], station_rows=[], location_rows=[], vehicle_rows=[], tracker_rows=[],
                    rail_station_rows=rows, rail_part_rows=parts, rail_closure_rows=closures,
                    near_pairs=[("RailStation", "940GZZLUWIM", "home", 0.8)])  # fmt: skip
    labels = {n.label for n in g.nodes}
    assert {"RailStation", "Line", "Entrance", "StationClosure"} <= labels
    rel = {(r.type, r.start, r.end) for r in g.rels}
    assert ("SERVES", ("Line", "Jubilee"), ("RailStation", "940GZZLUWLO")) in rel
    assert ("HAS", ("RailStation", "940GZZLUWLO"), ("Entrance", "940GZZLUWLO-E1")) in rel
    assert ("INTERCHANGE", ("RailStation", "940GZZCRWIM"), ("RailStation", "940GZZLUWIM")) in rel
    assert ("NEAR", ("RailStation", "940GZZLUWIM"), ("Place", "home")) in rel
    assert sum(r.type == "AT" for r in g.rels) == 2
    assert all(n.label in model.LABELS for n in g.nodes) and all(r.type in model.TYPES for r in g.rels)
    platforms = [n for n in g.nodes if n.id.endswith("-P1")]
    assert platforms == []  # platforms stay in Postgres (the API has them); the graph keeps entrances


@pytest.mark.parametrize("module", ["app.tfl", "app.graph_page"])
def test_page_scripts_parse(module: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    page = importlib.import_module(module).PAGE
    script = re.search(r"<script>(.*)</script>", page, re.DOTALL)
    assert script
    r = subprocess.run([node, "--check", "-"], input=script.group(1), capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr


@pytest.fixture
def schema() -> Iterator[None]:
    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    path = Path(__file__).parent.parent / "migrations" / "versions" / "0030_tfl_station.py"
    spec = importlib.util.spec_from_file_location("m0030", path)
    assert spec and spec.loader
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    with sync.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        for t in ("tfl_station_alert", "tfl_station_part", "tfl_station", "place", "app_setting"):
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        conn.execute(text("CREATE TABLE place (id text primary key, name text, kind text, postcode text, lat float8, "
                          "lon float8, geog geography(Point, 4326) GENERATED ALWAYS AS "
                          "(ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography) STORED)"))  # fmt: skip
        conn.execute(text("INSERT INTO place (id, name, kind, lat, lon) VALUES ('home', 'Home', 'home', 51.4205, -0.2070)"))
        with Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()
    yield
    with sync.begin() as conn:
        for t in ("tfl_station_alert", "tfl_station_part", "tfl_station", "place"):
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
    sync.dispose()


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_stored_and_asked_through_the_api_and_the_assistant(schema: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import assistant
    from app.config import get_settings
    from app.integrations.tfl.client import TflError
    from app.integrations.tflstations import sync
    from app.main import app

    monkeypatch.setenv("API_TOKEN", "tok")
    get_settings.cache_clear()
    engine = create_async_engine(URL, poolclass=NullPool)  # type: ignore[arg-type]
    now = datetime.now(UTC)
    rows, parts = stations([WATERLOO, WIMBLEDON_HUB], now)
    assert await sync.store_stations(engine, rows, parts) == {"stations": 3, "entrances": 2, "platforms": 1}
    live = [{**d, "toDate": (now + timedelta(days=3)).isoformat()} for d in DISRUPTIONS[:2]]
    assert await sync.store_alerts(engine, alerts(live, now)) == {"closures": 2, "lifts": 1}
    with pytest.raises(TflError, match="keeping the stored ones"):  # one station back instead of three
        await sync.store_stations(engine, rows[:1], [])

    app.state.engine = engine
    c = TestClient(app)
    found = c.get("/v1/stations?q=waterloo", headers=H).json()
    assert found["stations"][0]["id"] == "940GZZLUWLO" and found["stations"][0]["closures"] == 1
    assert found["stations"][0]["has"] == {"Lifts": "4", "Toilets": "yes"}
    one = c.get("/v1/stations/940GZZLUWLO", headers=H).json()
    assert len(one["entrance_list"]) == 2 and one["platform_list"][0]["towards"] == "Stratford"
    assert one["closures"][0]["kind"] == "lift"
    # Wimbledon: asking about the tram stop finds the escalator at the Underground station in the same building
    wim = c.get("/v1/stations/alerts?station=940GZZCRWIM", headers=H).json()
    assert [a["kind"] for a in wim["alerts"]] == ["escalator"] and wim["covered"] == ["940GZZCRWIM"]
    swr = c.get("/v1/stations/alerts?station=910GWORCSPK", headers=H).json()
    assert swr["alerts"] == [] and swr["covered"] == []  # not TfL's: no lift data, and the page says so
    near = c.get("/v1/stations/near?km=1", headers=H).json()
    assert {s["id"] for s in near["stations"]} == {"940GZZLUWIM", "940GZZCRWIM"}
    assert c.get("/v1/stations/nope", headers=H).status_code == 404
    lifts = c.get("/v1/stations/alerts?lifts_only=true", headers=H).json()
    assert [a["station_id"] for a in lifts["alerts"]] == ["940GZZLUWLO"]

    class Req:
        class app:
            class state:
                pass

    Req.app.state.engine = engine
    monkeypatch.setattr(assistant, "_available", lambda request: _all())
    said = await assistant.run_tool(Req(), "station", {"name": "Waterloo"})  # type: ignore[arg-type]
    assert said["text"].startswith("Waterloo, zone 1: Jubilee, Northern.") and "faulty lift" in said["text"]
    around = await assistant.run_tool(Req(), "station", {"km": 1})  # type: ignore[arg-type]
    assert "escalator" in around["text"] and "Near home" in around["text"]
    await engine.dispose()
    get_settings.cache_clear()


async def _all() -> set[str]:
    from app import assistant

    return set(assistant.TOOLS)
