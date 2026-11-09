"""The garage, more: routes and batteries from ride files, rain along the route, wear by surface, what goes together,
chain stretch, checklists, today's pressures, groupsets, removing parts, records, spares and recalls."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.bikes import checklists as CL
from app.bikes import compat as C
from app.bikes import defaults as D
from app.bikes import fit
from app.bikes import groupsets as GS
from app.bikes import rain as R
from app.bikes import recalls as RC
from app.bikes import wear as W
from tests.test_bikes import DB, T0, _part, garage, ride  # noqa: F401 - the fixture
from tests.test_bikes import fit as fitted


def test_a_route_every_km_and_the_batteries_a_ride_saw() -> None:
    track = [(None, 51.0 + i * 0.0009, -0.25) for i in range(101)]  # 100 steps of about 100 m: 10 km north
    r = fit.route(track)
    assert r[0] == [51.0, -0.25] and r[-1] == [51.09, -0.25] and 10 <= len(r) <= 12
    assert len(fit.route(track, every_km=0.05, most=20)) == 20
    devices = [{"index": 0, "battery_v": 4.0, "manufacturer": 1},  # the head unit: not a sensor
               {"index": 2, "type": 34, "manufacturer": 268, "serial": 1021, "battery_status": 5, "battery_v": 7.16},  # waking
               {"index": 2, "type": 34, "manufacturer": 268, "serial": 1021, "battery_status": 2, "battery_v": 8.0},
               {"index": 3, "type": 120, "manufacturer": 1, "serial": 9, "battery_status": 4}]  # fmt: skip
    s = fit.sensors(devices)
    assert s[0] == {"maker": "SRAM", "type": "shifting", "serial": 1021, "status": "good", "volts": 8.0, "level": 80}
    assert s[1]["type"] == "heart rate" and s[1]["status"] == "low" and s[1]["level"] == 20


def test_rain_along_the_route_takes_the_wettest_gauge(monkeypatch: pytest.MonkeyPatch) -> None:
    route = [[51.0 + i * 0.05, -0.25] for i in range(15)]  # about 78 km
    pts = R.along(route)
    assert pts[0] == (51.0, -0.25) and 5 <= len(pts) <= 8
    gauges = iter([(0.0, "Home", 2.0, "A"), (0.0, "Home", 3.0, "A"), (2.4, "Dorking", 4.0, "B"), None, None, None, None, None])

    async def fake(client: Any, lat: float, lon: float, start: Any, end: Any, km: float = 15, skip: Any = None) -> Any:
        got = next(gauges)
        return None if got and skip and got[3] in skip else got

    monkeypatch.setattr(R, "_gauge", fake)
    now = datetime.now(UTC)
    wet, why = asyncio.run(R.suggest_route(None, route, now - timedelta(hours=5), 3, now))  # type: ignore[arg-type]
    assert wet is True and why.startswith("up to 2.4 mm along the route (Dorking; 2 gauges)")
    assert asyncio.run(R.suggest_route(None, route, now - timedelta(days=30), 3, now))[0] is None  # type: ignore[arg-type]


def test_gravel_and_mountain_rides_wear_the_drivetrain_faster() -> None:
    placer = W.Placer([fitted("part", 1, 0, bike=1), fitted("part", 2, 0, bike=1), fitted("part", 3, 0, bike=1)])
    rides = [ride("a", 1, 1, 100, activity="Gravel Cycling"), ride("b", 2, 1, 100, activity="Road Cycling"),
             ride("c", 3, 1, 100, activity="Mountain Biking", wet=True)]  # fmt: skip
    chain = W.use_of({"id": 1, "kind": "chain"}, rides, placer, None)
    assert chain.km == 300 and chain.wear_km == 130 + 100 + 100 * 1.5 * 1.6 and chain.rough_rides == 2
    tyre = W.use_of({"id": 2, "kind": "tyre"}, rides, placer, None)  # off-road, but not the wet
    assert tyre.wear_km == 130 + 100 + 160
    assert W.use_of({"id": 3, "kind": "headset"}, rides, placer, None).wear_km == 300
    assert W.use_of({"id": 1, "kind": "chain"}, rides, placer, None, surface={}).wear_km == 100 + 100 + 150
    assert W.surface_of({"activity": "Cycling"}, "full_suspension") == "mtb" and W.surface_of({"activity": "Cycling"}, "road") == "road"


def test_what_does_not_go_together() -> None:
    parts = [{"id": 1, "kind": "cassette", "end": "rear", "details": {"speeds": 12, "freehub": "XD"}},
             {"id": 2, "kind": "chain", "details": {"speeds": "11-speed"}},
             {"id": 3, "kind": "hub", "end": "rear", "details": {"freehub": "Shimano HG", "rotor_mount": "6-bolt"}},
             {"id": 4, "kind": "rotor", "end": "rear", "details": {"diameter_mm": 180, "mount": "Centre Lock"}},
             {"id": 5, "kind": "brake_fluid", "position": "rear", "details": {"fluid": "Mineral oil"}},
             {"id": 6, "kind": "tyre", "end": "rear", "details": {"width_mm": 45, "bead_mm": 584}}]  # fmt: skip
    cfg = {"rear_rotor_mm": 160, "rear_fluid": "dot", "max_tyre_mm": 42, "wheel_size": "622", "speeds": 12}
    w = C.on_bike(parts, cfg)
    assert "12-speed cassette with an 11-speed chain: they need the same speeds" in w
    assert sum("cassette" in x and "chain" in x for x in w) == 1  # said once, not from both sides
    assert "11-speed chain on a bike with 12-speed shifting" in w
    assert "XD cassette on a HG freehub: it will not fit (change the freehub body or the cassette)" in w
    assert "Centre Lock rotor on a 6-bolt hub: it will not fit" in w
    assert any(x.startswith("180 mm rear rotor where the bike's rear brake is set up for 160 mm") for x in w)
    assert "mineral fluid for a DOT rear brake: never mix them (the seals swell)" in w
    assert "45 mm tyre on a bike that takes up to 42 mm" in w and "650b / 27.5-inch tyre on a bike with 700c / 29-inch wheels" in w
    ok = [{"id": 1, "kind": "cassette", "end": "rear", "details": {"speeds": 12, "freehub": "XD"}},
          {"id": 3, "kind": "hub", "end": "rear", "details": {"freehub": "XDR"}}, {"id": 2, "kind": "chain", "details": {}}]  # fmt: skip
    assert C.on_bike(ok, {}) == []  # an XDR body takes an XD cassette; an unknown chain says nothing


def test_chain_stretch_predicts_its_life() -> None:
    placer = W.Placer([fitted("part", 1, 0, bike=1)])
    rides = [ride(f"r{n}", n + 1, 1, 500) for n in range(8)]  # 500 km a day
    chain = {"id": 1, "kind": "chain", "fitted_at": T0, "details": {"speeds": 12}}

    def m(day: int, v: float) -> dict[str, Any]:
        return {"part_id": 1, "action": "measured", "reading": v, "at": T0 + timedelta(days=day, hours=12)}

    s = W.chain_wear(chain, [m(4, 0.2), m(8, 0.4)], rides, placer)  # 0.2% at 2,000 km, 0.4% at 4,000
    assert s and s["limit"] == 0.5 and [r["km"] for r in s["readings"]] == [2000, 4000]
    assert s["rate_per_1000km"] == 0.1 and s["life_km"] == 5000 and not s["worn"]
    assert W.chain_wear({**chain, "details": {"speeds": 10}}, [m(8, 0.75)], rides, placer)["worn"]  # type: ignore[index]
    assert W.chain_wear(chain, [], rides, placer) is None


def test_checklists_from_the_bikes_answers() -> None:
    tandem = CL.lists({**D.defaults_for("tandem"), "shifting": "axs", "checklist": {"pre": ["Bell"]}})
    ids = [i["id"] for i in tandem["pre"]]
    assert "timing" in ids and "axs" in ids and ids[-1] == "own0" and "sag" not in ids
    assert next(i for i in tandem["pre"] if i["id"] == "tyres")["checks"] == "tyre_check"
    mtb = CL.lists(D.defaults_for("full_suspension"))
    assert "sag" in [i["id"] for i in mtb["pre"]] and "sealant" in [i["id"] for i in mtb["service"]]
    assert "rimpads" not in [i["id"] for i in mtb["service"]]
    items = [{"id": "a", "text": "Tyres: x"}, {"id": "b", "text": "Lights and computer"}]
    assert CL.summary("Pre-ride check", items, ["a", "b"]) == "Pre-ride check: all 2 done"
    assert CL.summary("Pre-ride check", items, ["a"]) == "Pre-ride check: 1 of 2 done (not: Lights and computer)"


def test_todays_pressure_for_the_riding() -> None:
    assert D.today_pressure(50, "gravel", "gravel") == 50  # a gravel bike's usual is gravel
    assert D.today_pressure(50, "gravel", "road") == round(50 / 0.85)
    assert D.today_pressure(80, "road", "gravel") == 68 and D.today_pressure(80, "road", "wet") == 74


def test_a_groupset_changes_the_answers_and_has_the_models() -> None:
    g = GS.GROUPSETS["sram-rival-axs-e1-xplr"]
    a = GS.answers(g, {"front_brake": "hydraulic_disc", "rear_brake": "hydraulic_disc"})
    assert a["shifting"] == "axs" and a["front_rings"] == 1 and a["speeds"] == 13 and a["rear_fluid"] == "dot"
    assert GS.details_for(g, "cassette") == {"range": "10-46", "speeds": 13, "freehub": "XDR"}
    r7000 = GS.GROUPSETS["shimano-105-r7000"]
    assert GS.model_for(r7000, "levers", disc=False) == "ST-R7000" and GS.model_for(r7000, "levers", disc=True) == "ST-R7020"
    assert GS.answers(GS.GROUPSETS["sram-nx-eagle"], {"rear_brake": "hydraulic_disc"}).get("rear_fluid") is None  # no brakes
    assert {"sram-nx-eagle", "sram-force-10", "shimano-xt-m760"} <= set(GS.GROUPSETS)


def test_recalls_match_your_makers_and_bikes() -> None:
    now = datetime(2026, 11, 8, tzinfo=UTC)
    results = [{"title": "Yeti Cycles recalls SB140 frames", "link": "/product-safety-alerts-reports-recalls/yeti", "public_timestamp": "2026-03-01T10:00:00Z",
                "description": "Bicycle frames may crack at the shock mount."},
               {"title": "Yeti coolbox lids", "link": "/x", "public_timestamp": "2026-03-01T10:00:00Z", "description": "A lid can trap fingers."},
               {"title": "Yeti bike recall", "link": "/old", "public_timestamp": "2019-01-01", "description": "bike"}]  # fmt: skip
    got = RC.matches("Yeti", results, now)
    assert len(got) == 1 and got[0]["link"] == "https://www.gov.uk/product-safety-alerts-reports-recalls/yeti" and got[0]["date"] == "2026-03-01"
    bikes = [{"config": {"model": "yeti-asr-sl-2005"}}, {"config": {}, "records": {"maker": "Viking"}}, {"retired_at": 1, "config": {"model": "trek-1.2-2008"}}]
    assert RC.makers(bikes, [{"status": "fitted", "brand": "SRAM"}, {"status": "retired", "brand": "Old"}]) == ["SRAM", "Viking", "Yeti"]

    def fake(req: httpx.Request) -> httpx.Response:
        assert req.url.params["filter_format"] == RC.FORMAT
        return httpx.Response(200, json={"results": results if req.url.params["q"] == "Yeti" else []})

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as c:
            return await RC.check(bikes, [], c, now)

    out = asyncio.run(go())
    assert out["makers"] == ["Viking", "Yeti"] and len(out["matches"]) == 1 and "error" not in out


# --- the API ---------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_groupsets_checklists_measurements_batteries_and_records(garage: Any) -> None:  # noqa: F811
    import sqlalchemy as sa

    c = garage
    c.post("/v1/bikes/riders", json={"name": "Rob", "weight_kg": 80})
    trek = c.post("/v1/bikes/bikes", json={"rider": "rob", "label": "Trek", "type": "road", "fitted_at": "2026-01-01",
                                           "config": {"front_brake": "rim", "rear_brake": "rim"}}).json()["id"]  # fmt: skip
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    b = g["bikes"][0]
    kinds = {p["kind"] for p in b["parts"]}
    assert {"levers", "rear_derailleur", "front_derailleur", "crankset", "calliper"} <= kinds and "shifters" not in kinds
    assert set(b["checklists"]) == {"pre", "service"} and b["warnings"] == [] and b["pressures"]["rear"]["today"]["wet"]["psi"]

    # a groupset: its models on the parts, the answers to match
    r = c.post(f"/v1/bikes/bikes/{trek}/groupset", json={"groupset": "sram-force-10"}).json()
    assert r["answers"]["speeds"] == 10 and "Rear derailleur: SRAM Force" in r["filled"] and "Shift and brake levers: SRAM Force DoubleTap" in r["filled"]
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    cas = _part(g, "cassette")
    assert (cas["brand"], cas["model"], cas["details"]["speeds"], cas["details"]["freehub"]) == ("SRAM", "PG-1070", 10, "HG")
    assert _part(g, "calliper", "front")["model"] == "Force" and _part(g, "chain")["details"]["speeds"] == 10
    assert c.post(f"/v1/bikes/bikes/{trek}/groupset", json={"groupset": "nope"}).status_code == 422
    # a 1x groupset: the front derailleur goes
    r = c.post(f"/v1/bikes/bikes/{trek}/groupset", json={"groupset": "sram-apex-1", "only_empty": True}).json()
    assert "Front derailleur" in r["retired"] and _part(c.get("/v1/bikes", params={"rider": "rob"}).json(), "cassette")["model"] == "PG-1070"
    # a part that does not go: an 11-speed chain with the 10-speed cassette
    chain = _part(g, "chain")
    c.patch(f"/v1/bikes/parts/{chain['id']}", json={"details": {"speeds": 11}})
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    assert any("11-speed chain" in w for w in g["bikes"][0]["warnings"])

    # the chain checker's readings
    c.post("/v1/bikes/rides", json={"rider": "rob", "bike_id": trek, "started": "2026-02-01T09:00", "km": 2000, "hours": 60, "wet": False})
    m = c.post("/v1/bikes/service", json={"rider": "rob", "action": "measured", "part_id": chain["id"], "reading": 0.25}).json()
    assert m["action"] == "measured"
    assert c.post("/v1/bikes/service", json={"rider": "rob", "action": "measured", "part_id": chain["id"]}).status_code == 422
    ch = _part(c.get("/v1/bikes", params={"rider": "rob"}).json(), "chain")
    assert ch["stretch"]["latest"] == 0.25 and ch["stretch"]["life_km"] == 4000 and ch["wear"]["measures"]["km"]["life"] == 4000
    assert ch["history"][0]["note"] == "0.25% stretch (worn out at 0.5%)"

    # a checklist ticked off: logged, and the tyre pressure checks restart
    pre = g["bikes"][0]["checklists"]["pre"]["items"]
    r = c.post("/v1/bikes/checklist", json={"rider": "rob", "bike_id": trek, "list": "pre", "done": [i["id"] for i in pre if i["id"] != "lights"]}).json()
    assert r["note"].startswith(f"Pre-ride check: {len(pre) - 1} of {len(pre)} done (not: Lights") and len(r["checked"]) == 2
    b = c.get("/v1/bikes", params={"rider": "rob"}).json()["bikes"][0]
    assert b["checklists"]["pre"]["last"] and b["checklists"]["service"]["last"] is None
    assert c.post("/v1/bikes/checklist", json={"rider": "rob", "bike_id": 999, "list": "pre"}).status_code == 404

    # own checklist items and number answers, checked
    assert c.patch(f"/v1/bikes/bikes/{trek}", json={"config": {"checklist": {"pre": ["Bell"]}, "max_tyre_mm": 28}}).status_code == 200
    assert c.patch(f"/v1/bikes/bikes/{trek}", json={"config": {"max_tyre_mm": 500}}).status_code == 422
    assert c.patch(f"/v1/bikes/bikes/{trek}", json={"config": {"checklist": "x"}}).status_code == 422

    # an AXS battery: its level from what Garmin saw on the last ride
    gravel = c.post("/v1/bikes/bikes", json={"rider": "rob", "label": "Reiver", "type": "gravel", "config": {"shifting": "axs"}}).json()["id"]
    bat = c.post("/v1/bikes/parts", json={"rider": "rob", "kind": "axs_battery", "status": "kit", "label": "Battery A"}).json()
    c.post("/v1/bikes/fit", json={"item": "part", "id": bat["id"], "bike_id": gravel})
    c.post(f"/v1/bikes/bikes/{gravel}/groupset", json={"groupset": "sram-rival-axs-e1-xplr"})
    rb = next(x for x in c.get("/v1/bikes", params={"rider": "rob"}).json()["bikes"] if x["id"] == gravel)
    assert {p["details"]["fluid"] for p in rb["parts"] if p["kind"] == "brake_fluid"} == {"DOT 5.1"} and rb["warnings"] == []
    eng = sa.create_engine(DB)  # type: ignore[arg-type]
    with eng.begin() as conn:
        conn.execute(sa.text("INSERT INTO bike_ride (id, rider_id, bike_id, started, km, hours, indoor, source, sensors) VALUES "
                             "('garmin:9', 'rob', :b, now() + interval '1 minute', 40, 2, false, 'garmin', CAST(:s AS jsonb))"),
                     {"b": gravel, "s": '[{"maker": "SRAM", "type": "shifting", "serial": 1021, "status": "low", "volts": 7.2, "level": 20}]'})  # fmt: skip
    eng.dispose()
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    b = next(x for x in g["bikes"] if x["id"] == gravel)
    assert b["batteries"][0]["reported"]["status"] == "low" and b["batteries"][0]["wear"]["state"] == "due" and b["batteries"][0]["level_text"] == "low (20%)"
    assert b["sensors"][0]["serial"] == 1021

    # spares to keep, and the records
    assert c.put("/v1/bikes/settings?rider=rob", json={"min_spares": {"chain": 2, "nonsense": 1}}).status_code == 422
    c.put("/v1/bikes/settings?rider=rob", json={"min_spares": {"chain": 2, "disc_pads": 0}})
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    assert g["spares_low"] == [{"kind": "chain", "name": "Chain", "have": 0, "want": 2, "buy": "SRAM Rival AXS E1"}]
    c.patch(f"/v1/bikes/bikes/{trek}", json={"records": {"frame_number": "WTU123", "insurer": "Yellow Jersey"}})
    png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    assert c.put(f"/v1/bikes/bikes/{trek}/frame-photo", json={"data_url": png}).status_code == 200
    b = next(x for x in c.get("/v1/bikes", params={"rider": "rob"}).json()["bikes"] if x["id"] == trek)
    assert b["records"]["frame_number"] == "WTU123" and b["has_frame_photo"]
    assert c.get(f"/v1/bikes/bikes/{trek}/frame-photo").headers["content-type"] == "image/png"
    c.delete(f"/v1/bikes/bikes/{trek}/frame-photo")
    assert c.get(f"/v1/bikes/bikes/{trek}/frame-photo").status_code == 404


def test_recent_rides_get_their_route_and_batteries_from_their_files(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.bikes import garmin as G

    now = datetime.now(UTC)
    track = [(None, 51.0 + i * 0.001, -0.25) for i in range(30)]
    devs = [{"index": 1, "type": 34, "manufacturer": 268, "serial": 5, "battery_status": 1, "battery_v": 8.1}]

    class Api:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def ride_file(self, aid: str) -> Any:
            self.calls.append(aid)
            if aid == "2":
                return None  # no file
            return fit.Summary(counts={}, track=track, devices=devs, dev_fields=[], sport=None)

    rides = [{"id": "garmin:1", "started": now - timedelta(days=1), "indoor": False},
             {"id": "garmin:2", "started": now - timedelta(days=2), "indoor": False},
             {"id": "garmin:3", "started": now - timedelta(days=3), "indoor": True},
             {"id": "garmin:4", "started": now - timedelta(days=60), "indoor": False}]  # too old: not read  # fmt: skip
    api = Api()
    monkeypatch.setattr(G, "FILE_GAP_S", 0)
    assert asyncio.run(G.read_files(api, rides, now)) == 2  # type: ignore[arg-type]
    assert api.calls == ["1", "2", "3"] and len(rides[0]["route"]) >= 3 and rides[0]["sensors"][0]["status"] == "new"
    assert "route" not in rides[2] and rides[2]["sensors"][0]["level"] == 100 and "route" not in rides[1]


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_garmin_sensors_become_kit_with_their_batteries(garage: Any) -> None:  # noqa: F811
    from app.bikes import api as A
    from app.main import app

    c = garage
    c.post("/v1/bikes/riders", json={"name": "Rob", "weight_kg": 80})
    reiver = c.post("/v1/bikes/bikes", json={"rider": "rob", "label": "Reiver", "type": "gravel"}).json()["id"]
    now = datetime.now(UTC)
    readings = [{"maker": "Garmin", "type": "speed", "serial": 11, "status": "good", "level": 80, "volts": 2.9,
                 "at": (now - timedelta(days=2)).isoformat(), "activity": "Gravel Cycling", "bike_id": reiver},
                {"maker": "Garmin", "type": "heart rate", "serial": 22, "status": "ok", "level": 50,
                 "at": (now - timedelta(days=3)).isoformat(), "activity": "Running", "bike_id": None},
                {"maker": "Garmin", "type": "heart rate", "serial": 22, "status": "low", "level": 20,
                 "at": (now - timedelta(days=1)).isoformat(), "activity": "Running", "bike_id": None},
                {"maker": "SRAM", "type": "shifting", "serial": 33, "status": "new", "at": now.isoformat()}]  # fmt: skip
    assert asyncio.run(A.save_sensors(app.state.engine, "rob", readings)) == {"added": 2, "updated": 0}
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    sens = {p["label"]: p for p in g["kit"] if p["kind"] == "sensor"}
    assert set(sens) == {"Speed sensor", "Heart rate strap"}  # not the AXS derailleur
    hr = sens["Heart rate strap"]
    assert hr["reported"]["status"] == "low" and hr["reported"]["activity"] == "Running" and hr["wear"]["state"] == "due"
    assert sens["Speed sensor"]["reported"]["bike"] == "Reiver" and sens["Speed sensor"]["wear"]["state"] != "due"
    rb = next(b for b in g["bikes"] if b["id"] == reiver)
    pw = {i["name"]: i for i in rb["power"]["items"]}
    assert pw["Speed sensor"]["text"] == "good (80%)" and pw["Heart rate strap"]["low"] and pw["Heart rate strap"]["worn"]
    assert rb["power"]["ready"] is False and rb["power"]["say"] == "Charge or replace first: Heart rate strap"
    other = c.post("/v1/bikes/bikes", json={"rider": "rob", "label": "Trek", "type": "road"}).json()["id"]
    tb = next(b for b in c.get("/v1/bikes", params={"rider": "rob"}).json()["bikes"] if b["id"] == other)
    assert [i["name"] for i in tb["power"]["items"]] == ["Heart rate strap"] and tb["power"]["ready"] is False  # the strap is low
    assert hr["level"] == 20 and hr["level_text"] == "low (20%)"
    # a new battery: what Garmin said before it no longer counts; a later reading updates it
    c.post("/v1/bikes/service", json={"rider": "rob", "action": "serviced", "part_id": hr["id"]})
    hr = next(p for p in c.get("/v1/bikes", params={"rider": "rob"}).json()["kit"] if p["id"] == hr["id"])
    assert "reported" not in hr and hr["wear"]["state"] == "ok" and hr["level"] is None and hr["level_text"] == "--"  # a new battery: unknown until Garmin says
    later = [{**readings[1], "status": "new", "level": 100, "at": (now + timedelta(minutes=5)).isoformat()}]
    assert asyncio.run(A.save_sensors(app.state.engine, "rob", later)) == {"added": 0, "updated": 1}


def test_parts_have_a_place_on_the_drawing() -> None:
    from app.bikes import art
    from app.bikes import garage as GR

    sp = art.spots({"type": "full_suspension"})
    assert "shock" in sp and all(0 <= v <= 1 for xy in sp.values() for v in xy) and sp["rear_wheel"][0] < sp["front_wheel"][0]
    assert GR.spot_of({"kind": "tyre"}, "front", sp) == "front_tyre" and GR.spot_of({"kind": "disc_pads", "position": "rear"}, None, sp) == "rear_brake"
    assert GR.spot_of({"kind": "shock_air"}, None, art.spots({"type": "road"})) == "frame"  # no shock drawn: the frame
    assert "timing" in art.spots({"type": "tandem"})


def test_what_to_buy_fits_the_bike() -> None:
    from app.bikes import garage as GR

    cfg = {"groupset": "sram-rival-axs-e1-xplr", "speeds": 13, "front_brake": "hydraulic_disc", "rear_brake": "hydraulic_disc"}
    chain = {"id": 1, "kind": "chain", "brand": "SRAM", "model": "Rival AXS E1", "details": {"speeds": 13}}
    got = GR.buy_options(chain, cfg, [])
    assert got[0] == {"text": "SRAM Rival AXS E1 chain", "brand": "SRAM", "model": "Rival AXS E1", "why": "the same again", "details": {"speeds": 13}}
    assert all("13" in x["why"] or x["why"] == "the same again" or "groupset" in x["why"] for x in got)
    cas = {"id": 2, "kind": "cassette", "details": {"speeds": 12}}
    hub = {"id": 3, "kind": "hub", "end": "rear", "details": {"freehub": "HG"}}
    texts = [x["text"] for x in GR.buy_options(cas, {"speeds": 12}, [hub])]
    assert texts and all("XG-" not in t for t in texts) and any("CS-R7101" in t or "PG-1230" in t for t in texts)  # HG only
    pads = {"id": 4, "kind": "disc_pads", "position": "front"}
    cal = {"id": 5, "kind": "calliper", "position": "front", "brand": "SRAM", "model": "Rival AXS E1 HRD flat mount"}
    assert GR.buy_options(pads, {}, [cal])[0]["text"] == "SRAM pads for Rival AXS E1 HRD flat mount"
    assert GR.buy_options({"id": 6, "kind": "saddle"}, {}, [])[0]["text"] == "Saddle"


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_a_service_list_done_in_one_go(garage: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    c = garage
    c.post("/v1/bikes/riders", json={"name": "Rob", "weight_kg": 80})
    bike = c.post("/v1/bikes/bikes", json={"rider": "rob", "label": "Reiver", "type": "gravel"}).json()["id"]
    spare = c.post("/v1/bikes/parts", json={"rider": "rob", "kind": "chain", "status": "shelf", "brand": "SRAM", "model": "Rival", "quantity": 2}).json()
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    b = g["bikes"][0]
    assert b["spots"]["chain"] and _part(g, "chain")["spot"] == "chain" and _part(g, "tyre", "front")["spot"] == "front_tyre"
    chain, lube, pads = _part(g, "chain"), _part(g, "lube"), _part(g, "disc_pads", "front")
    opts = c.get(f"/v1/bikes/parts/{chain['id']}/options", params={"rider": "rob"}).json()
    assert opts["bike"]["label"] == "Reiver" and opts["spares"][0]["id"] == spare["id"] and opts["spares"][0]["problems"] == []
    assert c.get("/v1/bikes/parts/99999/options", params={"rider": "rob"}).status_code == 404

    bought: list[str] = []

    async def fake_list(request: Any) -> Any:
        return None, 7, "Outdoors"

    async def fake_add(request: Any, text: str, why: str, list_id: Any = None) -> Any:
        bought.append(text)
        return {}

    from app import outdoors, shopping

    monkeypatch.setattr(outdoors, "the_list", fake_list)
    monkeypatch.setattr(shopping, "add_item", fake_add)
    r = c.post("/v1/bikes/jobs", json={"rider": "rob", "jobs": [
        {"op": "replace", "part_id": chain["id"], "spare_id": spare["id"]},
        {"op": "service", "part_id": lube["id"], "action": "serviced"},
        {"op": "replace", "part_id": pads["id"], "brand": "SRAM", "model": "Organic pads"},
        {"op": "bin", "part_id": spare["id"]},
        {"op": "buy", "text": "SRAM Rival chain"},
        {"op": "service", "part_id": lube["id"], "action": "nonsense"},
        {"op": "bin"}]}).json()  # fmt: skip
    assert (r["done"], r["failed"]) == (5, 2) and bought == ["SRAM Rival chain"]
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    assert _part(g, "chain")["model"] == "Rival" and _part(g, "disc_pads", "front")["model"] == "Organic pads"
    assert not [p for p in g["shelf"] if p["kind"] == "chain"]  # one fitted, the other binned
    assert bike
