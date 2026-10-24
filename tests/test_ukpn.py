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

NEO = {  # an ENA National Energy Outage record, as the portal flattens it
    "incidentreference": "INCD-123456-Z", "powercuttype": "Unplanned", "statusid": 2, "status": "In Progress",
    "creationdatetime": "2026-10-24T07:41:00+00:00", "estimatedrestorationdate": "2026-10-24T11:30:00+00:00",
    "nocustomeraffected": "143", "postcodesaffected": "KT3 3AA;KT3 3AB; KT3 4ZZ", "incidentdescription": "Fault on cable",
    "geopoint": {"lon": HOME[1] + 0.002, "lat": HOME[0] + 0.001},
}  # fmt: skip
LIVE = {  # the older Live Faults layout
    "incident_reference": "INCD-999", "power_cut_type": "Planned", "postcode_affected": "CM23 2AB",
    "planned_start": "24/10/2026 09:00", "estimated_restoration": "24/10/2026 15:00", "customers_affected": 12,
    "latitude": 51.87, "longitude": 0.16,
}  # fmt: skip
WORK = {
    "permit_reference_number": "UK019-ABC-1", "street_name": "Kingston Road", "town": "New Malden", "usrn": "21401234",
    "works_category": "Standard", "traffic_management_type": "Two-way signals", "activity_type": "Remedial works",
    "proposed_start_date": "2026-10-20", "proposed_end_date": "2026-10-30",
    "geo_point_2d": {"lon": HOME[1] - 0.001, "lat": HOME[0]},
}  # fmt: skip


def test_power_cuts_from_either_layout() -> None:
    f = N.fault(NEO, NOW)
    assert (f["id"], f["planned"], f["status"], f["customers"]) == ("INCD-123456-Z", False, "In Progress", 143)
    assert f["postcodes"] == ["KT3 3AA", "KT3 3AB", "KT3 4ZZ"] and f["description"] == "Fault on cable"
    assert f["restore_at"] == datetime(2026, 10, 24, 11, 30, tzinfo=UTC) and f["lat"] == pytest.approx(HOME[0] + 0.001)
    g = N.fault(LIVE, NOW)
    assert (g["id"], g["planned"], g["postcodes"], g["customers"]) == ("INCD-999", True, ["CM23 2AB"], 12)
    assert g["started_at"] == datetime(2026, 10, 24, 9, 0, tzinfo=UTC) and (g["lat"], g["lon"]) == (51.87, 0.16)


def test_works_areas_and_carbon() -> None:
    w = N.works(WORK, "open", NOW)
    assert (w["id"], w["street"], w["town"], w["usrn"], w["traffic"]) == (
        "open:UK019-ABC-1", "Kingston Road", "New Malden", "21401234", "Two-way signals")
    assert w["starts_at"].date().isoformat() == "2026-10-20" and w["lon"] == pytest.approx(HOME[1] - 0.001)
    assert N.area({"gsp_name": "Wimbledon 132kV", "gsp_id": "WIMB"}, "gsp") == {"kind": "gsp", "code": "WIMB",
                                                                                "name": "Wimbledon 132kV"}  # fmt: skip
    assert N.area({"rota_block": "K", "region": "SPN"}, "rota")["code"] == "K"
    assert N.carbon({"gsp": "WIMBLEDON GSP", "datetime": "2026-10-24T09:00:00Z", "carbon_intensity": 182.5}) == (
        "WIMBLEDON GSP", NOW, 182.5)
    assert N.same_gsp("Wimbledon 132kV", "WIMBLEDON GSP") and not N.same_gsp("Wimbledon", "Wimbledon Park North")
    rows = [(NOW + timedelta(minutes=30 * i), v) for i, v in enumerate([200, 190, 120, 110, 100, 180, 210])]
    best = N.cleanest(rows, NOW, hours=1.5)
    assert best is not None and best["now"] == 200 and best["best_start"] == (NOW + timedelta(hours=1)).isoformat()
    assert best["best_average"] == pytest.approx(110.0)


