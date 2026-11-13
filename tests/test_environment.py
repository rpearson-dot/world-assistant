"""Air and water: the air quality index, reading UK-AIR and the Environment Agency, the sensors and the tools."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app import assistant_more as M
from app.ha_publish import environment_device
from app.modules.environment.sources.environment import clients as C

NOW = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)
HOME = (51.38, -0.25)
MS = lambda t: int(t.timestamp() * 1000)


@pytest.mark.parametrize(("p", "value", "index"), [
    ("no2", 40, 1), ("no2", 67, 1), ("no2", 68, 2), ("no2", 601, 10),
    ("o3", 100, 3), ("o3", 101, 4), ("pm25", 35.4, 3), ("pm25", 36, 4), ("pm25", 71, 10), ("pm10", 51, 4), ("pm10", 100, 9),
])  # fmt: skip
def test_daqi_bands_are_defras(p: str, value: float, index: int) -> None:
    assert C.daqi(p, value) == index


def test_band_names() -> None:
    assert [C.daqi_band(i) for i in (1, 3, 4, 6, 7, 9, 10)] == ["Low", "Low", "Moderate", "Moderate", "High", "High", "Very high"]


@pytest.mark.parametrize(("label", "want"), [
    ("Nitrogen dioxide (air)", "no2"), ("Ozone (air)", "o3"), ("Particulate matter < 2.5 µm (aerosol)", "pm25"),
    ("Particulate matter < 10 µm (aerosol)", "pm10"), ("PM10 particulate matter (Hourly measured)", "pm10"),
    ("Nitrogen oxides as nitrogen dioxide", "no2"), ("Sulphur dioxide (air)", None), ("Carbon monoxide", None),
])  # fmt: skip
def test_pollutants_are_known_by_their_labels(label: str, want: str | None) -> None:
    assert C.pollutant_of(label) == want


def test_station_positions_in_either_order() -> None:
    assert C.station_point({"geometry": {"coordinates": [-0.15, 51.52]}}) == (51.52, -0.15)
    assert C.station_point({"geometry": {"coordinates": [51.52, -0.15, 0]}}) == (51.52, -0.15)
    assert C.station_point({"geometry": {}}) is None


asked: list[str] = []


def ukair() -> httpx.MockTransport:
    def answer(req: httpx.Request) -> httpx.Response:
        path, q = req.url.path, parse_qs(urlparse(str(req.url)).query)
        if path.endswith("/stations"):
            assert "near" not in q  # UK-AIR's nearby search answers HTTP 500: every station is asked for
            asked.append("stations")
            return httpx.Response(200, json=[
                {"properties": {"id": 9, "label": "Edinburgh St Leonards"}, "geometry": {"coordinates": [-3.18, 55.95]}},
                {"properties": {"id": 7, "label": "Far Away"}, "geometry": {"coordinates": [-0.1, 51.45]}},
                {"properties": {"id": 3, "label": "Sutton Worcester Park"}, "geometry": {"coordinates": [-0.24, 51.38]}},
            ])  # fmt: skip
        if path.endswith("/timeseries"):
            station = q["station"][0]
            fresh, stale = MS(NOW - timedelta(minutes=50)), MS(NOW - timedelta(hours=9))
            if station == "3":
                return httpx.Response(200, json=[
                    {"id": "ts_no2", "uom": "ug.m-3", "lastValue": {"timestamp": fresh, "value": 70.0},
                     "parameters": {"phenomenon": {"label": "Nitrogen dioxide (air)"}}},
                    {"id": "ts_pm25", "uom": "ug.m-3", "lastValue": {"timestamp": fresh, "value": 80.0},
                     "parameters": {"phenomenon": {"label": "Particulate matter < 2.5 µm (aerosol)"}}},
                    {"id": "ts_o3", "uom": "ug.m-3", "lastValue": {"timestamp": stale, "value": 50.0},
                     "parameters": {"phenomenon": {"label": "Ozone (air)"}}},
                ])  # fmt: skip
            return httpx.Response(200, json=[
                {"id": "ts_far_o3", "uom": "ug.m-3", "lastValue": {"timestamp": fresh, "value": 90.0},
                 "parameters": {"phenomenon": {"label": "Ozone (air)"}}}])  # fmt: skip
        if "/getData" in path:
            sid = path.split("/")[-2]
            n = 24 if sid == "ts_pm25" else 8
            vals = [{"timestamp": MS(NOW - timedelta(hours=h)), "value": 20.0 if sid == "ts_pm25" else 110.0} for h in range(n)]
            body = {sid: {"values": vals}} if sid == "ts_far_o3" else {"values": vals}  # both shapes
            return httpx.Response(200, json=body)
        return httpx.Response(404)

    return httpx.MockTransport(answer)


async def test_air_uses_the_means_defra_does_and_the_worst_pollutant_sets_the_index() -> None:
    C.forget()
    async with httpx.AsyncClient(transport=ukair()) as c:
        r = await C.air_near(c, *HOME, now=NOW)
    no2, pm25, o3 = r["readings"]["no2"], r["readings"]["pm25"], r["readings"]["o3"]
    assert (no2["mean"], no2["index"], no2["station"]) == (70.0, 2, "Sutton Worcester Park")
    assert (pm25["value"], pm25["mean"], pm25["index"]) == (80.0, 20.0, 2)  # the 24-hour mean, not the last hour
    assert (o3["station"], o3["mean"], o3["index"]) == ("Far Away", 110.0, 4)  # the near one is stale: the next one
    assert "pm10" not in r["readings"]
    assert (r["index"], r["band"], r["worst"]) == (4, "Moderate", "Ozone")
    assert not any(x["station"].startswith("Edinburgh") for x in r["readings"].values())  # beyond 20 km


async def test_the_station_list_is_kept_a_day_and_the_last_one_used_if_uk_air_fails() -> None:
    C.forget()
    asked.clear()
    async with httpx.AsyncClient(transport=ukair()) as c:
        await C.air_near(c, *HOME, now=NOW)
        C._cache.clear()  # the ten-minute cache gone: the station list is still kept
        await C.air_near(c, *HOME, now=NOW)
    assert asked == ["stations"]
    C._ukair_stations["at"] -= C.STATIONS_S + 1  # a day on, and UK-AIR failing: the last list is used
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(500) if r.url.path.endswith("/stations") else ukair().handle_request(r))) as c:  # fmt: skip
        assert len(await C.all_stations(c)) == 3


async def test_no_recent_reading_says_so() -> None:
    C.forget()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[]))) as c:
        r = await C.air_near(c, *HOME, now=NOW)
    assert r["index"] is None and "six hours" in r["note"]


def ea() -> httpx.MockTransport:
    base = "http://environment.data.gov.uk/flood-monitoring/id/measures/"

    def answer(req: httpx.Request) -> httpx.Response:
        path, q = req.url.path, parse_qs(urlparse(str(req.url)).query)
        if path.endswith("/id/stations"):
            kind = q.get("parameter", q.get("type", [""]))[0]
            items = {
                "level": [{"stationReference": "3400TH", "label": "Kingston", "riverName": "Hogsmill", "lat": 51.39, "long": -0.27, "type": "SingleLevel"},
                          {"stationReference": "C1", "label": "Coastal one", "lat": 51.38, "long": -0.25, "type": ["Coastal", "SingleLevel"]}],
                "rainfall": [{"stationReference": "R9", "label": "Worcester Park rain", "lat": 51.37, "long": -0.24,
                              "measures": [{"@id": base + "R9-rainfall-tipping_bucket_raingauge-t-15_min-mm", "parameter": "rainfall"}]}],
                "Coastal": [{"stationReference": "T1", "label": "Sheerness", "lat": 51.44, "long": 0.74}],
            }.get(kind, [])  # fmt: skip
            return httpx.Response(200, json={"items": items})
        if "/id/stations/3400TH" in path:
            return httpx.Response(200, json={"items": {"label": "Kingston", "riverName": "Hogsmill", "town": "Kingston",
                "stageScale": {"typicalRangeLow": 0.2, "typicalRangeHigh": 0.9},
                "measures": [{"@id": base + "3400TH-level-stage-i-15_min-m", "parameter": "level", "qualifier": "Stage",
                              "unitName": "m", "latestReading": {"value": 1.12, "dateTime": "2026-11-07T08:45:00Z"}}]}})  # fmt: skip
        if "/id/stations/T1" in path:
            return httpx.Response(200, json={"items": [{"label": "Sheerness", "measures": {"@id": base + "T1-level-tidal_level",
                "parameter": "level", "unitName": "mAOD", "latestReading": {"value": [2.4, 2.4], "dateTime": "2026-11-07T08:45:00Z"}}}]})  # fmt: skip
        if path.endswith("/readings"):
            if "3400TH" in path:
                vals = [(3, 0.95), (2, 1.0), (0, 1.12)]
            elif "T1" in path:
                vals = [(1, 2.9), (0, 2.4)]
            else:
                vals = [(h, 0.5) for h in range(24)] + [(0.25, 0.4)]
            return httpx.Response(200, json={"items": [{"dateTime": (NOW - timedelta(hours=h)).isoformat(), "value": v} for h, v in vals]})
        return httpx.Response(404)

    return httpx.MockTransport(answer)


async def test_rivers_rain_and_tides_from_the_ea() -> None:
    C.forget()
    async with httpx.AsyncClient(transport=ea()) as c:
        rv = await C.rivers_near(c, *HOME, now=NOW)
        rn = await C.rain_near(c, *HOME, now=NOW)
        td = await C.tides_near(c, *HOME, km=60, now=NOW)
    assert rv["count"] == 1  # the coastal gauge is not a river
    r = rv["rivers"][0]
    assert (r["river"], r["level_m"], r["state"], r["trend"]) == ("Hogsmill", 1.12, "high", "rising")
    g = rn["gauges"][0]
    assert (g["gauge"], g["last_hour_mm"], g["last_24h_mm"]) == ("Worcester Park rain", 0.9, 12.4)
    assert (td["gauge"], td["level_m"], td["unit"], td["trend"]) == ("Sheerness", 2.4, "mAOD", "falling")


async def test_no_tide_gauge_says_so() -> None:
    C.forget()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"items": []}))) as c:
        assert (await C.tides_near(c, *HOME, now=NOW))["gauge"] is None


async def test_a_source_that_fails_is_an_enverror() -> None:
    C.forget()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as c:
        with pytest.raises(C.EnvError, match="HTTP 503"):
            await C.air_near(c, *HOME, now=NOW)


def test_home_assistant_gets_a_sensor_per_place_and_homes_water() -> None:
    d = environment_device({"home": "home", "places": [
        {"place": "home", "name": "Home", "air": {"index": 4, "band": "Moderate", "worst": "Ozone", "readings": {}},
         "rivers": {"rivers": [{"river": "Hogsmill", "level_m": 1.12, "state": "high", "trend": "rising"}]},
         "rain": {"gauges": [{"gauge": "W", "last_24h_mm": 12.4, "last_hour_mm": 0.9}]}},
        {"place": "mum's", "name": "Mum's", "air": {"error": "uk-air.defra.gov.uk did not answer"},
         "tide": {"gauge": "Sheerness", "level_m": 2.4, "unit": "mAOD", "trend": "falling"}},
    ]})  # fmt: skip
    got = {s.key: s for s in d.sensors}
    assert set(got) == {"air_quality_home", "river_level_home", "rain_24h_home", "air_quality_mum_s", "tide_mum_s"}
    assert got["air_quality_home"].state == 4 and got["river_level_home"].attributes["state"] == "high"
    assert got["air_quality_mum_s"].state is None and "did not answer" in got["air_quality_mum_s"].attributes["error"]
    with pytest.raises(ValueError):
        environment_device({"places": []})


async def test_the_tools_say_it_plainly(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.modules.environment.environment as E

    async def air(request: Any, **kw: Any) -> dict[str, Any]:
        return {"name": "Home", "index": 4, "band": "Moderate", "worst": "Ozone", "readings": {
            "o3": {"pollutant": "Ozone", "mean": 110.0, "mean_hours": 8, "unit": "µg/m³", "index": 4, "station": "Far Away", "station_km": 18.2},
            "no2": {"pollutant": "Nitrogen dioxide", "mean": 70.0, "mean_hours": 1, "unit": "µg/m³", "index": 2,
                    "station": "Sutton Worcester Park", "station_km": 0.7}}}  # fmt: skip

    async def rivers(request: Any, **kw: Any) -> dict[str, Any]:
        return {"name": "Home", "rivers": [{"river": "Hogsmill", "station": "Kingston", "level_m": 1.12, "state": "high", "trend": "rising"}]}

    async def rain(request: Any, **kw: Any) -> dict[str, Any]:
        return {"gauges": [{"gauge": "Worcester Park", "last_hour_mm": 0.9, "last_24h_mm": 12.4}]}

    async def tides(request: Any, **kw: Any) -> dict[str, Any]:
        return {"name": "Home", "gauge": "Sheerness", "km": 62.0, "level_m": 2.4, "unit": "mAOD", "trend": "falling"}

    monkeypatch.setattr(E, "air", air)
    monkeypatch.setattr(E, "rivers", rivers)
    monkeypatch.setattr(E, "rain", rain)
    monkeypatch.setattr(E, "tides", tides)
    a = (await M.air_quality_tool(object(), {}))["text"]  # type: ignore[arg-type]
    assert a.startswith("Air quality near Home: moderate, index 4 of 10 (worst: Ozone). Ozone 110 µg/m³ (8-hour mean), index 4")
    assert a.endswith("Nearest station: Sutton Worcester Park, 0.7 km.")
    r = (await M.river_levels_tool(object(), {}))["text"]  # type: ignore[arg-type]
    assert r == ("Rivers near Home: Hogsmill at Kingston: 1.12 m, high for there, rising. "
                 "Rain at Worcester Park: 0.9 mm in the last hour, 12.4 mm in 24 hours.")  # fmt: skip
    t = (await M.tides_tool(object(), {}))["text"]  # type: ignore[arg-type]
    assert t.startswith("The tide gauge at Sheerness (62 km from Home) reads 2.40 mAOD, falling.")


def hub(pages: list[list[dict[str, Any]]], seen: list[dict[str, list[str]]]) -> httpx.MockTransport:
    """The Hub's ArcGIS layer: features within the asked distance, in pages."""

    def answer(req: httpx.Request) -> httpx.Response:
        q = parse_qs(urlparse(str(req.url)).query)
        seen.append(q)
        page = int(q["resultOffset"][0]) // 2
        return httpx.Response(200, json={"features": [{"attributes": a} for a in pages[page]],
                                         "exceededTransferLimit": page + 1 < len(pages)})  # fmt: skip

    return httpx.MockTransport(answer)


