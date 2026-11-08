"""The bike garage: defaults from a bike's answers, tyre pressures, and wear from rides and where each part was."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.bikes import defaults as D
from app.bikes import wear as W

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def kinds(cfg: dict[str, Any]) -> list[tuple[str, str, str | None]]:
    return [(p.kind, p.on, p.position) for p in D.planned_parts(cfg)]


def test_brakes_front_and_rear_each_give_their_own_parts() -> None:
    cfg = {**D.defaults_for("hybrid"), "front_brake": "hydraulic_disc", "rear_brake": "v_brake"}
    k = kinds(cfg)
    assert ("disc_pads", "bike", "front") in k and ("rotor", "front", "front") in k and ("brake_fluid", "bike", "front") in k
    assert ("rim_pads", "bike", "rear") in k and ("brake_cable", "bike", "rear") in k and ("rim", "rear", "rear") in k
    assert ("brake_cable", "bike", "front") not in k and ("rotor", "rear", "rear") not in k
    cable = kinds({**cfg, "front_brake": "cable_disc", "rear_brake": "cable_hydraulic"})
    assert ("brake_cable", "bike", "front") in cable and ("brake_fluid", "bike", "front") not in cable
    assert {("disc_pads", "bike", "rear"), ("brake_cable", "bike", "rear"), ("calliper_service", "bike", "rear")} <= set(cable)


def test_suspension_front_and_rear_separately_and_the_dropper_its_own() -> None:
    rear_only = kinds({**D.defaults_for("gravel"), "rear_suspension": "post"})
    assert ("susp_post", "bike", "rear") in rear_only and not any(k[0].startswith("fork") for k in rear_only)
    full = kinds(D.defaults_for("full_suspension"))
    assert {("fork_lowers", "bike", "front"), ("shock_air", "bike", "rear"), ("dropper_lower", "bike", None)} <= set(full)
    assert not any(k[0].startswith("dropper") for k in kinds(D.defaults_for("road")))


def test_tyres_per_end_tubeless_gets_sealant_tubed_a_tube() -> None:
    k = kinds({**D.defaults_for("gravel"), "rear_tyre": "latex"})
    assert ("sealant", "front", "front") in k and ("sealant", "rear", "rear") not in k and ("tube", "rear", "rear") in k
    checks = {p.position: p.details for p in D.planned_parts({**D.defaults_for("gravel"), "rear_tyre": "latex"}) if p.kind == "tyre_check"}
    assert checks == {"front": {"every_days": 7}, "rear": {"every_days": 1}}  # latex: before every ride


def test_tandem_ebike_axs_and_belt() -> None:
    t = kinds(D.defaults_for("tandem"))
    assert {("timing_chain", "bike", None), ("bottom_bracket", "bike", "stoker"), ("saddle", "bike", "stoker")} <= set(t)
    e = kinds(D.defaults_for("road", ebike=True))
    assert ("ebike_motor", "bike", None) in e
    axs = kinds({**D.defaults_for("gravel"), "shifting": "axs"})
    assert ("coin_cells", "bike", None) in axs and ("gear_cables", "bike", None) not in axs
    belt = kinds({**D.defaults_for("hybrid"), "drive": "belt"})
    assert ("belt", "bike", None) in belt and ("chain", "bike", None) not in belt
    with pytest.raises(ValueError, match="no bike type"):
        D.defaults_for("unicycle")


def test_tyre_pressures_start_where_the_calculators_do() -> None:
    assert D.tyre_pressure(28, False, "road", 90, "rear") == 73
    assert D.tyre_pressure(40, True, "gravel", 90, "rear") == 36 and D.tyre_pressure(40, True, "gravel", 90, "front") == 31
    assert D.tyre_pressure(28, False, "road", 110, "rear") > 73  # heavier: higher
    assert D.tyre_pressure(25, False, "road", 90, "rear", max_psi=80) == 80  # never above the tyre's or rim's maximum
    assert D.bar(73) == 5.0


def ride(rid: str, day: int, bike: int, km: float, hours: float = 1.0, **kw: Any) -> dict[str, Any]:
    return {"id": rid, "started": T0 + timedelta(days=day), "bike_id": bike, "km": km, "hours": hours,
            "indoor": False, "wet": None, "wet_suggested": None, **kw}  # fmt: skip


def fit(item: str, item_id: int, day: int, bike: int | None = None, set_id: int | None = None) -> dict[str, Any]:
    return {"item": item, "item_id": item_id, "bike_id": bike, "set_id": set_id, "at": T0 + timedelta(days=day)}


def test_a_wheelset_swap_moves_its_wear_with_it() -> None:
    # tyre 10 is on the winter rear wheel (set 1); the wheel goes on bike 1, then onto bike 2 on day 10
    placer = W.Placer([fit("part", 10, 0, set_id=1), fit("set", 1, 0, bike=1), fit("set", 1, 10, bike=2)])
    rides = [ride("a", 1, 1, 50), ride("b", 11, 1, 70), ride("c", 12, 2, 30)]
    u = W.use_of({"id": 10, "kind": "tyre"}, rides, placer, None)
    assert (u.km, u.rides) == (80, 2)  # 50 on bike 1 before the swap, 30 on bike 2 after; not the 70 without it


def test_wet_rides_count_extra_for_what_wears_faster_and_the_riders_word_beats_the_forecast() -> None:
    placer = W.Placer([fit("part", 1, 0, bike=1), fit("part", 2, 0, bike=1)])
    rides = [ride("a", 1, 1, 100, wet_suggested=True), ride("b", 2, 1, 100, wet_suggested=True, wet=False),
             ride("c", 3, 1, 100, wet=True)]  # fmt: skip
    chain = W.use_of({"id": 1, "kind": "chain"}, rides, placer, None)
    assert (chain.km, chain.wear_km, chain.wet_rides) == (300, 400, 2)  # the rider said ride b was dry
    hub = W.use_of({"id": 2, "kind": "bottom_bracket"}, rides, placer, None)
    assert hub.wear_km == 300  # not a part that wears faster in the wet


def test_indoor_rides_count_for_the_chain_not_tyres_and_the_cassette_only_without_a_trainer_one() -> None:
    placer = W.Placer([fit("part", 1, 0, bike=1), fit("part", 2, 0, bike=1), fit("part", 3, 0, bike=1)])
    rides = [ride("a", 1, 1, 40, indoor=True), ride("b", 2, 1, 60)]
    assert W.use_of({"id": 1, "kind": "chain"}, rides, placer, None).km == 100
    assert W.use_of({"id": 2, "kind": "tyre"}, rides, placer, None).km == 60
    assert W.use_of({"id": 3, "kind": "cassette"}, rides, placer, None).km == 60
    assert W.use_of({"id": 3, "kind": "cassette"}, rides, placer, None, trainer_has_cassette=False).km == 100


def test_due_at_90_percent_or_300_km_left_and_coming_from_75() -> None:
    now = T0 + timedelta(days=30)
    part = {"id": 1, "kind": "chain", "life": {"km": 3500}}
    assert W.state(part, W.Use(wear_km=2000), T0, now)["state"] == "ok"
    assert W.state(part, W.Use(wear_km=2700), T0, now)["state"] == "coming"
    assert W.state(part, W.Use(wear_km=3250), T0, now)["state"] == "due"  # 250 km left
    pads = {"id": 2, "kind": "disc_pads", "life": {"km": 2000}}
    assert W.state(pads, W.Use(wear_km=1750), T0, now)["state"] == "due"  # 250 km left, though only 87%
    sealant = {"id": 3, "kind": "sealant"}  # 90 days by default: due a fortnight before
    assert W.state(sealant, W.Use(), T0, T0 + timedelta(days=70))["state"] == "coming"
    assert W.state(sealant, W.Use(), T0, T0 + timedelta(days=77))["state"] == "due"
    lube = {"id": 6, "kind": "lube"}  # a 250 km life: "within 300 km" does not apply, or it would always be due
    assert W.state(lube, W.Use(), T0, now)["state"] == "ok" and W.state(lube, W.Use(wear_km=230), T0, now)["state"] == "due"
    check = {"id": 4, "kind": "tyre_check", "details": {"every_days": 7}}  # a weekly check: due on the day
    assert W.state(check, W.Use(), T0, T0 + timedelta(days=6))["state"] != "due"
    assert W.state(check, W.Use(), T0, T0 + timedelta(days=7))["state"] == "due"
    fork = {"id": 5, "kind": "fork_lowers"}  # 50 hours or a year: hours first here
    s = W.state(fork, W.Use(hours=41), T0, now)
    assert s["state"] == "due" and s["measures"]["hours"]["left"] == 9


def test_the_count_starts_at_the_last_reset() -> None:
    part = {"id": 1, "fitted_at": T0}
    services = [{"part_id": 1, "at": T0 + timedelta(days=5), "resets": True},
                {"part_id": 1, "at": T0 + timedelta(days=9), "resets": False}]  # fmt: skip
    assert W.since_reset(part, services) == T0 + timedelta(days=5)


def test_each_labelled_battery_learns_its_own_hours_and_shows_wearing() -> None:
    # battery 7 on bike 1 throughout; ten hours ridden between each charge, logged at 20% left (80% used)
    placer = W.Placer([fit("part", 7, 0, bike=1)])
    rides, services = [], []
    for n in range(8):
        hours = 10.0 if n < 4 else 7.0  # later cycles give fewer hours for the same 80%
        rides.append(ride(f"r{n}", n * 10 + 1, 1, 200, hours=hours))
        services.append({"part_id": 7, "action": "charged", "at": T0 + timedelta(days=n * 10), "level_before": 20.0})
    services.append({"part_id": 7, "action": "charged", "at": T0 + timedelta(days=80), "level_before": 20.0})
    h = W.battery_health({"id": 7}, services, rides, placer)
    assert h["charges"] == 9 and len(h["cycles"]) == 8
    assert h["early"] == 12.5 and h["recent"] == 8.8 and h["wearing"]  # 10 h for 80%: 12.5 h a charge, then 8.75
    assert h["learned_hours"] == 8.8


# --- the API, against a real Postgres --------------------------------------------------------------------------------
DB = __import__("os").environ.get("WA_TEST_DATABASE_URL")


@pytest.fixture
def garage(monkeypatch: pytest.MonkeyPatch) -> Any:
    import importlib.util
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.bikes import api as A
    from app.main import app

    tables = ("bike_service", "bike_ride", "bike_fit", "bike_part", "bike_set", "bike", "bike_rider", "place")
    eng = sa.create_engine(DB)  # type: ignore[arg-type]
    with eng.begin() as conn:
        for t in tables:
            conn.execute(sa.text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        conn.execute(sa.text("CREATE TABLE place (id text primary key, kind text, lat float, lon float)"))
        conn.execute(sa.text("INSERT INTO place VALUES ('home', 'home', 51.37, -0.247)"))
        spec = importlib.util.spec_from_file_location("m0044", Path("migrations/versions/0044_bikes.py"))
        m = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(m)  # type: ignore[union-attr]
        with Operations.context(MigrationContext.configure(conn)):
            m.upgrade()

    async def no_rain(*a: Any, **kw: Any) -> tuple[bool | None, str]:
        return True, "3.2 mm at Ewell gauge (2 km) during and before the ride"

    monkeypatch.setattr(A, "suggest", no_rain)
    monkeypatch.setenv("API_TOKEN", "s3cret")
    app.state.engine = create_async_engine(DB, poolclass=NullPool)  # type: ignore[arg-type]
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer s3cret"
    try:
        yield c
    finally:
        with eng.begin() as conn:
            for t in tables:
                conn.execute(sa.text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        eng.dispose()


def _part(g: dict[str, Any], kind: str, position: str | None = None) -> dict[str, Any]:
    for b in g["bikes"]:
        for p in b["parts"] + [p for s in b["sets"] for p in s["parts"]]:
            if p["kind"] == kind and (position is None or p["position"] == position):
                return p
    raise AssertionError(f"no {kind} {position}")


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_a_garage_from_riders_to_due_parts(garage: Any) -> None:
    c = garage
    assert c.post("/v1/bikes/riders", json={"name": "Rob", "weight_kg": 80}).json()["id"] == "rob"
    assert c.post("/v1/bikes/riders", json={"name": "rob"}).status_code == 409
    gravel = c.post("/v1/bikes/bikes", json={"rider": "rob", "label": "Gravel", "type": "gravel", "fitted_at": "2026-01-01",
                                             "config": {"shifting": "axs", "rear_brake": "cable_disc"}}).json()  # fmt: skip
    road = c.post("/v1/bikes/bikes", json={"rider": "rob", "label": "Road", "type": "road", "fitted_at": "2026-01-01"}).json()
    assert c.post("/v1/bikes/bikes", json={"rider": "rob", "label": "Road"}).status_code == 409
    assert c.post("/v1/bikes/bikes", json={"rider": "rob", "label": "X", "config": {"front_brake": "magic"}}).status_code == 422

    # rides: one on each bike, both outdoors (wet suggested), and an indoor one on the road bike
    for bike, day, km in ((gravel["id"], "2026-02-01T09:00", 2000), (road["id"], "2026-02-02T09:00", 500)):
        r = c.post("/v1/bikes/rides", json={"rider": "rob", "bike_id": bike, "started": day, "km": km, "hours": 30}).json()
        assert r["wet_suggested"] is True
    c.post("/v1/bikes/rides", json={"rider": "rob", "bike_id": road["id"], "started": "2026-02-03T18:00", "km": 40, "hours": 1,
                                    "indoor": True})  # fmt: skip
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    chain = _part(g, "chain")  # the gravel bike's: 2,000 km, all wet (1.5 times), so 3,000 of its 3,500: due (500 left? no: 86%)
    assert chain["wear"]["km"] == 2000 and chain["wear"]["wear_km"] == 3000 and chain["wear"]["state"] == "coming"
    assert len(g["ask_wet"]) == 2 and all(r["wet_suggested"] for r in g["ask_wet"])  # the two outdoor rides, to confirm
    ride_id = next(r["id"] for r in g["ask_wet"] if r["km"] == 2000)
    c.patch(f"/v1/bikes/rides/{ride_id}", json={"wet": False})  # it was dry after all
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    assert _part(g, "chain")["wear"]["wear_km"] == 2000 and len(g["ask_wet"]) == 1
    pads = _part(g, "disc_pads", "front")  # 2,000 km of a 2,000 km life: due
    assert pads["wear"]["state"] == "due" and any(d["part_id"] == pads["id"] for d in g["due"])
    assert {("brake_cable", "rear"), ("disc_pads", "rear")} <= {(p["kind"], p["position"]) for p in g["bikes"][0]["parts"]}
    assert g["bikes"][0]["pressures"]["rear"]["psi"] == D.tyre_pressure(40, True, "gravel", 90, "rear")

    # a spare on the shelf is used for the replacement; the count starts again
    spare = c.post("/v1/bikes/parts", json={"rider": "rob", "kind": "disc_pads", "status": "shelf", "quantity": 2,
                                            "brand": "Shimano", "model": "L05A", "price": 13}).json()  # fmt: skip
    assert next(d for d in g["due"] if d["part_id"] == pads["id"])["spare"] is None
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    assert next(d for d in g["due"] if d["part_id"] == pads["id"])["spare"]["left"] == 2
    done = c.post("/v1/bikes/service", json={"rider": "rob", "action": "replaced", "part_id": pads["id"], "spare_id": spare["id"]}).json()
    assert done["spares_left"] == 1
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    new = _part(g, "disc_pads", "front")
    assert new["id"] == done["new_part"] and new["brand"] == "Shimano" and new["wear"]["km"] == 0
    assert g["shelf"][0]["quantity"] == 1

    # the gravel wheels go on the road bike: their tyres' wear moves with them, and the road wheels come off
    moved = c.post("/v1/bikes/fit", json={"item": "group", "group": "Gravel wheels", "rider": "rob", "bike_id": road["id"],
                                          "at": "2026-03-01T00:00:00Z"}).json()  # fmt: skip
    assert set(moved["moved"]) == {"Road front wheel off", "Road rear wheel off", "Gravel front wheel", "Gravel rear wheel"}
    c.post("/v1/bikes/rides", json={"rider": "rob", "bike_id": road["id"], "started": "2026-03-02T09:00", "km": 100, "hours": 4,
                                    "wet": False})  # fmt: skip
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    road_bike = next(b for b in g["bikes"] if b["id"] == road["id"])
    tyres = {s["label"]: next(p for p in s["parts"] if p["kind"] == "tyre")["wear"]["km"] for s in road_bike["sets"]}
    assert tyres == {"Gravel front wheel": 2100, "Gravel rear wheel": 2100}  # 2,000 on the gravel bike, 100 on the road bike
    assert {s["label"] for s in g["loose_sets"]} == {"Road front wheel", "Road rear wheel"}

    # changed answers: a rear suspension seatpost added gives its service; the parts it no longer gives are retired
    ch = c.patch(f"/v1/bikes/bikes/{gravel['id']}", json={"config": {"rear_suspension": "post", "rear_brake": "hydraulic_disc"}}).json()
    assert "Suspension seatpost service (rear)" in ch["added"] and "Brake fluid (bleed) (rear)" in ch["added"]
    assert "Brake cable and housing (rear)" in ch["retired"]

    # an AXS battery, labelled: on the gravel bike, charged with its level logged
    bat = c.post("/v1/bikes/parts", json={"rider": "rob", "kind": "axs_battery", "label": "Battery A", "status": "kit",
                                          "bike_id": gravel["id"], "fitted_at": "2026-01-01"}).json()  # fmt: skip
    c.post("/v1/bikes/service", json={"rider": "rob", "action": "charged", "part_id": bat["id"], "level_before": 30,
                                      "at": "2026-02-05T00:00:00Z"})  # fmt: skip
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    kit = {p["label"]: p for p in g["kit"]}
    assert kit["Battery A"]["health"]["charges"] == 1 and g["bikes"][0]["batteries"][0]["label"] == "Battery A"

    # running cost: the fitted spare's price, on the gravel bike in 2026
    cost = c.get("/v1/bikes/costs", params={"rider": "rob"}).json()
    gc = next(b for b in cost["bikes"] if b["label"] == "Gravel")
    assert gc["total"] == 13.0 and gc["years"]["2026"]["cost"] == 13.0

    # Garmin Connect's CSV: cycling rows only, indoor marked, already-imported skipped
    csv_text = ("Activity Type,Date,Favorite,Title,Distance,Calories,Time,Moving Time\n"
                "Gravel Cycling,2026-03-05 08:00:00,false,Box Hill,62.40,1500,03:01:00,02:45:00\n"
                "Virtual Cycling,2026-03-06 18:00:00,false,Zwift - Watopia,30.00,600,01:00:00,01:00:00\n"
                "Running,2026-03-07 07:00:00,false,Park run,5.00,300,00:25:00,00:25:00\n")  # fmt: skip
    first = c.post("/v1/bikes/rides/import", json={"rider": "rob", "csv": csv_text, "bike_id": gravel["id"]}).json()
    again = c.post("/v1/bikes/rides/import", json={"rider": "rob", "csv": csv_text, "bike_id": gravel["id"]}).json()
    assert (first["read"], first["added"], again["added"], again["already"]) == (2, 2, 0, 2)
    rides = c.get("/v1/bikes/rides", params={"rider": "rob"}).json()["rides"]
    zwift = next(r for r in rides if r["title"] == "Zwift - Watopia")
    assert zwift["indoor"] and zwift["hours"] == 1.0 and next(r for r in rides if r["title"] == "Box Hill")["hours"] == 2.75


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_due_parts_go_on_the_outdoors_list_once_and_not_with_a_spare(garage: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from app.bikes import api as A
    from app.main import app

    c = garage
    c.post("/v1/bikes/riders", json={"name": "Rob"})
    bike = c.post("/v1/bikes/bikes", json={"rider": "rob", "label": "Road", "fitted_at": "2026-01-01"}).json()
    for day in ("2026-02-01T09:00", "2026-02-08T09:00"):
        c.post("/v1/bikes/rides", json={"rider": "rob", "bike_id": bike["id"], "started": day, "km": 1250, "hours": 50,
                                        "wet": False})  # fmt: skip
    listed: list[str] = []

    async def put(request: Any, part: dict[str, Any], bike_label: str | None) -> dict[str, Any]:
        listed.append(f"{A.list_name(part)} for {bike_label}")
        async with app.state.engine.begin() as conn:
            await conn.execute(A.text("UPDATE bike_part SET to_list_at = now() WHERE id = :p"), {"p": part["id"]})
        return {"added": True}

    monkeypatch.setattr(A, "put_on_list", put)
    first = asyncio.run(A.due_to_list(None, app.state.engine))
    assert "Disc brake pads for Road" in listed and len(listed) == 2  # both ends' pads (2,500 km of 2,000)
    assert not any("check" in x.lower() or "fluid" in x.lower() for x in listed)  # services are not bought
    assert asyncio.run(A.due_to_list(None, app.state.engine))["added"] == [] and first["added"]  # once each
    c.put("/v1/bikes/settings", params={"rider": "rob"}, json={"to_outdoors": "tap"})
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    assert g["settings"]["to_outdoors"] == "tap"


def test_the_page_script_compiles_and_calls_real_routes(tmp_path: Any) -> None:
    import re
    import subprocess

    from app.bikes.page import PAGE
    from app.main import app

    js = re.findall(r"<script>(.*?)</script\s*>", PAGE, re.DOTALL | re.IGNORECASE)[-1]
    f = tmp_path / "bikes.js"
    f.write_text(js)
    subprocess.run(["node", "--check", str(f)], check=True)
    paths = set(app.openapi()["paths"])
    for p in set(re.findall(r"'(/v1/bikes[^'?]*)", PAGE)):
        p = p.rstrip("/")
        assert p in paths or any(x.startswith(p + "/{") for x in paths), p


def test_every_kind_has_a_picture_and_each_bike_type_draws() -> None:
    from app.bikes import art

    assert {k.icon for k in D.KINDS.values()} <= set(art.ICONS)
    for t in D.BIKE_TYPES:
        for extra in ({}, {"ebike": True}, {"front_suspension": "fork", "rear_suspension": "shock"}):
            s = art.bike({**D.defaults_for(t), **extra}, t)
            assert s.startswith("<svg") and s.endswith("</svg>")
    assert "opacity" in art.bike({**D.defaults_for("road"), "ebike": True})  # the battery


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_replaced_with_the_same_again_or_something_different_and_the_history_says_so(garage: Any) -> None:
    c = garage
    c.post("/v1/bikes/riders", json={"name": "Rob"})
    c.post("/v1/bikes/bikes", json={"rider": "rob", "label": "Gravel", "type": "gravel", "fitted_at": "2026-01-01"})
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    ring, rotor = _part(g, "chainrings"), _part(g, "rotor", "front")
    c.patch(f"/v1/bikes/parts/{ring['id']}", json={"brand": "SRAM", "model": "X-Sync 2", "details": {"teeth": 40, "bcd": "direct"},
                                                   "price": 45, "url": "https://www.sram.com/x-sync"})  # fmt: skip
    c.patch(f"/v1/bikes/parts/{rotor['id']}", json={"brand": "SRAM", "model": "Centerline", "details": {"diameter_mm": 160}})
    # this week: a 42t in place of the 40t, a different brand
    r = c.post("/v1/bikes/service", json={"rider": "rob", "action": "replaced", "part_id": ring["id"],
                                          "new": {"brand": "Wolf Tooth", "model": "Drop-Stop", "details": {"teeth": 42}, "price": 60}}).json()  # fmt: skip
    assert r["note"] == "SRAM X-Sync 2 40t direct → Wolf Tooth Drop-Stop 42t direct"
    # the rotor: the same again
    same = c.post("/v1/bikes/service", json={"rider": "rob", "action": "replaced", "part_id": rotor["id"], "cost": 38}).json()
    assert same["note"] == "the same again (SRAM Centerline 160 mm)"
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    ring2, rotor2 = _part(g, "chainrings"), _part(g, "rotor", "front")
    assert ring2["describe"] == "Wolf Tooth Drop-Stop 42t direct" and ring2["url"] is None and ring2["price"] == 60.0
    assert rotor2["describe"] == "SRAM Centerline 160 mm" and rotor2["price"] == 38.0
    assert ring2["history"][0]["note"] == "SRAM X-Sync 2 40t direct → Wolf Tooth Drop-Stop 42t direct"
    assert ring2["history"][0]["part"] == "SRAM X-Sync 2 40t direct"  # what it replaced
    cat = c.get("/v1/bikes/catalogue").json()
    assert ("teeth", "Teeth (e.g. 42 or 50/34)") in [tuple(x) for x in cat["specs"]["chainrings"]]


def test_a_ride_on_the_wrong_bike_is_spotted_and_the_better_one_offered() -> None:
    from app.bikes import api as A

    bikes = [{"id": 1, "label": "Road", "config": {"type": "road"}}, {"id": 2, "label": "Trail", "config": {"type": "full_suspension"}},
             {"id": 3, "label": "Commuter e-bike", "config": {"type": "hybrid", "ebike": True}}]  # fmt: skip
    assert A.bike_doubt({"bike_id": 1, "activity": "Mountain Biking"}, bikes) == ("Mountain Biking logged on Road", 2)
    assert A.bike_doubt({"bike_id": 1, "activity": "E-Bike Ride"}, bikes) == ("E-Bike Ride logged on Road", 3)
    assert A.bike_doubt({"bike_id": 1, "activity": "Road Cycling"}, bikes) is None  # fits
    assert A.bike_doubt({"bike_id": 2, "activity": "Cycling"}, bikes) is None  # says nothing either way
    assert A.bike_doubt({"bike_id": 1, "activity": "Mountain Biking", "indoor": True}, bikes) is None
    assert A.garmin_link({"id": "garmin:123456"}) == "https://connect.garmin.com/modern/activity/123456"
    assert A.garmin_link({"id": "csv:rob:x"}) is None


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_imported_rides_are_asked_about_their_bike_and_a_correction_moves_the_wear(garage: Any) -> None:
    c = garage
    c.post("/v1/bikes/riders", json={"name": "Rob"})
    road = c.post("/v1/bikes/bikes", json={"rider": "rob", "label": "Road", "type": "road", "fitted_at": "2026-01-01"}).json()
    mtb = c.post("/v1/bikes/bikes", json={"rider": "rob", "label": "Trail", "type": "full_suspension", "fitted_at": "2026-01-01"}).json()
    csv_text = "Activity Type,Date,Title,Distance,Moving Time\nMountain Biking,2026-03-05 08:00:00,Surrey Hills,40.0,03:00:00\n"
    c.post("/v1/bikes/rides/import", json={"rider": "rob", "csv": csv_text, "bike_id": road["id"]})  # Garmin left it on Road
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    (r,) = g["ask_wet"]
    assert r["doubt"] == "Mountain Biking logged on Road" and r["suggest_bike"] == mtb["id"] and not r["bike_checked"]
    assert _part(g, "fork_lowers")["wear"]["hours"] == 0  # counted on the wrong bike so far
    c.patch(f"/v1/bikes/rides/{r['id']}", json={"bike_id": mtb["id"]})
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    assert _part(g, "fork_lowers")["wear"]["hours"] == 3.0  # moved: the fork's hours now count it
    assert all(x["bike_checked"] for x in g["ask_wet"])  # still asked whether it was wet, not which bike
    hand = c.post("/v1/bikes/rides", json={"rider": "rob", "bike_id": road["id"], "started": "2026-03-06T09:00", "km": 30,
                                           "wet": False}).json()  # fmt: skip
    assert hand["id"] not in {x["id"] for x in c.get("/v1/bikes", params={"rider": "rob"}).json()["ask_wet"]}  # chosen by hand



async def test_the_product_lookup_only_asks_public_https_websites(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    from fastapi import HTTPException

    from app.bikes import api as A

    async def resolve(host: str, port: int, **kw: Any) -> list[Any]:
        ips = {"shop.example": "93.184.216.34", "nas.example": "10.0.2.49", "sneaky.example": "93.184.216.34"}
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ips.get(host, "127.0.0.1"), port))]

    import asyncio

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    await A.public_https("https://shop.example/item/1")
    for bad, why in (("http://shop.example/", "https"), ("https://nas.example/", "not a public"), ("https://localhost/", "not a public"),
                     ("https://user:pw@shop.example/", "https")):  # fmt: skip
        with pytest.raises(HTTPException, match=why):
            await A.public_https(bad)


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_undo_a_replacement_delete_rides_and_parts_and_retire_a_bike_keeping_its_history(garage: Any) -> None:
    c = garage
    c.post("/v1/bikes/riders", json={"name": "Rob"})
    road = c.post("/v1/bikes/bikes", json={"rider": "rob", "label": "Road", "fitted_at": "2026-01-01"}).json()
    for day in ("2026-02-01T09:00", "2026-02-08T09:00"):
        c.post("/v1/bikes/rides", json={"rider": "rob", "bike_id": road["id"], "started": day, "km": 1000, "hours": 40, "wet": False})
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    pads = _part(g, "disc_pads", "front")
    assert pads["wear"]["state"] == "due"

    # the only spare is fitted, then the replacement is undone: the old pads are back with their wear, the spare on the shelf
    spare = c.post("/v1/bikes/parts", json={"rider": "rob", "kind": "disc_pads", "status": "shelf", "brand": "Shimano"}).json()
    done = c.post("/v1/bikes/service", json={"rider": "rob", "action": "replaced", "part_id": pads["id"], "spare_id": spare["id"]}).json()
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    assert _part(g, "disc_pads", "front")["id"] == spare["id"] and not g["shelf"]
    assert g["log"][0]["id"] == done["id"] and g["log"][0]["undo"] and g["log"][0]["bike"] == "Road"
    u = c.delete(f"/v1/bikes/service/{done['id']}").json()
    assert u["restored_part"] == pads["id"] and u["back_on_shelf"] == spare["id"]
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    back = _part(g, "disc_pads", "front")
    assert back["id"] == pads["id"] and back["wear"]["km"] == 2000 and g["shelf"][0]["id"] == spare["id"] and not g["log"]

    # the same again, then a check on the new pads: undoing the replacement waits until the check is undone
    r = c.post("/v1/bikes/service", json={"rider": "rob", "action": "replaced", "part_id": pads["id"]}).json()
    chk = c.post("/v1/bikes/service", json={"rider": "rob", "action": "checked", "part_id": r["new_part"]}).json()
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    assert [x["undo"] for x in g["log"][:2]] == [True, False]
    assert c.delete(f"/v1/bikes/service/{r['id']}").status_code == 409
    c.delete(f"/v1/bikes/service/{chk['id']}")
    assert c.delete(f"/v1/bikes/service/{r['id']}").json()["removed_part"] == r["new_part"]
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    assert _part(g, "disc_pads", "front")["id"] == pads["id"]

    # a ride logged twice is deleted: its wear comes off
    assert len(g["recent_rides"]) == 2
    assert c.delete(f"/v1/bikes/rides/{g['recent_rides'][0]['id']}").json()["deleted"]
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    assert _part(g, "disc_pads", "front")["wear"]["km"] == 1000
    assert c.delete("/v1/bikes/rides/manual:nope").status_code == 404

    # a part added by mistake is deleted; a bike with rides is retired, not deleted
    extra = c.post("/v1/bikes/parts", json={"rider": "rob", "kind": "chain", "status": "shelf"}).json()
    assert c.delete(f"/v1/bikes/parts/{extra['id']}").json()["deleted"]
    assert c.delete(f"/v1/bikes/bikes/{road['id']}").status_code == 409
    c.post("/v1/bikes/service", json={"rider": "rob", "action": "cost", "bike_id": road["id"], "cost": 25, "note": "service"})
    c.patch(f"/v1/bikes/bikes/{road['id']}", json={"retired": True})
    c.post("/v1/bikes/rides", json={"rider": "rob", "bike_id": road["id"], "started": "2026-03-01T09:00", "km": 1500, "hours": 50, "wet": False})
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    assert not g["due"]  # a retired bike's parts are not due
    old = g["retired"][0]
    assert old["label"] == "Road" and old["km"] == 2500 and old["rides"] == 2 and old["cost"]["total"] == 25.0
    assert any(p["kind"] == "chain" for p in old["parts"]) and {"Road front wheel", "Road rear wheel"} == set(old["sets"])
    assert any(x["action"] == "cost" for x in old["history"])
    c.patch(f"/v1/bikes/bikes/{road['id']}", json={"retired": False})
    assert not c.get("/v1/bikes", params={"rider": "rob"}).json()["retired"]

    # a bike added by mistake (no rides) is deleted with its parts and wheels
    oops = c.post("/v1/bikes/bikes", json={"rider": "rob", "label": "Oops", "type": "gravel"}).json()
    d = c.delete(f"/v1/bikes/bikes/{oops['id']}").json()
    assert d["deleted"] and d["sets"] == 2 and d["parts"] > 5
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    assert [b["label"] for b in g["bikes"]] == ["Road"] and not g["loose_sets"]


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_a_new_wheel_with_its_usual_parts_fitted_and_a_part_moved_onto_it(garage: Any) -> None:
    c = garage
    c.post("/v1/bikes/riders", json={"name": "Jo"})
    bike = c.post("/v1/bikes/bikes", json={"rider": "jo", "label": "Gravel", "type": "gravel"}).json()
    old_rear = bike["wheels"]["rear"]
    assert c.post("/v1/bikes/sets", json={"rider": "jo", "label": "W", "kind": "wheel"}).status_code == 422  # front or rear?
    r = c.post("/v1/bikes/sets", json={"rider": "jo", "label": "Winter rear wheel", "kind": "wheel", "position": "rear",
                                       "group": "Winter wheels", "bike_id": bike["id"], "parts": True}).json()  # fmt: skip
    assert r["took_off"] == ["Gravel rear wheel"] and "Tyre" in r["parts"]
    g = c.get("/v1/bikes?rider=jo").json()
    b = g["bikes"][0]
    assert [s["label"] for s in b["sets"]] == ["Gravel front wheel", "Winter rear wheel"]
    assert [s["id"] for s in g["loose_sets"]] == [old_rear]  # the old rear wheel, in storage, its parts with it
    new_rear = next(s for s in b["sets"] if s["label"] == "Winter rear wheel")
    assert {p["kind"] for p in new_rear["parts"]} >= {"tyre", "cassette"}
    # a spare wheel, not fitted, with no parts; then the old wheel's cassette moved onto it
    spare = c.post("/v1/bikes/sets", json={"rider": "jo", "label": "Spare rear", "kind": "wheel", "position": "rear"}).json()
    assert spare["parts"] == [] and spare["took_off"] == []
    cassette = next(p for p in g["loose_sets"][0]["parts"] if p["kind"] == "cassette")
    assert c.post("/v1/bikes/fit", json={"item": "part", "id": cassette["id"], "set_id": spare["id"]}).status_code == 200
    loose = {s["label"]: s for s in c.get("/v1/bikes?rider=jo").json()["loose_sets"]}
    assert any(p["id"] == cassette["id"] for p in loose["Spare rear"]["parts"])
    assert not any(p["id"] == cassette["id"] for p in loose["Gravel rear wheel"]["parts"])


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_a_wheel_swap_dated_in_the_past_puts_the_rides_since_on_those_wheels(garage: Any) -> None:
    c = garage
    c.post("/v1/bikes/riders", json={"name": "Al"})
    bike = c.post("/v1/bikes/bikes", json={"rider": "al", "label": "Road", "type": "road", "fitted_at": "2026-01-01"}).json()
    winter = c.post("/v1/bikes/sets", json={"rider": "al", "label": "Winter rear", "kind": "wheel", "position": "rear",
                                            "parts": True, "parts_from": bike["id"], "fitted_at": "2026-01-01"}).json()  # fmt: skip
    for day, km in (("2026-02-01", 50), ("2026-02-10", 30)):
        c.post("/v1/bikes/rides", json={"rider": "al", "bike_id": bike["id"], "started": f"{day}T09:00:00", "km": km, "wet": False})
    # the winter wheel went on on 5 February (said afterwards): the ride of the 10th was on it, the 1st's was not
    assert c.post("/v1/bikes/fit", json={"item": "set", "id": winter["id"], "bike_id": bike["id"], "at": "2026-02-05T00:00:00"}).status_code == 200
    g = c.get("/v1/bikes?rider=al").json()
    on = {r["km"]: r["wheels"] for r in g["recent_rides"]}
    assert on[30.0] == ["Road front wheel", "Winter rear"] and on[50.0] == ["Road front wheel", "Road rear wheel"]
    tyre = next(p for s in g["bikes"][0]["sets"] if s["label"] == "Winter rear" for p in s["parts"] if p["kind"] == "tyre")
    assert tyre["wear"]["km"] == 30


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_one_bike_changed_to_axs_takes_batteries_and_they_come_off_again(garage: Any) -> None:
    c = garage
    c.post("/v1/bikes/riders", json={"name": "Bo"})
    road = c.post("/v1/bikes/bikes", json={"rider": "bo", "label": "Road", "type": "road"}).json()["id"]
    c.post("/v1/bikes/bikes", json={"rider": "bo", "label": "Town", "type": "hybrid"})
    r = c.patch(f"/v1/bikes/bikes/{road}", json={"config": {"shifting": "axs"}}).json()
    assert "Coin cells" in " ".join(r["added"]) or r["added"]  # the AXS bike's own parts
    bat = c.post("/v1/bikes/parts", json={"rider": "bo", "kind": "axs_battery", "status": "kit", "label": "Battery A"}).json()["id"]
    assert c.post("/v1/bikes/fit", json={"item": "part", "id": bat, "bike_id": road}).status_code == 200
    g = c.get("/v1/bikes?rider=bo").json()
    on = {b["label"]: [x["id"] for x in b["batteries"]] for b in g["bikes"]}
    assert on == {"Road": [bat], "Town": []}
    assert c.post("/v1/bikes/fit", json={"item": "part", "id": bat, "bike_id": None}).status_code == 200
    g = c.get("/v1/bikes?rider=bo").json()
    assert all(not b["batteries"] for b in g["bikes"]) and any(k["id"] == bat for k in g["kit"])


def test_what_cannot_go_where() -> None:
    from app.bikes.placement import problems

    road = {"id": 1, "label": "Road", "config": {**D.defaults_for("road"), "front_brake": "rim", "rear_brake": "rim"}}
    gravel = {"id": 2, "label": "Gravel", "config": D.defaults_for("gravel")}
    front, rear = {"id": 10, "label": "Front wheel", "kind": "wheel", "position": "front"}, {"id": 11, "label": "Rear wheel", "kind": "wheel", "position": "rear"}
    part = lambda kind, pos=None: {"id": None, "kind": kind, "position": pos}
    assert problems(part("cassette"), wheel=front, wheel_bike=gravel) == ["A cassette only goes on a rear wheel"]
    assert problems(part("cassette"), wheel=rear, wheel_bike=gravel) == []
    assert "goes on the bike, not on a wheel" in problems(part("chain"), wheel=rear)[0]
    assert "goes on a wheel" in problems(part("tyre"), bike=gravel)[0]
    assert "rotor needs a disc brake" in problems(part("rotor"), wheel=front, wheel_bike=road)[0]
    assert problems(part("rotor"), wheel=front, wheel_bike=gravel) == []
    assert problems(part("rotor"), wheel=front) == []  # in storage: no bike to check against
    assert "AXS" in problems(part("axs_battery"), bike=gravel)[0]
    assert problems(part("axs_battery"), bike={**gravel, "config": {**gravel["config"], "shifting": "axs"}}) == []
    assert "already a tyre" in problems(part("tyre"), wheel=front, there=[{"id": 5, "kind": "tyre", "position": "front"}])[0]
    assert problems(part("disc_pads", "rear"), bike=gravel, there=[{"id": 6, "kind": "disc_pads", "position": "front"}]) == []
    assert "already" in problems(part("disc_pads", "front"), bike=gravel, there=[{"id": 6, "kind": "disc_pads", "position": "front"}])[0]
    assert "sealant is for a tubeless tyre" in problems(part("sealant"), wheel=rear, wheel_bike={**gravel, "config": {**gravel["config"], "rear_tyre": "tubed"}})[0].lower()


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_a_cassette_cannot_go_on_a_front_wheel(garage: Any) -> None:
    c = garage
    c.post("/v1/bikes/riders", json={"name": "Cy"})
    bike = c.post("/v1/bikes/bikes", json={"rider": "cy", "label": "Gravel", "type": "gravel"}).json()
    r = c.post("/v1/bikes/parts", json={"rider": "cy", "kind": "cassette", "set_id": bike["wheels"]["front"]})
    assert r.status_code == 422 and "rear wheel" in r.json()["detail"]
    r = c.post("/v1/bikes/parts", json={"rider": "cy", "kind": "cassette", "set_id": bike["wheels"]["rear"]})
    assert r.status_code == 422 and "already a cassette" in r.json()["detail"]  # the bike came with one
    spare = c.post("/v1/bikes/parts", json={"rider": "cy", "kind": "cassette", "status": "shelf"}).json()["id"]
    r = c.post("/v1/bikes/fit", json={"item": "part", "id": spare, "set_id": bike["wheels"]["front"]})
    assert r.status_code == 422


def test_each_model_and_paint_draws_and_bad_ones_are_refused() -> None:
    from fastapi import HTTPException

    from app.bikes import art
    from app.bikes.api import _check
    from app.themes import PAINTS

    for key, m in art.MODELS.items():
        svg = art.bike({**D.defaults_for(m["type"]), "model": key, "paint": "bronze", "paint2": "red"}, key)
        assert svg.startswith("<svg") and PAINTS["bronze"][1] in svg and PAINTS["red"][1] in svg
    assert "var(--accent)" in art.bike(D.defaults_for("road"))  # no paint: the theme's colour
    _check({"model": "planet-x-reiver-2025", "paint": "barbie-pink"})
    _check({"pinstripe": "red", "fade": True, "paint": "burnt-orange", "paint2": "slate"})
    assert "linearGradient" in art.bike({**D.defaults_for("gravel"), "paint": "burnt-orange", "paint2": "slate", "fade": True}, "r")
    for bad in ({"model": "penny-farthing"}, {"paint": "#ff00ff"}, {"pinstripe": "tartan"}):
        with pytest.raises(HTTPException):
            _check(bad)


def test_a_tandem_has_a_long_timing_chain_its_rings_and_an_eccentric_bottom_bracket() -> None:
    t = kinds(D.defaults_for("tandem"))
    assert {("timing_chain", "bike", None), ("timing_rings", "bike", None), ("eccentric_bb", "bike", "captain"),
            ("chainrings", "bike", None)} <= set(t)  # fmt: skip
    assert ("chainrings", "bike", "stoker") not in t  # the drive rings are the stoker's: the bike's chainrings
    timing = next(p for p in D.planned_parts(D.defaults_for("tandem")) if p.kind == "timing_chain")
    assert "two chains" in timing.details["links"]


def test_a_tyre_size_as_written_gives_its_width_etrto_and_wheel_and_the_pressure_follows() -> None:
    assert D.tyre_size("29x2.4") == {"width_mm": 61, "bead_mm": 622, "etrto": "61-622", "wheel": "700c / 29-inch"}
    assert D.tyre_size("650x47b")["etrto"] == "47-584" and D.tyre_size("26 x 2.10")["etrto"] == "53-559"
    assert D.tyre_size("nonsense") == {}
    d = D.tyre_details({"size": "700x40c", "width_mm": 35, "tubeless": True})
    assert d["width_mm"] == 40 and d["etrto"] == "40-622" and d["tubeless"]  # the size as written wins
    assert D.tyre_details({"compound": "soft"}) == {"compound": "soft"}
    big, small = D.tyre_pressure(50, False, "hybrid", 90, "rear"), D.tyre_pressure(50, False, "hybrid", 90, "rear", bead_mm=559)
    assert small > big  # a 26-inch wheel a little higher than a 700c at the same width


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_removing_a_part_the_bike_does_not_have(garage: Any) -> None:
    c = garage
    c.post("/v1/bikes/riders", json={"name": "Rob", "weight_kg": 80})
    yeti = c.post("/v1/bikes/bikes", json={"rider": "rob", "label": "Yeti", "type": "full_suspension"}).json()["id"]
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    dropper = _part(g, "dropper_lower")
    # a fixed seatpost: removing the dropper's service says so in the answers, and both dropper services go
    r = c.post(f"/v1/bikes/parts/{dropper['id']}/remove").json()
    assert r["answer"] == {"dropper": "none"} and len(r["retired"]) == 2
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    b = next(x for x in g["bikes"] if x["id"] == yeti)
    assert b["config"]["dropper"] == "none" and not [p for p in b["parts"] if p["kind"].startswith("dropper")]
    # any other part: off, and not added back when the answers change
    hanger = _part(g, "hanger")
    assert c.post(f"/v1/bikes/parts/{hanger['id']}/remove").json()["deleted"] is True
    c.patch(f"/v1/bikes/bikes/{yeti}", json={"config": {"front_tyre": "tubed"}})
    g = c.get("/v1/bikes", params={"rider": "rob"}).json()
    b = next(x for x in g["bikes"] if x["id"] == yeti)
    assert "hanger@" in b["config"]["removed"] and not [p for p in b["parts"] if p["kind"] == "hanger"]
    # a part with history is retired, keeping it
    chain = _part(g, "chain")
    c.post("/v1/bikes/service", json={"rider": "rob", "action": "replaced", "part_id": chain["id"]})
    chain = _part(c.get("/v1/bikes", params={"rider": "rob"}).json(), "chain")
    c.post("/v1/bikes/service", json={"rider": "rob", "action": "checked", "part_id": chain["id"]})
    assert c.post(f"/v1/bikes/parts/{chain['id']}/remove").json()["deleted"] is False
    assert c.post("/v1/bikes/parts/99999/remove").status_code == 404
