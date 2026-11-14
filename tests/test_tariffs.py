"""Tariff comparison: today's day pattern from a tariff's rates (fixed, banded, daily-changing), cheap windows and
their spread, the battery moving use into them, ranking with paired exports, and (with a database) working it out
from meter readings and Octopus's products API."""

from __future__ import annotations

import asyncio
import importlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy import text

from app.modules.tariffs import catalogue, price

LON = ZoneInfo("Europe/London")
URL = os.environ.get("WA_TEST_DATABASE_URL")
NOW = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
GO = [{"from": "00:30", "to": "05:30", "pence": 8.5}, {"from": "00:00", "to": "00:00", "pence": 28.0}]
COSY = [{"from": "04:00", "to": "07:00", "pence": 13.0}, {"from": "13:00", "to": "16:00", "pence": 13.0},
        {"from": "22:00", "to": "00:00", "pence": 13.0}, {"from": "16:00", "to": "19:00", "pence": 40.0},
        {"from": "00:00", "to": "00:00", "pence": 27.0}]  # fmt: skip


def _day_rows(pat: list[float], day0: datetime, days: int, jitter: bool = False) -> list[dict[str, Any]]:
    out = []
    for d in range(days):
        for i in range(48):
            a = day0 + timedelta(days=d, minutes=30 * i)
            out.append({"valid_from": a, "valid_to": a + timedelta(minutes=30), "pence_inc_vat": pat[i] + ((d % 5) if jitter else 0)})
    return out


def test_patterns_are_todays_rates() -> None:
    flat, averaged = price.pattern([{"valid_from": NOW - timedelta(days=400), "valid_to": None, "pence_inc_vat": 24.5}], LON, NOW)
    assert flat == [24.5] * 48 and averaged is False
    old_then_new = [{"valid_from": NOW - timedelta(days=60), "valid_to": NOW - timedelta(days=2), "pence_inc_vat": 30.0},
                    {"valid_from": NOW - timedelta(days=2), "valid_to": None, "pence_inc_vat": 22.0}]  # fmt: skip
    assert price.pattern(old_then_new, LON, NOW)[0] == [22.0] * 48  # today's, not the past's
    go = price.bands(GO)
    start = datetime(2026, 10, 26, tzinfo=UTC)  # after the clocks went back: UTC is London time
    banded, averaged = price.pattern(_day_rows(go, start, 19), LON, NOW)  # published to midnight only: yesterday's fills in
    assert banded == go and averaged is False
    agile, averaged = price.pattern(_day_rows(go, start, 19, jitter=True), LON, NOW)
    assert averaged is True and agile is not None and 8.5 < agile[2] < 8.5 + 4  # 01:00, averaged over the days
    assert price.pattern([], LON, NOW) == (None, False)


def test_cheap_windows_and_their_spread() -> None:
    w = price.windows(price.bands(COSY))
    assert (w["count"], w["hours"], w["longest_gap_hours"]) == (3, 8.0, 6.0)
    assert w["windows"] == ["04:00 to 07:00", "13:00 to 16:00", "22:00 to 00:00"]
    g = price.windows(price.bands(GO))
    assert (g["count"], g["hours"], g["longest_gap_hours"]) == (1, 5.0, 19.0)
    assert price.windows([24.0] * 48)["count"] == 0


def _use(days: int, per_half_hour: float = 0.5) -> list[dict[str, Any]]:
    start = datetime(2026, 11, 1, tzinfo=UTC)
    return [{"start": start + timedelta(minutes=30 * i), "kwh": per_half_hour} for i in range(48 * days)]


def test_the_battery_moves_use_into_every_cheap_window() -> None:
    use, cosy, go = _use(7), price.bands(COSY), price.bands(GO)
    as_used = price.cost(use, cosy, LON)
    moved = price.shifted(use, cosy, LON, capacity_kwh=32, charge_kw=5)
    assert moved["pence"] < as_used["pence"]
    assert moved["kwh"] > as_used["kwh"]  # what is lost going in and out of the battery
    small = price.shifted(use, go, LON, capacity_kwh=5, charge_kw=5)
    big = price.shifted(use, go, LON, capacity_kwh=32, charge_kw=5)
    assert big["pence"] < small["pence"]  # one window a day: the battery has to bridge 19 hours
    flat = [24.0] * 48
    assert price.shifted(use, flat, LON, 32, 5) == price.cost(use, flat, LON)  # nothing to move into


def test_conditions_and_pairs() -> None:
    assert catalogue.conditions("FLUX-IMPORT-23-02-14")[1] == "flux"
    assert catalogue.conditions("INTELLI-FLUX-EXPORT-23-07-14")[1] == "intelligent-flux"
    assert catalogue.conditions("OUTGOING-VAR-24-10-26")[0] == ["needs your solar's MCS certificate (or equivalent)"]
    assert catalogue.conditions("GO-VAR-22-10-14")[0] == ["needs an electric car"]
    assert catalogue.conditions("VAR-22-11-01") == ([], None)
    assert catalogue.region_of("E-1R-COSY-22-12-08-H") == "H" and catalogue.region_of(None) is None


