"""UK Power Networks: reading records, finding datasets by title, syncing, the API, the assistant, HA and the graph."""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

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
from app.integrations.ukpn import client as uc
from app.integrations.ukpn import normalise as N

NOW = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
URL = os.environ.get("WA_TEST_DATABASE_URL")
HOME = (51.4024, -0.2550)  # New Malden

NEO = {  # National Energy Outage, as the portal sent it on 24 October 2026 (one still in progress)
    "fault_id": "INCD-123456-Z", "postcode": '[\\KT3 4\\", \\KT3 3\\"]"', "licence_area": "South East England",
    "voltage": "LV", "status": "Awaiting", "restored": 20, "etr": "2026-10-24T11:30:00+00:00", "confirmed_off": 123.0,
    "planned": "FALSE", "as_at_date": "2026-10-24T08:06:18+00:00", "outage_start_date": "2026-10-24T07:41:00+00:00",
    "date_of_restoration": None, "geo_point_2d": {"lon": HOME[1] + 0.002, "lat": HOME[0] + 0.001},
}  # fmt: skip
DONE = {  # one already over but still listed (status Complete, date_of_restoration set)
    "fault_id": "INCD-106731-V", "postcode": '[\\KT20 5\\"]"', "status": "Complete", "restored": 8, "confirmed_off": 0.0,
    "planned": "FALSE", "etr": "2026-10-23T03:00:00+00:00", "outage_start_date": "2026-10-22T23:52:00+00:00",
    "date_of_restoration": "2026-10-23T02:20:00+00:00", "geo_point_2d": {"lon": -0.2235, "lat": 51.2954},
}  # fmt: skip
LIVE = {  # a planned cut far away (Bishop's Stortford)
    "fault_id": "INCD-999", "postcode": '[\\CM23 2\\"]"', "status": "Planned", "planned": "TRUE", "confirmed_off": 12,
    "outage_start_date": "2026-10-24T09:00:00+00:00", "etr": "2026-10-24T15:00:00+00:00",
    "geo_point_2d": {"lon": 0.16, "lat": 51.87},
}  # fmt: skip
WORK = {  # Open Street and Roadworks, as sent
    "permit_ref": "UK019-ABC-1", "location": "KINGSTON ROAD, NEW MALDEN", "works_description": "Remedial works",
    "work_stream": "Network Operations Faults", "actualstartdate": "2026-10-20", "odp_end_date": "2026-10-22",
    "highway_authority1": "ROYAL BOROUGH OF KINGSTON", "geo_point_2d": {"lon": HOME[1] - 0.001, "lat": HOME[0]},
}  # fmt: skip
PROPOSED = {  # Proposed Street and Roadworks, as sent (road_category1 is a number, not the street)
    "permit_ref": "UK019-NEXT", "town": "KINGSTON ROAD,  NEW MALDEN", "work_category": "STANDARD",
    "works_description1": "Disconnection Or Alteration Of Supply", "road_category1": "2",
    "traffic_management_type1": "Road closure", "proposedstartdate3": "2026-11-05", "proposedenddate1": "2026-11-07",
    "geo_point_2d": {"lon": HOME[1] - 0.001, "lat": HOME[0]},
}  # fmt: skip


def test_power_cuts_as_the_portal_sends_them() -> None:
    f = N.fault(NEO, NOW)
    assert (f["id"], f["planned"], f["status"], f["active"], f["customers"], f["off_now"]) == (
        "INCD-123456-Z", False, "Awaiting", True, 143, 123)
    assert f["postcodes"] == ["KT3 4", "KT3 3"] and f["restore_at"] == datetime(2026, 10, 24, 11, 30, tzinfo=UTC)
    assert f["started_at"] == datetime(2026, 10, 24, 7, 41, tzinfo=UTC) and f["lat"] == pytest.approx(HOME[0] + 0.001)
    d = N.fault(DONE, NOW)
    assert (d["active"], d["ended_at"], d["restore_at"].hour) == (False, datetime(2026, 10, 23, 2, 20, tzinfo=UTC), 3)
    g = N.fault(LIVE, NOW)
    assert (g["planned"], g["postcodes"], g["customers"]) == (True, ["CM23 2"], 12)
    assert N.postcodes("KT3 3AA;KT3 3AB") == ["KT3 3AA", "KT3 3", "KT3 3AB"] and N.sector("kt3 4zz") == "KT3 4"