def spill(i: str, river: str, status: int, dlat: float, start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
    return {"Id": i, "Company": "Thames Water", "Status": status, "StatusStart": MS(start or NOW - timedelta(hours=3)),
            "LatestEventStart": MS(start or NOW - timedelta(hours=3)), "LatestEventEnd": MS(end) if end else None,
            "Latitude": HOME[0] + dlat, "Longitude": HOME[1], "ReceivingWaterCourse": river,
            "LastUpdated": MS(NOW - timedelta(minutes=10))}  # fmt: skip


async def test_overflows_near_and_on_watched_rivers() -> None:
    C.forget()
    seen: list[dict[str, list[str]]] = []
    pages = [
        [spill("TWL1", "Hogsmill River", 1, 0.01, start=NOW - timedelta(hours=5)),  # 1.1 km, spilling 5 h
         spill("TWL2", "Beverley Brook", 0, 0.02, end=NOW - timedelta(hours=20))],  # 2.2 km, spilt yesterday
        [spill("TWL3", "River Thames", 1, 0.1),  # 11 km, on a watched river
         spill("TWL4", "River Wandle", 1, 0.1),  # 11 km, not watched: left out
         spill("TWL5", "Pyl Brook", -1, 0.005),  # offline
         {"Id": "NOWHERE", "Status": 1}],  # no location: left out
    ]  # fmt: skip
    async with httpx.AsyncClient(transport=hub(pages, seen)) as c:
        r = await C.overflows_near(c, *HOME, km=3, rivers=["hogsmill", "Thames"], now=NOW)
    assert seen[0]["distance"] == ["15000"] and seen[0]["inSR"] == ["4326"] and seen[1]["resultOffset"] == ["2"]
    assert [o["id"] for o in r["discharging"]] == ["TWL1", "TWL3"] and r["discharging"][0]["hours"] == 5.0
    assert r["discharging"][1]["watched"] and not r["discharging"][0]["km"] > 3
    assert [o["id"] for o in r["recent"]] == ["TWL2"] and r["offline"] == 1 and r["count"] == 4
    C.forget()
    async with httpx.AsyncClient(transport=hub([pages[0]], seen)) as c:
        near = await C.overflows_near(c, *HOME, km=3, now=NOW)
    assert seen[-1]["distance"] == ["3000"] and near["rivers"] == [] and near["river_km"] is None


def test_an_overflow_reads_words_or_numbers() -> None:
    o = C.overflow({"id": "X", "status": "Not discharging", "latitude": 51.4, "longitude": -0.3,
                    "LatestEventEnd": "2026-11-07T01:00:00Z"}, *HOME, NOW)  # fmt: skip
    assert o is not None and o["status"] == "not discharging" and o["recent"] and o["name"] == "X"
    assert C.overflow({"Status": 1}, *HOME, NOW) is None


async def test_a_layer_error_is_an_enverror() -> None:
    C.forget()
    t = httpx.MockTransport(lambda r: httpx.Response(200, json={"error": {"code": 400, "message": "Invalid query"}}))
    async with httpx.AsyncClient(transport=t) as c:
        with pytest.raises(C.EnvError, match="Invalid query"):
            await C.overflows_near(c, *HOME, now=NOW)


def test_home_assistant_counts_the_spills() -> None:
    d = environment_device({"places": [{"place": "home", "name": "Home", "air": {}, "overflows": {
        "discharging": [{"name": "TWL1", "river": "Hogsmill River", "km": 1.1, "hours": 5.0}],
        "recent": [{"name": "TWL2"}], "offline": 1, "rivers": ["Hogsmill"], "count": 3}}]})  # fmt: skip
    s = {x.key: x for x in d.sensors}["storm_overflows_home"]
    assert s.state == 1 and s.attributes["discharging"][0]["river"] == "Hogsmill River" and len(s.attributes["last_48h"]) == 1
    d = environment_device({"places": [{"place": "home", "name": "Home", "air": {}, "overflows": {"error": "down"}}]})
    assert {x.key: x for x in d.sensors}["storm_overflows_home"].state is None


async def test_the_overflow_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.modules.environment.environment as E

    got: dict[str, Any] = {}

    async def overflows(request: Any, **kw: Any) -> dict[str, Any]:
        got.update(kw)
        return {"name": "Home", "km": 3, "rivers": ["Hogsmill"], "count": 4, "offline": 1,
                "discharging": [{"name": "Kingston Road CSO", "river": "Hogsmill River", "km": 1.1, "hours": 5.0}],
                "recent": [{"name": "TWL2", "river": None, "km": 2.2}]}  # fmt: skip

    monkeypatch.setattr(E, "overflows", overflows)
    t = (await M.storm_overflows_tool(object(), {"rivers": ["Hogsmill", "Thames"]}))["text"]  # type: ignore[arg-type]
    assert got["rivers"] == "Hogsmill,Thames"
    assert t.startswith("1 of 4 storm overflows within 3 km of Home or on the Hogsmill within 15 km is discharging now: "
                        "Kingston Road CSO into the Hogsmill River (1.1 km), for 5 hours. 1 more did in the last 48 hours: TWL2 (2.2 km). "
                        "1 monitor is offline")  # fmt: skip


DB = os.environ.get("WA_TEST_DATABASE_URL")


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_watched_rivers_are_saved(monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlalchemy as sa
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.main import app

    sync = sa.create_engine(DB)  # type: ignore[arg-type]
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS app_setting"))
        conn.execute(text("CREATE TABLE app_setting (key text primary key, value jsonb not null,"
                          " updated_at timestamptz not null default now())"))  # fmt: skip
    try:
        monkeypatch.setenv("API_TOKEN", "s3cret")
        app.state.engine = create_async_engine(DB, poolclass=NullPool)  # type: ignore[arg-type]
        h = {"Authorization": "Bearer s3cret"}
        c = TestClient(app)
        assert c.get("/v1/environment/overflows/rivers", headers=h).json() == {"rivers": []}
        assert c.put("/v1/environment/overflows/rivers", json={"rivers": [" Hogsmill ", "", "Thames"]}, headers=h).json() == {
            "rivers": ["Hogsmill", "Thames"]}  # fmt: skip
        assert c.get("/v1/environment/overflows/rivers", headers=h).json() == {"rivers": ["Hogsmill", "Thames"]}
        assert c.put("/v1/environment/overflows/rivers", json={"rivers": []}, headers=h).json() == {"rivers": []}
    finally:
        with sync.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS app_setting"))
        sync.dispose()