def test_compare_ranks_and_pairs_exports() -> None:
    use, out = _use(10), [{"start": datetime(2026, 11, 1, 12, tzinfo=UTC) + timedelta(days=d), "kwh": 4.0} for d in range(10)]
    tariffs = [
        {"code": "E-1R-VAR-H", "name": "Flexible", "direction": "import", "pattern": [25.0] * 48, "standing_p": 50},
        {"code": "E-1R-COSY-H", "name": "Cosy", "direction": "import", "pattern": price.bands(COSY), "standing_p": 50},
        {"code": "E-1R-FLUX-IMPORT-H", "name": "Flux", "direction": "import", "pattern": price.bands(COSY), "standing_p": 40, "pairs_with": "flux"},
        {"code": "E-1R-OUTGOING-H", "name": "Outgoing", "direction": "export", "pattern": [15.0] * 48},
        {"code": "E-1R-FLUX-EXPORT-H", "name": "Flux export", "direction": "export", "pattern": [3.0] * 48, "pairs_with": "flux"},
    ]
    r = price.compare(use, out, tariffs, LON, current="E-1R-VAR-H", battery={"capacity_kwh": 32, "charge_kw": 5})
    assert r["days"] == 10 and r["full_year"] is False
    assert [x["name"] for x in r["export"]] == ["Outgoing", "Flux export"]
    flex = next(x for x in r["import"] if x["name"] == "Flexible")
    assert flex["current"] and flex["standing_pounds"] == 5.0 and flex["windows"]["count"] == 0
    s = r["suggestion"]
    assert s["import_name"] == "Cosy" and s["export_name"] == "Outgoing"  # Flux is cheaper to buy but its export is poor
    assert s["saving_pounds"] > 0 and s["already_best"] is False


@pytest.fixture
def db() -> Iterator[str]:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    names = "energy_usage, energy_rate, job_run, app_setting"
    with sync.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {names}"))
        conn.execute(text("CREATE TABLE app_setting (key text primary key, value jsonb, updated_at timestamptz)"))
        conn.execute(text("CREATE TABLE energy_rate (tariff_code text, valid_from timestamptz, valid_to timestamptz, "
                          "pence_inc_vat float8, pence_exc_vat float8, fetched_at timestamptz default now())"))
        mig = importlib.import_module("migrations.versions.0054_energy_usage")
        mig.op = Operations(MigrationContext.configure(conn))  # type: ignore[attr-defined]
        mig.upgrade()
        conn.execute(text("INSERT INTO energy_rate (tariff_code, valid_from, valid_to, pence_inc_vat) VALUES "
                          "('E-1R-VAR-22-11-01-H', now() - interval '3 days', NULL, 25)"))
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=20)
        conn.execute(text("INSERT INTO energy_usage (direction, interval_start, kwh, mpan) VALUES (:d, :s, :k, '1')"),
                     [{"d": "import", "s": start + timedelta(minutes=30 * i), "k": 0.4} for i in range(48 * 14)]
                     + [{"d": "export", "s": start + timedelta(minutes=30 * i), "k": 0.2} for i in range(48 * 14)])  # fmt: skip
    yield URL  # type: ignore[misc]
    with sync.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {names}"))
    sync.dispose()


def _octopus(request: httpx.Request) -> httpx.Response:
    p = request.url.path
    if p == "/v1/products/":
        return httpx.Response(200, json={"next": None, "results": [
            {"code": "VAR-22-11-01", "display_name": "Flexible Octopus", "direction": "IMPORT"},
            {"code": "GO-VAR-22-10-14", "display_name": "Octopus Go", "direction": "IMPORT"},
            {"code": "OUTGOING-VAR-24-10-26", "display_name": "Outgoing Octopus", "direction": "EXPORT"},
            {"code": "BUSINESS-1", "display_name": "Business", "is_business": True},
            {"code": "PREPAY-1", "display_name": "Prepay", "is_prepay": True}]})
    if p.startswith("/v1/products/") and p.count("/") == 4:
        code = p.split("/")[3]
        tariff = {"code": f"E-1R-{code}-H", "standing_charge_inc_vat": 50.0, "exit_fees_type": "NONE"}
        return httpx.Response(200, json={"code": code, "single_register_electricity_tariffs": {"_H": {"direct_debit_monthly": tariff}}})
    if p.endswith("/standard-unit-rates/"):
        code = p.split("/")[3]
        now = datetime.now(UTC)
        if code == "GO-VAR-22-10-14":
            day0 = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=31)
            rows = [{"valid_from": r["valid_from"].isoformat(), "valid_to": r["valid_to"].isoformat(), "value_inc_vat": r["pence_inc_vat"]}
                    for r in _day_rows(price.bands(GO), day0, 32)]  # fmt: skip
        else:
            rows = [{"valid_from": (now - timedelta(days=90)).isoformat(), "valid_to": None,
                     "value_inc_vat": 15.0 if "OUTGOING" in code else 25.0}]  # fmt: skip
        return httpx.Response(200, json={"next": None, "results": rows})
    return httpx.Response(404)


@pytest.mark.skipif(not URL, reason="needs WA_TEST_DATABASE_URL")
def test_work_out_from_readings_and_octopus(db: str) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.modules.tariffs import api

    async def go() -> dict[str, Any]:
        engine = create_async_engine(db)
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(_octopus)) as client:
                return await api.work_out(engine, client)
        finally:
            await engine.dispose()

    r = asyncio.run(go())
    assert r["region"] == "H" and r["current"] == "E-1R-VAR-22-11-01-H" and r["days"] == 14
    names = [x["name"] for x in r["import"]]
    assert set(names) == {"Flexible Octopus", "Octopus Go"} and "Business" not in names
    flex = next(x for x in r["import"] if x["current"])
    assert flex["year_pounds"] == round((0.4 * 48 * 25 + 50) * 365 / 100)
    assert r["export"][0]["name"] == "Outgoing Octopus" and r["battery"] == {"capacity_kwh": 32.0, "charge_kw": 5.0}
    assert r["suggestion"]["import_name"] == "Octopus Go"  # with a 32 kWh battery, Go's 8.5p beats 25p all day
