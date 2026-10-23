from __future__ import annotations

import importlib.util
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.integrations.fuelfinder import sync
from app.integrations.fuelfinder.queries import FuelHit
from app.integrations.nationalhighways.match import Corridor
from app.integrations.nationalhighways.store import upsert_corridor
from app.main import app
from app.vehicles import litres_for, net_saving_pence
from tests.test_fuel_store import price, station

URL = os.environ.get("WA_TEST_DATABASE_URL")


def test_litres_for_33_mpg() -> None:
    assert litres_for(98.0, 33.0) == pytest.approx(8.39, abs=0.01)
    assert litres_for(0, 33.0) == 0


def _hit(pence: float, dist: float) -> FuelHit:
    return FuelHit("n", "N", None, None, None, 51.0, -0.5, "E10", pence, 1.0, dist, False, False)


def test_ev_energy_and_usable_battery() -> None:
    from app.vehicles import ev_kwh_for, usable_kwh

    assert usable_kwh(40, 80) == 32.0
    assert ev_kwh_for(160.9344, 4.0) == pytest.approx(25.0)


def test_detour_can_wipe_out_a_saving() -> None:
    near = net_saving_pence(_hit(140.0, 0.0), 145.0, 33.0, 30.0)
    assert near["net_saving_p"] == 150.0  # 5p x 30 L, no detour
    far = net_saving_pence(_hit(144.0, 15.0), 145.0, 33.0, 30.0)
    assert far["net_saving_p"] < 0 < far["gross_saving_p"]
    assert far["detour_km"] == 30.0


VERSIONS = Path(__file__).parent.parent / "migrations" / "versions"
NAMES = ["0003_nh_storage", "0006_places_trips", "0007_fuel", "0008_trip_routes", "0009_vehicle",
         "0029_vehicle_own_price"]
TABLES = ["vehicle", "fuel_price_change", "fuel_price", "fuel_station", "trip", "place",
          "nh_sign_message", "nh_sign", "nh_event_corridor", "nh_event", "nh_corridor"]  # fmt: skip


def _migrate(direction: str) -> None:
    sync_engine = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync_engine.begin() as conn:
        if direction == "upgrade":
            for t in TABLES:
                conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        with Operations.context(MigrationContext.configure(conn)):
            for n in NAMES if direction == "upgrade" else reversed(NAMES):
                spec = importlib.util.spec_from_file_location(n, VERSIONS / f"{n}.py")
                assert spec and spec.loader
                m = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(m)
                getattr(m, direction)()
    sync_engine.dispose()


@pytest.fixture
def schema() -> Iterator[None]:
    _migrate("upgrade")
    yield
    _migrate("downgrade")


