"""GRIDSERVE EV chargers: reading the API, storing it, and asking about it."""

from __future__ import annotations

import copy
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
from sqlalchemy.pool import NullPool

from app.graph import model
from app.integrations.gridserve import normalise as N
from app.integrations.gridserve.client import GridserveClient, GridserveError, unwrap

URL = os.environ.get("WA_TEST_DATABASE_URL")
VERSIONS = Path(__file__).parent.parent / "migrations" / "versions"
HOME = (51.5074, -0.1278)


def _evse(uid: str, status: str, standard: str, watts: int, tariff: str = "GS22") -> dict[str, Any]:
    return {"uid": uid, "status": status, "capabilities": ["CREDIT_CARD_PAYABLE"], "last_updated": "2026-10-31T07:16:26Z",
            "connectors": [{"id": "1", "standard": standard, "format": "CABLE", "power_type": "AC_3_PHASE" if standard ==
                            "IEC_62196_T2" else "DC", "max_voltage": 0, "max_amperage": 0, "max_electric_power": watts,
                            "tariff_ids": [tariff]}]}  # fmt: skip


def _loc(lid: str, name: str, lat: float, lon: float, evses: list[dict[str, Any]], publish: bool = True) -> dict[str, Any]:
    return {"country_code": "GB", "party_id": "GRD", "id": lid, "publish": publish, "name": name, "address": "Services",
            "city": "Town", "postal_code": "AB1 2CD", "country": "GBR",
            "coordinates": {"latitude": str(lat), "longitude": str(lon)}, "parking_type": "ON_STREET", "evses": evses,
            "operator": {"name": "GRIDSERVE-GRD"}, "facilities": ["RESTAURANT", "WIFI"], "time_zone": "Europe/London",
            "opening_times": {"twentyfourseven": True}, "last_updated": "2026-09-17T10:06:09Z"}  # fmt: skip


# As GRIDSERVE sends them (shapes from a real answer): each lead is its own EVSE, numbered on its charger's id.
LOCATIONS = [
    _loc("near", "EH - MOTO Near", 51.5100, -0.1300, [
        _evse("GB-GRD-EGS1-1", "CHARGING", "IEC_62196_T2_COMBO", 50000),  # this charger is in use...
        _evse("GB-GRD-EGS1-2", "AVAILABLE", "CHADEMO", 50000),  # ...so its CHAdeMO lead is not free
        _evse("GB-GRD-EGS1-3", "AVAILABLE", "IEC_62196_T2", 22000),
        _evse("GB-GRD-EGS2-1", "AVAILABLE", "IEC_62196_T2_COMBO", 350000, "GS13"),
        _evse("GB-GRD-EGS3-1", "Out_Of_Order", "IEC_62196_T2_COMBO", 350000, "GS13"),
        _evse("GB-GRD-EGS4-1", "REMOVED", "IEC_62196_T2_COMBO", 350000),
    ]),
    _loc("hub", "HU - Priory Fields", 51.62, -0.20, [_evse("GB-GRD-H1-1", "AVAILABLE", "IEC_62196_T2_COMBO", 360000)]),
    _loc("hidden", "EH - Not open yet", 51.5100, -0.1300, [], publish=False),
    _loc("far", "EH - MOTO Far", 52.4862, -1.8904, [_evse("GB-GRD-F1-1", "AVAILABLE", "CHADEMO", 50000)]),
]
TARIFFS = [
    {"country_code": "GB", "party_id": "GRD", "id": "GS22", "currency": "GBP", "type": None,
     "elements": [{"price_components": [{"type": "ENERGY", "price": 0.6583, "vat": 20, "step_size": 1}]}]},
    {"country_code": "GB", "party_id": "GRD", "id": "GS13", "currency": "GBP",
     "elements": [{"price_components": [{"type": "ENERGY", "price": 0.7417, "vat": 20, "step_size": 1}]}]},
]  # fmt: skip


def portal(locations: list[dict[str, Any]] | None = None, seen: list[httpx.Request] | None = None) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(req)
        if req.headers.get("ec-subscription-key") != "k":
            return httpx.Response(401, json={"message": "Access denied"})
        if req.url.path.endswith("/locations"):
            return httpx.Response(200, json=LOCATIONS if locations is None else locations)
        if req.url.path.endswith("/tariffs"):
            return httpx.Response(200, json={"tariffs": TARIFFS})  # the shape the developer document shows
        return httpx.Response(404)

    return httpx.MockTransport(handler)


# --- reading ----------------------------------------------------------------------------------------------------------

