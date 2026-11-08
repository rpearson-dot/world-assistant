"""River flow against its usual flows (EA and NRFA, faked), muted events, and crime in a borough against its
neighbours (data.police.uk and Nomis, faked)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.integrations.environment import clients as C

NRFA_ROWS = {"data": [
    {"id": 39015, "name": "Hogsmill at Kingston", "river": "Hogsmill", "lat-long": {"latitude": 51.3986, "longitude": -0.2879},
     "measuring-authority-station-id": "3900TH", "gdf-mean-flow": 1.0, "gdf-q95-flow": 0.4, "gdf-q70-flow": 0.6,
     "gdf-q50-flow": 0.8, "gdf-q10-flow": 1.8, "gdf-q05-flow": 2.6},
    {"id": 1, "name": "Elsewhere", "river": "Thames", "lat-long": {"latitude": 52.0, "longitude": -1.0}}]}  # fmt: skip


def test_how_a_flow_compares_with_the_usual() -> None:
    st = {"mean": 1.0, "q95": 0.4, "q70": 0.6, "q10": 1.8, "q05": 2.6}
    assert C.flow_band(0.3, st) == {"state": "very low", "pct_of_mean": 30}
    assert C.flow_band(0.5, st)["state"] == "low" and C.flow_band(1.0, st)["state"] == "normal"
    assert C.flow_band(2.0, st)["state"] == "high" and C.flow_band(3.0, st)["state"] == "very high"
    assert C.flow_band(1.0, None) == {"state": None, "pct_of_mean": None}
    stations = [{"id": 2, "name": "x", "river": "Hogsmill", "lat": 51.4, "lon": -0.29, "ea_ref": None},
                {"id": 3, "name": "y", "river": "Thames", "lat": 51.4001, "lon": -0.2901, "ea_ref": "9999TH"}]
    assert C.nrfa_match(stations, "9999th", 0, 0, None)["id"] == 3  # type: ignore[index]  # by the station reference
    assert C.nrfa_match(stations, "1234", 51.4, -0.29, "Hogsmill River")["id"] == 2  # type: ignore[index]  # near, same river
    assert C.nrfa_match(stations, "1234", 50.0, 0.0, None) is None


def test_flows_near_with_the_usual_flows() -> None:
    C.forget()

    def fake(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if "nrfaapps" in u:
            assert req.url.params["station"] == "*" and "gdf-q95-flow" in req.url.params["fields"]
            return httpx.Response(200, json=NRFA_ROWS)
        if req.url.path.endswith("/id/stations"):
            assert req.url.params["parameter"] == "flow"
            return httpx.Response(200, json={"items": [{"stationReference": "3900TH", "label": "Kingston Vale", "riverName": "Hogsmill",
                                                        "lat": 51.3985, "long": -0.288}]})  # fmt: skip
        if req.url.path.endswith("/id/stations/3900TH"):
            return httpx.Response(200, json={"items": {"label": "Kingston Vale", "riverName": "Hogsmill", "measures": [
                {"@id": "http://environment.data.gov.uk/flood-monitoring/id/measures/3900TH-flow--i-15_min-m3_s", "parameter": "flow",
                 "latestReading": {"value": 2.1, "dateTime": "2026-11-08T21:45:00Z"}}]}})  # fmt: skip
        if req.url.path.endswith("/readings"):
            return httpx.Response(200, json={"items": [{"dateTime": "2026-11-08T19:00:00Z", "value": 1.5},
                                                       {"dateTime": "2026-11-08T21:45:00Z", "value": 2.1}]})  # fmt: skip
        return httpx.Response(404)

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as c:
            return await C.flows_near(c, 51.39, -0.28)

    d = asyncio.run(go())
    f = d["flows"][0]
    assert f["flow_m3s"] == 2.1 and f["state"] == "high" and f["pct_of_mean"] == 210 and f["trend"] == "rising"
    assert f["nrfa"] == {"id": 39015, "name": "Hogsmill at Kingston"} and f["usual"]["q95"] == 0.4
    assert "National River Flow Archive" in d["source"] and d["note"] is None


def test_an_event_is_muted_by_name_or_word() -> None:
    from app.events import is_muted

    m = {"names": ["Quiz Night"], "words": ["tribute"]}
    assert is_muted({"name": "quiz  night"}, m) == "the event"
    assert is_muted({"name": "ABBA live", "description": "The best Abba Tribute act"}, m) == 'the word "tribute"'
    assert is_muted({"name": "Christmas market", "venue": "Market Place"}, m) is None
    assert is_muted({"name": None}, {"names": [], "words": []}) is None


DB = __import__("os").environ.get("WA_TEST_DATABASE_URL")


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_mutes_saved_and_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlalchemy as sa
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app import events as E
    from app.main import app

    eng = sa.create_engine(DB)  # type: ignore[arg-type]
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE IF NOT EXISTS app_setting (key text primary key, value jsonb not null, updated_at timestamptz not null default now())"))
        c.execute(sa.text("DELETE FROM app_setting WHERE key = 'events_mute'"))
    monkeypatch.setenv("API_TOKEN", "s3cret")
    app.state.engine = create_async_engine(DB, poolclass=NullPool)  # type: ignore[arg-type]
    cl = TestClient(app)
    cl.headers["Authorization"] = "Bearer s3cret"
    try:
        assert cl.get("/v1/events/mutes").json() == {"names": [], "words": []}
        got = cl.put("/v1/events/mutes", json={"names": ["Quiz night", " "], "words": ["Tribute", "tribute"]}).json()
        assert got == {"names": ["Quiz night"], "words": ["tribute"]}
        out = {"events": [{"name": "Quiz night"}, {"name": "Tribute to Queen"}, {"name": "Fireworks"}], "count": 3}
        kept = asyncio.run(E.apply_mutes(app.state.engine, out))
        assert [e["name"] for e in kept["events"]] == ["Fireworks"] and kept["muted"] == 2 and kept["count"] == 1
        shown = asyncio.run(E.apply_mutes(app.state.engine, out, show=True))
        assert [e.get("muted") for e in shown["events"]] == ["the event", 'the word "tribute"', None]
    finally:
        with eng.begin() as c:
            c.execute(sa.text("DELETE FROM app_setting WHERE key = 'events_mute'"))
        eng.dispose()


def test_a_borough_against_its_neighbours(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import crime as CR
    from app import crime_areas as A

    A._counts.clear()
    A._pops.clear()
    CR._cache.clear()
    monkeypatch.setattr(CR, "GAP_S", 0)
    square = {"type": "Polygon", "coordinates": [[[-0.3, 51.4], [-0.2, 51.4], [-0.2, 51.45], [-0.3, 51.45], [-0.3, 51.4]]]}
    areas = [{"name": "Kingston upon Thames", "code": "E09000021", "km2": 37.3, "here": True, "shape": square},
             {"name": "Richmond upon Thames", "code": "E09000027", "km2": 57.4, "here": False, "shape": square},
             {"name": "Merton", "code": "E09000024", "km2": 37.6, "here": False, "shape": square}]  # fmt: skip

    async def near(engine: Any, lat: float, lon: float) -> list[dict[str, Any]]:
        return [dict(a) for a in areas]

    monkeypatch.setattr(A, "boroughs", near)
    per = {"E09000021": 1000, "E09000027": 1200, "E09000024": 1500}
    posted: list[str] = []

    def fake(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/crime-last-updated"):
            return httpx.Response(200, json={"date": "2026-09-01"})
        if req.url.path.endswith("/crimes-street/all-crime"):
            body = req.content.decode()
            posted.append(body)
            assert "date=2026-09" in body and "poly=51.40000%2C-0.30000" in body
            code = ["E09000021", "E09000027", "E09000024"][len(posted) - 1]
            return httpx.Response(200, json=[{}] * per[code])
        if "nomisweb" in str(req.url):
            assert req.url.params["geography"] == "E09000021,E09000027,E09000024" and req.url.params["c_age"] == "200"
            return httpx.Response(200, json={"obs": [
                {"geography": {"geogcode": "E09000021"}, "date": {"description": "2024"}, "obs_value": {"value": 170000}},
                {"geography": {"geogcode": "E09000027"}, "date": {"description": "2024"}, "obs_value": {"value": 195000}},
                {"geography": {"geogcode": "E09000024"}, "date": {"description": "2024"}, "obs_value": {"value": 215000}}]})  # fmt: skip
        return httpx.Response(404)

    async def go() -> dict[str, Any] | None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as c:
            return await A.compare(None, 51.41, -0.29, c)  # type: ignore[arg-type]

    d = asyncio.run(go())
    assert d and d["by"] == "per_1000" and d["months"] == ["2026-09"]
    names = [a["name"] for a in d["boroughs"]]
    assert names == ["Kingston upon Thames", "Richmond upon Thames", "Merton"] and d["rank"] == 1 and d["of"] == 3
    k = d["boroughs"][0]
    assert k["per_1000"] == round(1000 * 12 / 170000 * 1000, 1) and k["population"] == 170000
    assert A.words(d) == ("Kingston upon Thames: about 70.6 crimes a year per 1,000 residents, the lowest of 3 neighbouring boroughs "
                          "(lowest Kingston upon Thames 70.6, highest Merton 83.7).")  # fmt: skip
    # a second time: the counts are kept
    n = len(posted)
    asyncio.run(go())
    assert len(posted) == n