@pytest_asyncio.fixture
async def engine(schema: None) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(URL)  # type: ignore[arg-type]
    yield eng
    await eng.dispose()


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_vehicle_api_trip_cost_and_net_saving(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sync, "MIN_STATIONS", 3)
    monkeypatch.setattr(sync, "MIN_PRICE_COVERAGE", 0.5)
    await sync.apply_stations(engine, [station("a", 51.30, -0.50), station("b", 51.31, -0.50),
                                       station("c", 51.32, -0.50)])  # fmt: skip
    # b is cheapest but 3 km off the route (a and c are on it)
    await sync.apply_stations(engine, [station("a", 51.30, -0.50), station("b", 51.31, -0.465),
                                       station("c", 51.32, -0.50)])  # fmt: skip
    await sync.apply_price_records(engine, [price("a", 150.0), price("b", 149.0), price("c", 160.0)])
    corridor = Corridor("trip-t", "T", ((51.29, -0.50), (51.33, -0.50)), buffer_m=500)
    await upsert_corridor(engine, corridor, source="route")
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO place (id,name,kind,lat,lon) VALUES "
                                "('p1','P1','other',51.29,-0.5),('p2','P2','other',51.33,-0.5)"))  # fmt: skip
        await conn.execute(text("INSERT INTO trip (id,name,from_place,to_place,corridor_id,"
                                "distance_km,duration_s) VALUES ('t','P1 to P2','p1','p2',"
                                "'trip-t',98,4200)"))  # fmt: skip

    monkeypatch.setenv("API_TOKEN", "tok")
    app.state.engine = engine
    c = TestClient(app)
    h = {"Authorization": "Bearer tok"}
    r = c.put("/v1/vehicles/estima", json={"name": "Toyota Estima hybrid", "mpg": 33}, headers=h)
    assert r.status_code == 200 and r.json()["fuel"] == "E10"
    assert c.put("/v1/vehicles/x", json={"name": "x", "mpg": 33, "fuel": "KEROSENE"}, headers=h).status_code == 422
    assert c.put("/v1/vehicles/x", json={"name": "x", "mpg": 2}, headers=h).status_code == 422
    assert [v["id"] for v in c.get("/v1/vehicles", headers=h).json()] == ["estima"]

    cost = c.get("/v1/trips/t/cost?vehicle=estima", headers=h).json()
    assert cost["litres"] == pytest.approx(8.4, abs=0.1)
    # b is 3 km off the route, so only a (150p) and c (160p) count within 2 km
    assert cost["stations_considered"] == 2
    assert cost["cheapest_pence_per_litre"] == 150.0
    assert cost["typical_pence_per_litre"] == 155.0
    assert cost["cost_gbp_typical"] == pytest.approx(cost["litres"] * 155 / 100, abs=0.02)
    assert c.get("/v1/trips/t/cost?vehicle=nope", headers=h).status_code == 404
    assert c.get("/v1/trips/zzz/cost?vehicle=estima", headers=h).status_code == 404

    along = c.get("/v1/fuel/along/trip-t?vehicle=estima&within_km=5&fill_litres=30", headers=h).json()
    assert along["vehicle"] == "Toyota Estima hybrid"
    top = along["stations"][0]
    assert {"net_saving_p", "detour_km"} <= top.keys()
    assert [s["net_saving_p"] for s in along["stations"]] == sorted(
        (s["net_saving_p"] for s in along["stations"]), reverse=True
    )
    leaf = {"name": "Nissan Leaf", "kind": "ev", "battery_kwh": 40, "soh_percent": 80,
            "miles_per_kwh": 3.5, "home_pence_per_kwh": 25}  # fmt: skip
    assert c.put("/v1/vehicles/leaf", json=leaf, headers=h).status_code == 200
    assert c.put("/v1/vehicles/bad", json={"name": "b", "kind": "ev"}, headers=h).status_code == 422
    ev = c.get("/v1/trips/t/cost?vehicle=leaf", headers=h).json()
    assert ev["usable_battery_kwh"] == 32.0
    assert ev["kwh"] == pytest.approx(98 / 1.609344 / 3.5, abs=0.05)
    assert ev["one_way_on_a_full_charge"] and not ev["return_on_a_full_charge"]
    assert ev["cost_gbp_at_home_rate"] == pytest.approx(ev["kwh"] * 0.25, abs=0.02)
    assert c.get("/v1/fuel/along/trip-t?vehicle=leaf", headers=h).status_code == 422
    gas = {"name": "LPG Estima", "kind": "lpg", "mpg": 28, "fuel_pence_per_litre": 85}
    saved = c.put("/v1/vehicles/gas", json=gas, headers=h).json()
    assert (saved["type"], saved["fuel"]) == ("lpg", "LPG")
    lpg = c.get("/v1/trips/t/cost?vehicle=gas", headers=h).json()
    assert lpg["stations_considered"] == 0 and "cost_gbp_typical" not in lpg
    assert lpg["cost_gbp_at_own_price"] == pytest.approx(lpg["litres"] * 0.85, abs=0.02)
    assert c.get("/v1/fuel/along/trip-t?vehicle=gas", headers=h).status_code == 422
    types = {v["id"]: v["type"] for v in c.get("/v1/vehicles", headers=h).json()}
    assert types == {"estima": "petrol", "gas": "lpg", "leaf": "electric"}
    assert c.delete("/v1/vehicles/gas", headers=h).status_code == 204
    assert c.delete("/v1/vehicles/leaf", headers=h).status_code == 204
    assert c.delete("/v1/vehicles/estima", headers=h).status_code == 204
    assert c.delete("/v1/vehicles/estima", headers=h).status_code == 404