def _ds(dataset_id: str, title: str, fields: list[tuple[str, str]]) -> dict[str, Any]:
    return {"dataset_id": dataset_id, "metas": {"default": {"title": title}},
            "fields": [{"name": n, "type": t} for n, t in fields]}  # fmt: skip


CATALOG = [
    _ds("ukpn-national-energy-outage", "National Energy Outage API", [("incidentreference", "text"), ("geopoint", "geo_point_2d")]),
    _ds("ukpn-open-streetworks", "Open Street and Roadworks associated with UK Power Networks' activities",
        [("permit_reference_number", "text"), ("geo_point_2d", "geo_point_2d")]),
    _ds("ukpn-proposed-streetworks", "Proposed Street and Roadworks associated with UK Power Networks' activities",
        [("permit_reference_number", "text"), ("geo_point_2d", "geo_point_2d")]),
    _ds("ukpn-carbon-intensity", "Carbon Intensity", [("gsp", "text"), ("datetime", "datetime"), ("carbon_intensity", "double")]),
    _ds("ukpn-boundary", "UK Power Networks Boundary", [("name", "text"), ("geo_shape", "geo_shape")]),
    _ds("ukpn-idno-areas", "Independent Distribution Network Operator areas", [("name", "text"), ("geo_shape", "geo_shape")]),
    _ds("ukpn-gsp-areas", "UK Power Networks Licence Area Grid Supply Points Areas",
        [("gsp_name", "text"), ("gsp_id", "text"), ("geo_shape", "geo_shape")]),
    _ds("ukpn-primary-areas", "UK Power Networks Primary Substation Distribution Areas",
        [("primary_name", "text"), ("site_functional_location", "text"), ("geo_shape", "geo_shape")]),
    _ds("ukpn-secondary-areas", "UK Power Networks Secondary Substation Distribution Areas",
        [("secondary_name", "text"), ("site_functional_location", "text"), ("geo_shape", "geo_shape")]),
]  # fmt: skip
EXPORTS: dict[str, list[dict[str, Any]]] = {
    "ukpn-national-energy-outage": [NEO, LIVE],
    "ukpn-open-streetworks": [WORK],
    "ukpn-proposed-streetworks": [{**WORK, "permit_reference_number": "UK019-NEXT", "proposed_start_date": "2026-11-05",
                                   "proposed_end_date": "2026-11-07", "traffic_management_type": "Road closure"}],
    "ukpn-carbon-intensity": [{"gsp": "WIMBLEDON GSP", "datetime": (NOW + timedelta(minutes=30 * i)).isoformat(),
                               "carbon_intensity": v} for i, v in enumerate([200, 150, 90, 95, 180])]
                             + [{"gsp": "ELSEWHERE", "datetime": NOW.isoformat(), "carbon_intensity": 50}],
    "ukpn-boundary": [{"name": "SPN", "geo_shape": {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": []}}}],
    "ukpn-idno-areas": [],
    "ukpn-gsp-areas": [{"gsp_name": "Wimbledon 132kV", "gsp_id": "WIMB"}],
    "ukpn-primary-areas": [{"primary_name": "New Malden", "site_functional_location": "SPN-P-123"}],
    "ukpn-secondary-areas": [{"secondary_name": "Burlington Road", "site_functional_location": "SPN-S-4567"}],
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
        assert got[0]["gsp_name"] == "Wimbledon 132kV"
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
    for rows in EXPORTS.values():  # put the half-hours, starts and ends around the real clock
        for r in rows:
            if "carbon_intensity" in r:
                r["datetime"] = (now.replace(minute=0, second=0, microsecond=0)
                                 + (datetime.fromisoformat(r["datetime"]) - NOW)).isoformat()  # fmt: skip
    assert not await sync.covered(engine)  # nothing is read before the places are looked up
    async with uc.UkpnClient("k", transport=portal([])) as c:
        placed = await sync.sync_places(engine, c, now)
        assert placed["areas"] == 4 and "rota: " in placed["skipped"][0]  # no rota dataset in this fake portal
        assert await sync.covered(engine)
        assert await sync.sync_faults(engine, c, now) == {"power_cuts": 2, "planned": 1, "placed": 2, "ended": 0, "at_home": True}
        assert (await sync.sync_works(engine, c, "open", now))["open_works"] == 1
        assert (await sync.sync_works(engine, c, "proposed", now))["proposed_works"] == 1
        assert await sync.sync_carbon(engine, c, now) == {"readings": 5, "grid_supply_points": 1}
        EXPORTS["ukpn-national-energy-outage"] = []
        assert (await sync.sync_faults(engine, c, now))["power_cuts"] == 0  # 2 stored: an empty list is fine
        EXPORTS["ukpn-national-energy-outage"] = [NEO, LIVE]
        await sync.sync_faults(engine, c, now)

    app.state.engine = engine
    h = {"Authorization": "Bearer tok"}
    c = TestClient(app)
    supply = c.get("/v1/power/supply", headers=h).json()
    assert supply["on_network"] is True and supply["power_cut_now"] is True
    assert (supply["grid_supply_point"]["name"], supply["primary"]["code"], supply["secondary"]["name"]) == (
        "Wimbledon 132kV", "SPN-P-123", "Burlington Road")
    cuts = c.get("/v1/power/cuts?km=5", headers=h).json()
    assert cuts["count"] == 1 and cuts["cuts"][0]["at_place"] and cuts["across_network"] == 2  # Bishop's Stortford is far
    works = c.get("/v1/power/works", headers=h).json()
    assert (works["open"], works["proposed"]) == (1, 1)
    carbon = c.get("/v1/power/carbon?hours=1", headers=h).json()
    assert carbon["available"] and carbon["now"] == 200 and carbon["best_average"] == pytest.approx(92.5)

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
    assert "grid supply point Wimbledon 132kV" in fed and "local substation Burlington Road" in fed
    assert "g CO2 per kWh" in (await assistant.run_tool(Req(), "power", {"about": "carbon"}))["text"]  # type: ignore[arg-type]

    dev = power_device(supply, cuts, works, carbon)
    states = {s.key: s.state for s in dev.sensors}
    assert states["power_cut_home"] == "on" and states["power_cuts_near"] == 1 and states["carbon_intensity"] == 200

    async with engine.connect() as conn:
        rows = [dict(r._mapping) for r in await conn.execute(text("SELECT place_id, kind, code, name FROM ukpn_place_area "
                                                                   "WHERE kind IN ('gsp', 'primary', 'secondary')"))]  # fmt: skip
    _, rels = model.power([], [], [], rows)
    chain = {(r.start, r.end) for r in rels}
    assert (("Substation", "gsp:WIMB"), ("Substation", "primary:SPN-P-123")) in chain
    assert (("Substation", "secondary:SPN-S-4567"), ("Place", "home")) in chain
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

            feed[:] = [LIVE, {**NEO, "estimatedrestorationdate": (t0 + timedelta(hours=1)).isoformat(),
                              "creationdatetime": (t0 - timedelta(minutes=30)).isoformat()}]  # now one at home  # fmt: skip
            t1 = t0 + timedelta(minutes=60)
            assert (await sync.sync_faults(engine, c, t1))["at_home"] is True
            due, why = await sync.faults_due(engine, t1 + timedelta(minutes=15))
            assert due and why == "every 15 minutes (a cut is at home)"

            feed[:] = [{**NEO, "nocustomeraffected": "210", "estimatedrestorationdate": (t1 + timedelta(hours=2)).isoformat()}]
            t2 = t1 + timedelta(minutes=15)
            got = await sync.sync_faults(engine, c, t2)
            assert got["ended"] == 1  # the Bishop's Stortford cut left the feed: closed in the history
            feed[:] = []  # power back at home
            t3 = t2 + timedelta(hours=3)
            got = await sync.sync_faults(engine, c, t3)
            assert (got["ended"], got["at_home"]) == (1, False)
            assert not (await sync.faults_due(engine, t3 + timedelta(minutes=15)))[0]  # back to hourly
        async with engine.connect() as conn:
            kept = {r.id: r for r in await conn.execute(text("SELECT * FROM ukpn_fault_history"))}
        home = kept["INCD-123456-Z"]
        assert home.home and home.places == ["home"] and home.customers_max == 210 and home.ended_at == t3
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