def test_works_and_areas_as_the_portal_sends_them() -> None:
    w = N.works(WORK, "open", NOW)
    assert (w["id"], w["street"], w["town"], w["category"], w["description"]) == (
        "open:UK019-ABC-1", "Kingston Road", "New Malden", "Network Operations Faults", "Remedial works")
    assert w["ends_at"].date().isoformat() == "2026-10-22" and w["lon"] == pytest.approx(HOME[1] - 0.001)
    p = N.works(PROPOSED, "proposed", NOW)
    assert (p["street"], p["town"], p["traffic"], p["category"]) == ("Kingston Road", "New Malden", "Road closure", "STANDARD")
    assert p["starts_at"].date().isoformat() == "2026-11-05"
    assert N.area({"dno": "SPN", "grid_supply_point": "BEDDINGTON (SPN)", "grid_supply_point_floc": "SPN-S000000009903"},
                  "gsp") == {"kind": "gsp", "code": "SPN-S000000009903", "name": "BEDDINGTON (SPN)"}  # fmt: skip
    assert N.area({"primary": "NORTH CHEAM 33/11KV", "grid_site": "Sutton Grid", "grid_site_floc": "SPN-S1",
                   "primary_floc": "SPN-S000000008443"}, "primary")["code"] == "SPN-S000000008443"  # fmt: skip
    sec = N.area({"sitefunctionallocation": "SPN-S000000123458", "substationdesign": "GMT", "substationalias": "1123458",
                  "customer_count": 221}, "secondary")  # fmt: skip
    assert sec == {"kind": "secondary", "code": "SPN-S000000123458", "name": "substation 1123458"}


def _ds(dataset_id: str, title: str, fields: list[tuple[str, str]]) -> dict[str, Any]:
    return {"dataset_id": dataset_id, "metas": {"default": {"title": title}},
            "fields": [{"name": n, "type": t} for n, t in fields]}  # fmt: skip


