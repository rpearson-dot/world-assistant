"""Weather from Open-Meteo: reading its hourly answer, each day in brief with heating degree-hours, and (with a
database) the hourly job keeping the forecast and filling in the past."""

from __future__ import annotations

import asyncio
import importlib
import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy import text

from app.modules.weather import meteo

LON = ZoneInfo("Europe/London")
URL = os.environ.get("WA_TEST_DATABASE_URL")


def _body(start: datetime, temps: list[float]) -> dict[str, Any]:
    times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(len(temps))]
    n = len(temps)
    return {"hourly": {"time": times, "temperature_2m": temps, "apparent_temperature": [t - 2 for t in temps],
                       "precipitation": [0.2] * n, "cloud_cover": [50] * n, "wind_speed_10m": [10] * n,
                       "shortwave_radiation": [100] * n, "weather_code": [3] * n}}  # fmt: skip


def test_hours_and_a_day_in_brief() -> None:
    day = date(2026, 11, 16)
    start = datetime(2026, 11, 16, 0, 0, tzinfo=UTC)  # November: London is on GMT
    temps = [5.0] * 16 + [3.5, 3.5, 3.5] + [8.0] * 5  # 16:00 to 19:00 at 3.5 °C
    rows = meteo.hours(_body(start, temps))
    assert rows[0]["at"] == start and rows[0]["code"] == 3 and rows[16]["temp_c"] == 3.5
    s = meteo.summary(rows, day, LON)
    assert (s["low_c"], s["high_c"], s["hours"], s["mostly"]) == (3.5, 8.0, 24, "overcast")
    assert s["evening_c"] == 3.5 and s["evening_degree_hours"] == 36.0  # 3 hours, 12 degrees under 15.5
    assert s["degree_hours"] == round(16 * 10.5 + 3 * 12 + 5 * 7.5, 1)
    assert s["rain_mm"] == 4.8 and s["sun_kwh_m2"] == 2.4
    with pytest.raises(ValueError):
        meteo.hours({"error": True, "reason": "bad latitude"})


@pytest.fixture
def db() -> Iterator[str]:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mig = importlib.import_module("migrations.versions.0053_weather")
    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS weather_hour; DROP TABLE IF EXISTS job_run; DROP TABLE IF EXISTS place"))
        conn.execute(text("CREATE TABLE place (id text primary key, name text, kind text, lat float8, lon float8)"))
        conn.execute(text("INSERT INTO place VALUES ('home', 'Home', 'home', 51.38, -0.24)"))
        conn.execute(text("CREATE TABLE job_run (id bigserial primary key, job text, started_at timestamptz default now(), "
                          "finished_at timestamptz, status text default 'running', result jsonb, error text)"))
        with Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()
    yield URL  # type: ignore[misc]
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS weather_hour; DROP TABLE IF EXISTS job_run; DROP TABLE IF EXISTS place"))
    sync.dispose()


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
def test_the_job_keeps_the_forecast_and_fills_in_the_past(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.modules.weather import api

    asked: list[str] = []
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)

    def handle(req: httpx.Request) -> httpx.Response:
        asked.append(req.url.host)
        if "archive" in req.url.host:
            s = datetime.fromisoformat(req.url.params["start_date"]).replace(tzinfo=UTC)
            return httpx.Response(200, json=_body(s, [6.0] * 24 * 3))
        return httpx.Response(200, json=_body(now - timedelta(hours=48), [7.0] * (48 + 24 * 7)))

    real = httpx.AsyncClient
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handle), **kw))

    async def no_ha(_e: Any, _p: str) -> None:
        return None

    monkeypatch.setattr(api, "ha_publish", no_ha)

    async def go() -> Any:
        engine = create_async_engine(db, poolclass=NullPool)
        out = await api.weather_sync({"engine": engine})
        async with engine.connect() as c:
            n = dict((await c.execute(text("SELECT source, count(*) FROM weather_hour GROUP BY source"))).all())
        await engine.dispose()
        return out, n

    out, n = asyncio.run(go())
    assert out["forecast"] > 24 * 6 and out["observed"] >= 46 and out["archive"] == 72
    assert asked == ["api.open-meteo.com", "archive-api.open-meteo.com"]
    assert n["forecast"] == out["forecast"] and n["observed"] == out["observed"] + 72