def test_sites_chargers_and_prices_are_read_as_sent() -> None:
    row, chargers = N.site(LOCATIONS[0])  # type: ignore[misc]
    assert (row["id"], row["name"], row["network"], row["lat"], row["open_24h"]) == (
        "gridserve:near", "MOTO Near", "Electric Highway", 51.51, True)
    assert [c["status"] for c in chargers] == ["CHARGING", "AVAILABLE", "AVAILABLE", "AVAILABLE", "OUT_OF_ORDER"]  # removed: out
    assert chargers[1]["connectors"] == [{"type": "CHAdeMO", "dc": True, "kw": 50.0, "cable": True, "tariffs": ["GS22"]}]
    assert N.site(LOCATIONS[1])[0]["name"] == "HU - Priory Fields"  # a prefix not known is kept  # type: ignore[index]
    assert N.site(LOCATIONS[2]) is None  # not published
    assert N.tariff(TARIFFS[0])["pence_per_kwh"] == 79.0  # with VAT  # type: ignore[index]
    assert N.status("Out_Of_Order") == "OUT_OF_ORDER" and N.status("something new") == "UNKNOWN"
    assert N.asked_connector("chademo") == "CHAdeMO" and N.asked_connector("Type 2") == "Type 2"
    assert N.asked_connector("ccs") == "CCS" and N.asked_connector("hydrogen") is None


def test_a_site_counts_the_boxes_in_the_car_park_not_the_leads() -> None:
    row, chargers = N.site(LOCATIONS[0])  # type: ignore[misc]
    s = N.summary(chargers, {"GS22": 79.0, "GS13": 89.0})
    assert (s["total"], s["working"], s["free"], s["in_use"], s["out_of_order"]) == (3, 2, 1, 1, 1)
    assert s["connectors"]["CHAdeMO"] == {"total": 1, "working": 1, "free": 0, "max_kw": 50.0}  # its charger is busy
    assert s["connectors"]["CCS"]["total"] == 3 and s["max_kw"] == 350.0
    assert s["pence_per_kwh"] == {"min": 79.0, "max": 89.0}
    assert N.sentence(row["name"], s, 3.21) == (
        "MOTO Near: 2 of 3 chargers working, 1 free now, up to 350 kW, 79-89p/kWh, 3.2 km away.")
    cha = N.summary(chargers, {"GS22": 79.0}, "CHAdeMO")
    assert (cha["total"], cha["free"]) == (1, 0)
    assert N.sentence("MOTO Near", cha, None, "CHAdeMO").startswith("MOTO Near: 1 of 1 CHAdeMO chargers working, 0 free")


async def test_the_client_sends_the_key_in_a_header_and_reads_either_shape() -> None:
    seen: list[httpx.Request] = []
    async with GridserveClient("k", transport=portal(seen=seen)) as c:
        assert len(await c.locations()) == 4  # a bare list
        assert [t["id"] for t in await c.tariffs()] == ["GS22", "GS13"]  # {"tariffs": [...]}
    assert all("k" not in str(r.url) for r in seen)  # never in the URL
    async with GridserveClient("wrong", transport=portal()) as c:
        with pytest.raises(GridserveError, match="refused the key"):
            await c.locations()
    with pytest.raises(GridserveError, match="no GRIDSERVE key"):
        GridserveClient("")
    assert unwrap({"data": [{"id": 1}], "status_code": 1000}, "locations") == [{"id": 1}]  # OCPI's own envelope


def test_charging_sites_join_the_graph_near_places_and_along_corridors() -> None:
    rows = [{"id": "gridserve:near", "name": "MOTO Near", "total": 3, "working": 2, "free": 1, "max_kw": 350}]
    nodes, rels = model.charge_sites(rows, [("gridserve:near", "m1", 0.4)])
    assert nodes[0].label == "ChargeSite" and nodes[0].props["free"] == 1
    assert rels[0].type == "ALONG" and rels[0].end == ("Corridor", "m1")
    assert "ChargeSite" in model.LABELS


# --- stored and asked -------------------------------------------------------------------------------------------------