CATALOG = [
    _ds("ukpn-national-energy-outage", "National Energy Outage API", [("fault_id", "text"), ("geo_point_2d", "geo_point_2d")]),
    _ds("ukpn-open-streetworks", "Open Street and Roadworks associated with UK Power Networks' activities",
        [("permit_ref", "text"), ("geo_point_2d", "geo_point_2d")]),
    _ds("ukpn-proposed-streetworks", "Proposed Street and Roadworks associated with UK Power Networks' activities",
        [("permit_ref", "text"), ("geo_point_2d", "geo_point_2d")]),
    _ds("ukpn-boundary", "UK Power Networks Boundary", [("name", "text"), ("geo_shape", "geo_shape")]),
    _ds("ukpn-idno-areas", "Independent Distribution Network Operator areas", [("name", "text"), ("geo_shape", "geo_shape")]),
    _ds("ukpn-gsp-areas", "UK Power Networks Licence Area Grid Supply Points Areas",
        [("grid_supply_point", "text"), ("grid_supply_point_floc", "text"), ("geo_shape", "geo_shape")]),
    _ds("ukpn-primary-areas", "UK Power Networks Primary Substation Distribution Areas",
        [("primary", "text"), ("primary_floc", "text"), ("geo_shape", "geo_shape")]),
    _ds("ukpn-secondary-areas", "UK Power Networks Secondary Substation Distribution Areas",
        [("substationalias", "text"), ("sitefunctionallocation", "text"), ("geo_shape", "geo_shape")]),
]  # fmt: skip
EXPORTS: dict[str, list[dict[str, Any]]] = {
    "ukpn-national-energy-outage": [NEO, LIVE, DONE],
    "ukpn-open-streetworks": [WORK],
    "ukpn-proposed-streetworks": [PROPOSED],
    "ukpn-boundary": [{"name": "SPN", "geo_shape": {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": []}}}],
    "ukpn-idno-areas": [],
    "ukpn-gsp-areas": [{"dno": "SPN", "grid_supply_point": "BEDDINGTON (SPN)", "grid_supply_point_floc": "SPN-S000000009903"}],
    "ukpn-primary-areas": [{"primary": "NORTH CHEAM 33/11KV", "grid_site": "Sutton Grid", "primary_floc": "SPN-S000000008443"}],
    "ukpn-secondary-areas": [{"sitefunctionallocation": "SPN-S000000123458", "substationdesign": "GMT",
                              "substationalias": "1123458", "customer_count": 221}],
}  # fmt: skip


def portal(seen: list[str], key_required: bool = False) -> httpx.MockTransport:
    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path + "?" + (req.url.query.decode() if isinstance(req.url.query, bytes) else str(req.url.query)))
        if key_required and req.headers.get("Authorization") != "Apikey k":
            return httpx.Response(401)
        path = req.url.path.removeprefix(uc.API)
        if path == "/catalog/datasets":
            q = parse_qs(urlparse(str(req.url)).query).get("where", [""])[0].lower()
            hits = [d for d in CATALOG if all(w in d["metas"]["default"]["title"].lower().replace("'", " ")
                                               for w in q.removeprefix('search("').removesuffix('")').split()[:3])]  # fmt: skip
            return httpx.Response(200, json={"total_count": len(hits), "results": hits})
        if path.endswith("/exports/json"):
            return httpx.Response(200, json=EXPORTS.get(path.split("/")[3], []))
        return httpx.Response(404)

    return httpx.MockTransport(answer)


async def test_datasets_are_found_by_title_and_points_looked_up_in_shapes() -> None:
    uc.forget()
    seen: list[str] = []
    async with uc.UkpnClient("k", transport=portal(seen, key_required=True)) as c:
        ds = await c.dataset("gsp")
        assert (ds.id, ds.shape, ds.point) == ("ukpn-gsp-areas", "geo_shape", None)
        got = await c.at_point(ds, *HOME)
        assert got[0]["grid_supply_point"] == "BEDDINGTON (SPN)"
        from urllib.parse import unquote_plus

        asked = unquote_plus(seen[-1])
        assert "intersects(geo_shape, geom'POINT(-0.255 51.4024)')" in asked
        with pytest.raises(uc.UkpnError, match="no dataset titled"):
            await c.dataset("rota")
    uc.forget()
    async with uc.UkpnClient("", transport=portal([], key_required=True)) as c:
        with pytest.raises(uc.UkpnError, match="make an API key"):
            await c.dataset("faults")
    uc.forget()


# --- end to end, against PostGIS ---------------------------------------------------------------------------------


@pytest.fixture
def schema() -> Iterator[None]:
    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    path = Path(__file__).parent.parent / "migrations" / "versions" / "0031_ukpn.py"
    spec = importlib.util.spec_from_file_location("m0031", path)
    assert spec and spec.loader
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    tables = ("ukpn_carbon", "ukpn_place_area", "ukpn_works", "ukpn_fault_history", "ukpn_fault", "place", "app_setting")
    with sync.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        for t in tables:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        conn.execute(text("CREATE TABLE place (id text primary key, name text, kind text, postcode text, lat float8, "
                          "lon float8, geog geography(Point, 4326) GENERATED ALWAYS AS "
                          "(ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography) STORED)"))  # fmt: skip
        conn.execute(text("CREATE TABLE app_setting (key text primary key, value jsonb not null, "
                          "updated_at timestamptz not null default now())"))  # fmt: skip
        conn.execute(text("INSERT INTO place (id, name, kind, postcode, lat, lon) VALUES "
                          f"('home', 'Home', 'home', 'KT3 4ZZ', {HOME[0]}, {HOME[1]})"))  # fmt: skip
        with Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()
    yield
    with sync.begin() as conn:
        for t in tables:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
    sync.dispose()


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_synced_and_asked_everywhere(schema: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import assistant
    from app.config import get_settings
    from app.ha_publish import power_device
    from app.integrations.ukpn import sync
    from app.main import app

    uc.forget()
    monkeypatch.setenv("API_TOKEN", "tok")
    get_settings.cache_clear()
    engine = create_async_engine(URL, poolclass=NullPool)  # type: ignore[arg-type]
    now = datetime.now(UTC)
    assert not await sync.covered(engine)  # nothing is read before the places are looked up
    async with uc.UkpnClient("k", transport=portal([])) as c:
        placed = await sync.sync_places(engine, c, now)
        assert placed["areas"] == 4 and not placed.get("skipped")  # rota is a document, not looked up
        assert await sync.covered(engine)
        got = await sync.sync_faults(engine, c, now)
        assert got == {"power_cuts": 2, "finished_in_feed": 1, "planned": 1, "placed": 2, "ended": 1, "at_home": True}  # fmt: skip
        # (ended: the Complete one goes straight into the history as over)
        assert (await sync.sync_works(engine, c, "open", now))["open_works"] == 1
        assert (await sync.sync_works(engine, c, "proposed", now))["proposed_works"] == 1
        EXPORTS["ukpn-national-energy-outage"] = []
        assert (await sync.sync_faults(engine, c, now))["power_cuts"] == 0  # 2 stored: an empty list is fine
        EXPORTS["ukpn-national-energy-outage"] = [NEO, LIVE, DONE]
        await sync.sync_faults(engine, c, now)

    app.state.engine = engine
    h = {"Authorization": "Bearer tok"}
    c = TestClient(app)
    supply = c.get("/v1/power/supply", headers=h).json()
    assert supply["on_network"] is True and supply["power_cut_now"] is True
    assert (supply["grid_supply_point"]["name"], supply["primary"]["code"], supply["secondary"]["name"]) == (
        "BEDDINGTON (SPN)", "SPN-S000000008443", "substation 1123458")
    cuts = c.get("/v1/power/cuts?km=5", headers=h).json()
    assert cuts["count"] == 1 and cuts["cuts"][0]["at_place"] and cuts["across_network"] == 2  # Bishop's Stortford is far
    works = c.get("/v1/power/works", headers=h).json()
    assert (works["open"], works["proposed"]) == (1, 1)
    assert c.get("/v1/power/carbon", headers=h).status_code == 404  # dropped in 0.21.1

    class Req:
        class app:
            class state:
                pass

    Req.app.state.engine = engine

    async def everything(request: object) -> set[str]:
        return set(assistant.TOOLS)

    monkeypatch.setattr(assistant, "_available", everything)
    said = (await assistant.run_tool(Req(), "power", {}))["text"]  # type: ignore[arg-type]
    assert said.startswith("There is a power cut at this place.") and "143 homes" in said
    fed = (await assistant.run_tool(Req(), "power", {"about": "supply"}))["text"]  # type: ignore[arg-type]
    assert "grid supply point BEDDINGTON (SPN)" in fed and "local substation substation 1123458" in fed

    dev = power_device(supply, cuts, works)
    states = {s.key: s.state for s in dev.sensors}
    assert states["power_cut_home"] == "on" and states["power_cuts_near"] == 1 and "carbon_intensity" not in states

    async with engine.connect() as conn:
        rows = [dict(r._mapping) for r in await conn.execute(text("SELECT place_id, kind, code, name FROM ukpn_place_area "
                                                                   "WHERE kind IN ('gsp', 'primary', 'secondary')"))]  # fmt: skip
    _, rels = model.power([], [], [], rows)
    chain = {(r.start, r.end) for r in rels}
    assert (("Substation", "gsp:SPN-S000000009903"), ("Substation", "primary:SPN-S000000008443")) in chain
    assert (("Substation", "secondary:SPN-S000000123458"), ("Place", "home")) in chain
    await engine.dispose()
    get_settings.cache_clear()
    uc.forget()


def test_graph_power_nodes_and_work_corridors() -> None:
    nodes, rels = model.power([{"id": "INCD-1", "planned": False, "customers": 5}],
                              [{"id": "open:W1", "stage": "open", "street": "A3"}], [("open:W1", "a3-corridor")], [])  # fmt: skip
    assert {n.label for n in nodes} == {"PowerCut", "StreetWork"} and nodes[0].props["name"] == "Power cut (5 homes)"
    assert rels[0].type == "AFFECTS" and rels[0].end == ("Corridor", "a3-corridor")
    assert all(n.label in model.LABELS for n in nodes) and all(r.type in model.TYPES for r in rels)


def test_details_keep_records_but_drop_shapes() -> None:
    rec = {"a": 1, "geo_shape": {"type": "Feature", "geometry": {"type": "Polygon"}}, "when": NOW}
    assert uc.compact(rec) == {"a": 1, "when": str(NOW)}


def test_works_on_a_route_read_as_trip_conditions() -> None:
    from types import SimpleNamespace

    from app.conditions import ukpn_item, ukpn_level

    assert (ukpn_level("Road closure"), ukpn_level("Two-way signals"), ukpn_level("Lane closure"), ukpn_level(None)) == (3, 2, 2, 1)
    r = SimpleNamespace(id="proposed:UK019-NEXT", stage="proposed", street="Kingston Road", town="New Malden",
                        traffic="Road closure", category="Standard", description="Remedial works",
                        starts_at=NOW, ends_at=NOW + timedelta(days=2), metres=12.4)  # fmt: skip
    item = ukpn_item(r)
    assert (item["source"], item["level"], item["closure"]) == ("ukpn", 3, True)
    assert item["headline"] == "Electricity works (proposed) (Road closure)" and item["where"] == "Kingston Road, New Malden"


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_hourly_until_a_cut_is_at_home_and_every_cut_is_kept(schema: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings
    from app.integrations.ukpn import sync
    from app.main import app

    uc.forget()
    monkeypatch.setenv("API_TOKEN", "tok")
    get_settings.cache_clear()
    engine = create_async_engine(URL, poolclass=NullPool)  # type: ignore[arg-type]
    t0 = datetime.now(UTC).replace(microsecond=0)
    feed = EXPORTS["ukpn-national-energy-outage"]
    saved = list(feed)
    try:
        assert (await sync.faults_due(engine, t0))[0]  # never read: read now
        feed[:] = [LIVE]  # one cut, far from home (Bishop's Stortford)
        async with uc.UkpnClient("k", transport=portal([])) as c:
            got = await sync.sync_faults(engine, c, t0)
            assert got["at_home"] is False
            assert not (await sync.faults_due(engine, t0 + timedelta(minutes=20)))[0]  # hourly
            due, why = await sync.faults_due(engine, t0 + timedelta(minutes=60))
            assert due and why == "every 60 minutes"

            feed[:] = [LIVE, {**NEO, "etr": (t0 + timedelta(hours=1)).isoformat(),
                              "outage_start_date": (t0 - timedelta(minutes=30)).isoformat()}]  # now one at home  # fmt: skip
            t1 = t0 + timedelta(minutes=60)
            assert (await sync.sync_faults(engine, c, t1))["at_home"] is True
            due, why = await sync.faults_due(engine, t1 + timedelta(minutes=15))
            assert due and why == "every 15 minutes (a cut is at home)"

            feed[:] = [{**NEO, "confirmed_off": 190, "etr": (t1 + timedelta(hours=2)).isoformat()}]
            t2 = t1 + timedelta(minutes=15)
            got = await sync.sync_faults(engine, c, t2)
            assert got["ended"] == 1  # the Bishop's Stortford cut left the feed: closed in the history
            t3 = t2 + timedelta(hours=3)  # power back at home: still listed, as Complete
            feed[:] = [{**NEO, "status": "Complete", "confirmed_off": 0, "restored": 210, "etr": (t1 + timedelta(hours=2)).isoformat(),
                        "date_of_restoration": (t3 - timedelta(minutes=5)).isoformat()}]  # fmt: skip
            got = await sync.sync_faults(engine, c, t3)
            assert (got["ended"], got["at_home"]) == (1, False)
            assert not (await sync.faults_due(engine, t3 + timedelta(minutes=15)))[0]  # back to hourly
        async with engine.connect() as conn:
            kept = {r.id: r for r in await conn.execute(text("SELECT * FROM ukpn_fault_history"))}
        home = kept["INCD-123456-Z"]
        assert home.home and home.places == ["home"] and home.customers_max == 210
        assert home.ended_at == t3 - timedelta(minutes=5)  # when UK Power Networks says power came back
        assert home.first_restore_at == t0 + timedelta(hours=1) and home.last_restore_at == t1 + timedelta(hours=2)
        assert kept["INCD-999"].ended_at == t2 and not kept["INCD-999"].home

        app.state.engine = engine
        h = TestClient(app).get("/v1/power/history", headers={"Authorization": "Bearer tok"}).json()
        assert (h["count"], h["unplanned"], h["late"]) == (1, 1, 1)  # back later than first promised
        assert h["cuts"][0]["hours"] == pytest.approx(4.8, abs=0.1)  # from 30 minutes before t0 to t3 and h["recorded_network_wide"] == 2
    finally:
        feed[:] = saved
        await engine.dispose()
        get_settings.cache_clear()
        uc.forget()
