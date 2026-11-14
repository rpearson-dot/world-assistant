"""The home energy planner: the evening-demand fit, the charging decision in each case, agreement with Home
Assistant, and (with a database) the five-minute tick keeping a decision and the charging summary."""

from __future__ import annotations

import asyncio
import importlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from app.modules.planner import charge, demand

LON = ZoneInfo("Europe/London")
URL = os.environ.get("WA_TEST_DATABASE_URL")


def test_fit_says_how_well_the_cold_explains_evenings() -> None:
    assert demand.fit([(1.0, 2.0)] * 5)["enough"] is False
    exact = [(float(x), 1.0 + 0.1 * x) for x in range(0, 40, 2)]
    f = demand.fit(exact)
    assert f["enough"] and f["r2"] == 1.0 and f["base_kwh"] == 1.0 and f["kwh_per_degree_hour"] == 0.1
    assert f["verdict"] == "predictable" and f["spread_kwh"] == 0.0
    p = demand.predict(f, 30)
    assert p == {"degree_hours": 30, "likely_kwh": 4.0, "nine_in_ten_kwh": 4.0, "at_least_kwh": 4.0}
    noisy = [(float(x), 3.0 + (2.0 if x % 4 else -2.0)) for x in range(20)]  # no link to the cold at all
    assert demand.fit(noisy)["verdict"] == "not predictable"
    assert demand.predict({"enough": False}, 10) is None


def test_evenings_count_only_whole_windows() -> None:
    day = datetime(2026, 11, 10, 16, tzinfo=UTC)  # GMT: local 16:00
    hourly = [(day + timedelta(hours=i), 1.5) for i in range(3)] + [(day + timedelta(days=1, hours=i), 1.0) for i in range(2)]
    assert demand.evenings(hourly, LON) == {"2026-11-10": 4.5}


def test_decide_in_each_case() -> None:
    d = charge.decide
    assert d(charge.Now(mode="off", status="Charging"))[0] == "off"
    assert d(charge.Now(status="Available"))[0] == "unplugged"
    assert d(charge.Now(status=None))[1] == "the charger's state is not known"
    assert d(charge.Now(mode="on", status="Preparing", soc=95))[0] == "charge"
    assert d(charge.Now(status="Preparing", soc=80, target=80, cheap=True))[0] == "full"
    assert d(charge.Now(status="Preparing", soc=50, free=True))[1] == "a Free Electricity hour you joined"
    assert d(charge.Now(status="Preparing", soc=10, low_on=True))[0] == "charge"
    assert d(charge.Now(status="Preparing", soc=10, low_on=False))[0] == "wait"
    assert d(charge.Now(status="Preparing", soc=50, cheap=True, cheap_pence=7))[1] == "the rate is cheap now (7 p/kWh)"
    sun = {"solar_start_w": 3500, "solar_stop_w": 2500}
    first = charge.Now(status="Preparing", soc=50, battery_soc=99, export_w=4000, settings=sun)
    assert d(first) == ("wait", "solar to spare: charging if it lasts another 5 minutes")
    first.solar_before = True
    assert d(first)[0] == "charge"
    charging = charge.Now(status="Charging", soc=50, battery_soc=99, export_w=-1000, car_w=4600, settings=sun)
    assert d(charging)[0] == "charge"  # 3.6 kW spare with the car taking 4.6 kW
    charging.export_w = -2500
    assert d(charging)[0] == "wait"  # only 2.1 kW: stop rather than run on the grid
    assert d(charge.Now(status="Preparing", soc=50, battery_soc=90, export_w=5000, solar_before=True, settings=sun))[0] == "wait"


def test_same() -> None:
    assert charge.same("charge", "charge") is True and charge.same("wait", "charge") is False
    assert charge.same("off", "wait") is None and charge.same("charge", "unavailable") is None


class FakeRedis:
    def __init__(self) -> None:
        self.d: dict[str, Any] = {}

    async def get(self, k: str) -> Any:
        return self.d.get(k)

    async def set(self, k: str, v: Any, ex: int | None = None) -> None:
        self.d[k] = v

    async def delete(self, k: str) -> None:
        self.d.pop(k, None)