@pytest.fixture
def schema() -> Iterator[None]:
    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    spec = importlib.util.spec_from_file_location("m0034", VERSIONS / "0034_ev.py")
    assert spec and spec.loader
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    tables = ("ev_tariff", "ev_evse", "ev_site", "trip", "nh_corridor", "place", "app_setting")
    with sync.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        for t in tables:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        conn.execute(text("CREATE TABLE place (id text primary key, name text, kind text, postcode text, lat float8, "
                          "lon float8, geog geography(Point, 4326) GENERATED ALWAYS AS "
                          "(ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography) STORED)"))  # fmt: skip
        conn.execute(text("CREATE TABLE app_setting (key text primary key, value jsonb not null, "
                          "updated_at timestamptz not null default now())"))  # fmt: skip
        conn.execute(text("CREATE TABLE nh_corridor (id text primary key, name text, centreline jsonb, enabled bool default true)"))
        conn.execute(text("CREATE TABLE trip (id text primary key, name text, corridor_id text, distance_km float8, "
                          "duration_s int, roads text[], enabled bool default true)"))  # fmt: skip
        conn.execute(text("INSERT INTO place (id, name, kind, lat, lon) VALUES ('home', 'Home', 'home', :la, :lo)"),
                     {"la": HOME[0], "lo": HOME[1]})  # fmt: skip
        # a route from home north, past the hub (0.0 km off it) and nowhere near Birmingham
        conn.execute(text("INSERT INTO nh_corridor (id, name, centreline) VALUES ('trip-north', 'North', CAST(:c AS jsonb))"),
                     {"c": f"[[{HOME[0]}, {HOME[1]}], [51.62, -0.20], [51.80, -0.30]]"})  # fmt: skip
        conn.execute(text("INSERT INTO trip (id, name, corridor_id, distance_km) VALUES ('north', 'North', 'trip-north', 40)"))
        with Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()
    yield
    with sync.begin() as conn:
        for t in tables:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
    sync.dispose()


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_synced_then_asked_near_home_along_a_trip_and_by_name(schema: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import assistant
    from app.config import get_settings
    from app.integrations.gridserve import sync
    from app.main import app

    monkeypatch.setenv("API_TOKEN", "tok")
    get_settings.cache_clear()
    engine = create_async_engine(URL, poolclass=NullPool)  # type: ignore[arg-type]
    now = datetime.now(UTC)
    async with GridserveClient("k", transport=portal()) as c:
        got = await sync.sync(engine, c, now)
    assert (got["sites"], got["not_published"], got["chargers"], got["tariffs"]) == (3, 1, 7, 2)
    assert got["status"] == {"AVAILABLE": 5, "CHARGING": 1, "OUT_OF_ORDER": 1}

    # an answer that has lost most of the network changes nothing
    async with GridserveClient("k", transport=portal(locations=LOCATIONS[:1])) as c:
        with pytest.raises(GridserveError, match="kept what is stored"):
            await sync.sync(engine, c)

    app.state.engine = engine
    h = {"Authorization": "Bearer tok"}
    client = TestClient(app)
    near = client.get("/v1/ev/sites?km=25", headers=h).json()
    assert [s["name"] for s in near["sites"]] == ["MOTO Near", "HU - Priory Fields"]  # Birmingham is too far
    assert near["sites"][0]["chargers"]["free"] == 1 and near["sites"][0]["chargers"]["pence_per_kwh"]["min"] == 79.0
    cha = client.get("/v1/ev/sites?km=25&connector=chademo", headers=h).json()
    assert [s["name"] for s in cha["sites"]] == ["MOTO Near"]  # the hub has no CHAdeMO
    assert client.get("/v1/ev/sites?km=25&connector=chademo&free=true", headers=h).json()["count"] == 0  # its charger is busy
    assert client.get("/v1/ev/sites?connector=hydrogen", headers=h).status_code == 422
    by_name = client.get("/v1/ev/sites?q=Far", headers=h).json()
    assert [s["name"] for s in by_name["sites"]] == ["MOTO Far"]  # found anywhere
    one = client.get("/v1/ev/sites/gridserve:near", headers=h).json()
    assert len(one["charger_list"]) == 5 and one["facilities"] == ["RESTAURANT", "WIFI"]
    trip = client.get("/v1/ev/trips/north", headers=h).json()
    assert [s["name"] for s in trip["sites"]] == ["MOTO Near", "HU - Priory Fields"]  # in the order passed
    assert trip["sites"][1]["km_along"] > trip["sites"][0]["km_along"] and trip["sites"][1]["off_route_km"] < 0.1

    class Req:
        class app:
            class state:
                pass

    Req.app.state.engine = engine

    async def everything(request: object) -> set[str]:
        return set(assistant.TOOLS)

    monkeypatch.setattr(assistant, "_available", everything)
    said = (await assistant.run_tool(Req(), "ev_chargers", {}))["text"]  # type: ignore[arg-type]
    assert said.startswith("MOTO Near: 2 of 3 chargers working, 1 free now, up to 350 kW, 79-89p/kWh")
    along = (await assistant.run_tool(Req(), "ev_chargers", {"trip": "north", "connector": "chademo"}))["text"]  # type: ignore[arg-type]
    assert along.startswith("1 GRIDSERVE site(s) along North: MOTO Near: 1 of 1 CHAdeMO chargers working, 0 free now")
    none = (await assistant.run_tool(Req(), "ev_chargers", {"km": 1, "connector": "type2", "free_only": True}))["text"]  # type: ignore[arg-type]
    assert none == "No GRIDSERVE chargers within 1 km with Type 2."

    # a site or charger no longer listed is dropped
    fewer = copy.deepcopy(LOCATIONS)
    fewer[0]["evses"] = fewer[0]["evses"][:2]
    async with GridserveClient("k", transport=portal(locations=fewer)) as c:
        again = await sync.sync(engine, c)
    assert again["removed"]["chargers"] == 3
    await engine.dispose()