def test_the_four_types_and_their_fuels() -> None:
    from pydantic import ValidationError

    from app.vehicles import VehicleIn, type_of

    petrol = VehicleIn(name="Estima", kind="petrol", mpg=33)
    assert (petrol.kind, petrol.fuel, type_of(petrol.kind, petrol.fuel)) == ("fuel", "E10", "petrol")
    diesel = VehicleIn(name="Van", kind="diesel", fuel="B7_PREMIUM", mpg=40)
    assert (diesel.fuel, type_of(diesel.kind, diesel.fuel)) == ("B7_PREMIUM", "diesel")
    lpg = VehicleIn(name="Gas", kind="lpg", mpg=28, fuel_pence_per_litre=85)
    assert (lpg.kind, lpg.fuel, type_of(lpg.kind, lpg.fuel)) == ("fuel", "LPG", "lpg")
    ev = VehicleIn(name="Leaf", kind="electric", battery_kwh=30, miles_per_kwh=3.6)
    assert (ev.kind, type_of(ev.kind, ev.fuel)) == ("ev", "electric")
    old = VehicleIn(name="Old", kind="fuel", fuel="diesel", mpg=40)  # the earlier form still works
    assert (old.fuel, type_of(old.kind, old.fuel)) == ("B7_STANDARD", "diesel")
    with pytest.raises(ValidationError, match="not a petrol fuel"):
        VehicleIn(name="x", kind="petrol", fuel="B7_STANDARD", mpg=30)
    with pytest.raises(ValidationError, match="needs mpg"):
        VehicleIn(name="x", kind="lpg")


async def test_an_ev_with_no_home_rate_uses_the_energy_tabs_cheapest(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime, timedelta

    from app import vehicles
    from app.energy import rates as R
    from app.energy import store as energy_store

    now = datetime(2026, 10, 23, 12, tzinfo=UTC)
    h = timedelta(hours=1)
    stored = [R.Period(now - 30 * h, now - 29 * h, 5.0), R.Period(now, now + 3 * h, 24.0),
              R.Period(now + 16 * h, now + 19 * h, 7.5), R.Period(now + 19 * h, now + 22 * h, 24.0)]

    async def load(engine: object, at: datetime | None = None) -> tuple[str, list[R.Period]]:
        return "E-1R-COSY", stored

    monkeypatch.setattr(energy_store, "load", load)
    best = await vehicles.cheapest_home_rate(None, now)
    assert best is not None and (best["pence_per_kwh"], best["over"]) == (7.5, "next 48 hours")
    stored[:] = [R.Period(now - 30 * h, now - 29 * h, -2.0)]  # nothing ahead: the past week, negative counts as free
    best = await vehicles.cheapest_home_rate(None, now)
    assert best is not None and (best["pence_per_kwh"], best["over"]) == (0.0, "past week")
    stored[:] = []
    assert await vehicles.cheapest_home_rate(None, now) is None

    trip = {"name": "T", "distance_km": 100.0, "duration_s": 3600}
    leaf = {"name": "Leaf", "battery_kwh": 30, "soh_percent": 100, "miles_per_kwh": 4.0, "home_pence_per_kwh": None}
    out = vehicles._ev_trip_cost(trip, leaf, {"pence_per_kwh": 7.5})
    assert out["home_rate_from"] == "the Energy tab's cheapest rate" and out["home_pence_per_kwh"] == 7.5
    assert out["cost_gbp_at_home_rate"] == pytest.approx(100 / 1.609344 / 4 * 0.075, abs=0.01)
    assert "cost_gbp_at_home_rate" not in vehicles._ev_trip_cost(trip, leaf, None)
    assert vehicles._ev_trip_cost(trip, {**leaf, "home_pence_per_kwh": 25}, {"pence_per_kwh": 7.5})["home_rate_from"] == "yours"


def test_the_vehicles_page_script_is_valid_javascript() -> None:
    """A Python escape once turned \\' into ' and broke the whole page; check the script parses."""
    import re
    import shutil
    import subprocess

    from app.vehicles_page import PAGE

    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = re.search(r"<script>(.*)</script>", PAGE, re.DOTALL)
    assert script
    r = subprocess.run([node, "--check", "-"], input=script.group(1), capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
