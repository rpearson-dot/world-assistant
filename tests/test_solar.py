"""Solar forecast: forecast.solar's answer read into periods, the day and hour sums, power now and what is still to
come, Home Assistant's arrays, and (with a database) a fetch kept with tomorrow's figure for the accuracy check."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.modules.solar import fsolar

LON = ZoneInfo("Europe/London")
URL = os.environ.get("WA_TEST_DATABASE_URL")


def answer(day: str = "2026-11-14", scale: float = 1.0) -> dict[str, Any]:
    """forecast.solar's shape: times every half hour from 08:00 to 16:00 local, power rising to noon and falling."""
    watts, period = {}, {}
    for i in range(17):
        t = datetime.fromisoformat(f"{day}T08:00:00") + timedelta(minutes=30 * i)
        w = max(0.0, 1000 - abs(i - 8) * 125) * scale
        key = t.replace(tzinfo=LON).isoformat()
        watts[key] = w
        period[key] = 0 if i == 0 else w / 2  # Wh in the half hour that ends here
    return {"result": {"watts": watts, "watt_hours_period": period, "watt_hours_day": {day: sum(period.values())}},
            "message": {"code": 0, "type": "success", "ratelimit": {"limit": 12, "remaining": 11}}}


def test_compass_bearings_become_forecast_solars() -> None:
    assert [fsolar.azimuth(x) for x in (180, 90, 270, 0, 360)] == [0, -90, 90, -180, -180]


def test_the_answer_read_into_periods_days_and_hours() -> None:
    rows = fsolar.periods(answer(), LON)
    assert len(rows) == 16 and rows[0]["start"] == datetime(2026, 11, 14, 8, tzinfo=UTC)
    assert rows[0]["wh"] == answer()["result"]["watt_hours_period"][datetime(2026, 11, 14, 8, 30, tzinfo=LON).isoformat()]
    total = sum(answer()["result"]["watt_hours_period"].values()) / 1000
    assert fsolar.days(rows, LON) == {"2026-11-14": round(total, 2)}
    h = fsolar.hours(rows, LON, datetime(2026, 11, 14).date())
    assert round(sum(h), 3) == round(total, 3) and h[7] == 0 and h[12] > h[9]
    plain = {"result": {"watts": {"2026-11-14 12:00:00": 500, "2026-11-14 12:30:00": 600},
                        "watt_hours_period": {"2026-11-14 12:00:00": 0, "2026-11-14 12:30:00": 275}}}  # the default local format
    p = fsolar.periods(plain, LON)
    assert p == [{"start": datetime(2026, 11, 14, 12, tzinfo=UTC), "end": datetime(2026, 11, 14, 12, 30, tzinfo=UTC), "watts": 500.0, "wh": 275.0}]
    with pytest.raises(fsolar.SolarError):
        fsolar.periods({"result": {"watts": []}}, LON)


def test_power_now_and_what_is_still_to_come() -> None:
    rows = [{**r, "plane": "south"} for r in fsolar.periods(answer(), LON)]
    rows += [{**r, "plane": "east"} for r in fsolar.periods(answer(scale=0.5), LON)]
    noon = datetime(2026, 11, 14, 12, tzinfo=UTC)
    assert fsolar.now_watts(rows, noon) == 1500
    assert fsolar.now_watts(rows, noon + timedelta(minutes=15)) == round((1000 + 875) / 2 + (500 + 437.5) / 2)
    whole = fsolar.days(rows, LON)["2026-11-14"]
    assert fsolar.remaining(rows, datetime(2026, 11, 14, 6, tzinfo=UTC), LON) == whole
    assert fsolar.remaining(rows, datetime(2026, 11, 14, 17, tzinfo=UTC), LON) == 0


def test_arrays_copied_from_home_assistant() -> None:
    entries = {"data": {"entries": [
        {"domain": "forecast_solar", "title": "South roof", "data": {"latitude": 51.3},
         "options": {"declination": 35, "azimuth": 180, "modules_power": 4200}},
        {"domain": "forecast_solar", "title": "Broken", "options": {"declination": 30}},
        {"domain": "met", "title": "Home"}]}}
    assert fsolar.from_ha(entries) == [{"name": "South roof", "declination": 35.0, "azimuth": 180.0, "kwp": 4.2}]


@pytest.mark.skipif(not URL, reason="needs WA_TEST_DATABASE_URL")
def test_a_fetch_is_kept_with_tomorrows_figure() -> None:
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.modules.solar import api

    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS solar_forecast, solar_day, app_setting, place"))
        conn.execute(sa.text("CREATE TABLE app_setting (key text primary key, value jsonb, updated_at timestamptz)"))
        conn.execute(sa.text("CREATE TABLE place (id text primary key, name text, kind text, lat float8, lon float8)"))
        conn.execute(sa.text("INSERT INTO place VALUES ('home', 'Home', 'home', 51.33, -0.27)"))
        conn.execute(sa.text("INSERT INTO app_setting VALUES ('solar.settings', CAST(:v AS jsonb), now())"),
                     {"v": json.dumps({"planes": [{"name": "South", "declination": 35, "azimuth": 180, "kwp": 4.2}]})})
        mig = importlib.import_module("migrations.versions.0056_solar")
        mig.op = Operations(MigrationContext.configure(conn))  # type: ignore[attr-defined]
        mig.upgrade()
    asked: list[str] = []
    tz = ZoneInfo("Europe/London")
    today = datetime.now(tz).date()

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        a, b = answer(today.isoformat()), answer((today + timedelta(days=1)).isoformat(), 0.5)
        for k in ("watts", "watt_hours_period"):
            a["result"][k].update(b["result"][k])
        return httpx.Response(200, json=a)

    async def go() -> dict[str, Any]:
        engine = create_async_engine(URL)  # type: ignore[arg-type]
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                out = await api.sync(engine, client)
            async with engine.connect() as conn:
                day = (await conn.execute(sa.text("SELECT day_ahead_kwh, morning_kwh FROM solar_day WHERE day = :d"),
                                          {"d": today + timedelta(days=1)})).first()
            return {**out, "day_ahead": day[0] if day else None}
        finally:
            await engine.dispose()

    try:
        out = asyncio.run(go())
    finally:
        with sync.begin() as conn:
            conn.execute(sa.text("DROP TABLE IF EXISTS solar_forecast, solar_day, app_setting, place"))
        sync.dispose()
    assert "/estimate/51.3300/-0.2700/35/0/4.2?time=iso8601" in asked[0]
    assert out["periods"] > 30 and out["tomorrow_kwh"] == out["day_ahead"] and out["today_kwh"] == round(2 * out["tomorrow_kwh"], 2)