@pytest.fixture
def db() -> Iterator[str]:
    mig = importlib.import_module("migrations.versions.0055_planner")
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS planner_decision; DROP TABLE IF EXISTS job_run; DROP TABLE IF EXISTS app_setting"))
        conn.execute(text("CREATE TABLE job_run (id bigserial primary key, job text, started_at timestamptz default now(), "
                          "finished_at timestamptz, status text default 'running', result jsonb, error text)"))
        conn.execute(text("CREATE TABLE app_setting (key text primary key, value jsonb, updated_at timestamptz)"))
        mig.op = Operations(MigrationContext.configure(conn))  # type: ignore[attr-defined]
        mig.upgrade()
    yield URL  # type: ignore[misc]
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS planner_decision; DROP TABLE IF EXISTS job_run; DROP TABLE IF EXISTS app_setting"))
    sync.dispose()


@pytest.mark.skipif(not URL, reason="needs WA_TEST_DATABASE_URL")
def test_tick_keeps_a_decision_beside_home_assistant(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.modules.planner import api, hass

    async def states(engine: Any, ids: list[str]) -> dict[str, Any]:
        v = {"sensor.wa_car_charging_plan": "wait", "sensor.wa_charger_status": "Preparing", "sensor.wa_car_charge_level": "40",
             "input_number.wa_ev_target_soc": "80", "sensor.office_inverter_battery_soc": "100"}  # fmt: skip
        return {k: {"entity_id": k, "state": s} for k, s in v.items() if k in ids}

    monkeypatch.setattr(hass, "states", states)

    async def go() -> tuple[Any, Any]:
        engine, redis = create_async_engine(db), FakeRedis()
        try:
            r = await api.planner_tick({"engine": engine, "redis": redis})
            async with engine.connect() as conn:
                row = (await conn.execute(text("SELECT plan, ha_plan, acted, inputs FROM planner_decision"))).mappings().one()
            return r, dict(row)
        finally:
            await engine.dispose()

    r, row = asyncio.run(go())
    assert r["plan"] == "wait" and r["ha"] == "wait" and r["acted"] is None
    assert row["plan"] == "wait" and row["inputs"]["soc"] == 40.0 and row["inputs"]["battery_soc"] == 100.0


def test_the_month_with_the_planner_in_charge() -> None:
    from app.modules.planner import review as R

    t0 = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    rates = [(t0, t0 + timedelta(hours=5), 7.0), (t0 + timedelta(hours=5), t0 + timedelta(days=1), 25.0)]
    ds = []
    for i in range(12 * 24):  # a day of five-minute decisions
        at = t0 + timedelta(minutes=5 * i)
        cheap = at.hour < 5
        charging = 18 <= at.hour < 20  # Home Assistant charged in the evening at 25p: 2 h at 4.6 kW
        ds.append({"at": at, "plan": "charge" if cheap else "wait", "ha_plan": "charge" if charging else "wait",
                   "inputs": {"status": "Charging" if charging else "Preparing", "car_w": 4600 if charging else 0}})
    r = R.judge(ds, rates)
    assert r["car_kwh"] == 9.2 and r["real_pounds"] == 2.3 and r["planner_pounds"] == 0.64 and r["saving_pounds"] == 1.66
    assert r["short_kwh"] == 0 and r["agreed_pct"] == round(100 * (24 * 12 - 60 - 24) / (24 * 12))
    tight = [d if d["at"].hour >= 1 else {**d, "plan": "wait"} for d in ds]  # only 4 h of charge times: 18.4 kWh fits
    assert R.judge(tight, rates)["short_kwh"] == 0
    few = [{**d, "plan": "charge" if d["at"].hour < 1 else "wait"} for d in ds]  # 1 h holds 4.6 of 9.2 kWh
    f = R.judge(few, rates)
    assert f["short_kwh"] == 4.6 and f["planner_pounds"] == round((4.6 * 7 + 4.6 * 25) / 100, 2)
    assert R.last_month(datetime(2026, 11, 1).date()) == (datetime(2026, 10, 1).date(), datetime(2026, 11, 1).date())
    assert "would have been about £0.64, which saved £1.66" in R.summary({"month": "2026-10", **r})
