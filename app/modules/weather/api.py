"""Weather (/v1/weather) for your home place from Open-Meteo: now, the next hours, each day in brief with its heating
degree-hours, and the past as it turned out. The worker keeps it: the forecast every hour, the past once a day."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from app.auth import require_token
from app.config import get_settings
from app.jobs import tracked
from app.modules.weather import meteo

router = APIRouter(prefix="/v1/weather", tags=["weather"], dependencies=[Depends(require_token)])
BACKFILL_DAYS = 92
COLS = ("temp_c", "apparent_c", "precip_mm", "cloud_pct", "wind_kph", "radiation_wm2", "code")


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().local_tz)


async def home(conn: Any) -> tuple[str, float, float] | None:
    row = (await conn.execute(text(
        "SELECT id, lat, lon FROM place WHERE lat IS NOT NULL ORDER BY (kind = 'home') DESC, (id = 'home') DESC, name LIMIT 1"))).first()  # fmt: skip
    return (row[0], float(row[1]), float(row[2])) if row else None


async def keep(conn: Any, place: str, rows: list[dict[str, Any]], source: str) -> int:
    if not rows:
        return 0
    await conn.execute(text(
        "INSERT INTO weather_hour (place, at, source, temp_c, apparent_c, precip_mm, cloud_pct, wind_kph, radiation_wm2, code, fetched_at) "
        "VALUES (:place, :at, :source, :temp_c, :apparent_c, :precip_mm, :cloud_pct, :wind_kph, :radiation_wm2, :code, now()) "
        "ON CONFLICT (place, at, source) DO UPDATE SET " + ", ".join(f"{c} = EXCLUDED.{c}" for c in COLS) + ", fetched_at = now()"),
        [{**r, "place": place, "source": source} for r in rows])  # fmt: skip
    return len(rows)


async def rows_between(conn: Any, place: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Each hour once: as it turned out where known, else as forecast."""
    rs = (await conn.execute(text(
        "SELECT DISTINCT ON (at) at, " + ", ".join(COLS) + ", source FROM weather_hour WHERE place = :p AND at >= :a AND at < :b "
        "ORDER BY at, (source = 'observed') DESC"), {"p": place, "a": start, "b": end})).mappings().all()  # fmt: skip
    return [dict(r) for r in rs]


@router.get("")
async def weather(request: Request) -> dict[str, Any]:
    """Now, the next 48 hours, and today plus the next six days in brief (low, high, rain, sunshine, heating
    degree-hours all day and 16:00 to 19:00)."""
    tz, now = _tz(), datetime.now(UTC)
    async with request.app.state.engine.connect() as conn:
        h = await home(conn)
        if h is None:
            raise HTTPException(409, "no place with a position: add your home on the Places page")
        today = now.astimezone(tz).date()
        rows = await rows_between(conn, h[0], datetime(today.year, today.month, today.day, tzinfo=tz).astimezone(UTC), now + timedelta(days=8))
        fetched = (await conn.execute(text("SELECT max(fetched_at) FROM weather_hour WHERE place = :p AND source = 'forecast'"), {"p": h[0]})).scalar()
    if not rows:
        raise HTTPException(503, "no weather yet: it is fetched within the hour after the module is switched on")
    cur = min(rows, key=lambda r: abs((r["at"] - now).total_seconds()))
    nxt = [r for r in rows if now - timedelta(hours=1) < r["at"] <= now + timedelta(hours=48)]
    return {"place": h[0], "fetched_at": fetched, "now": {**cur, "words": meteo.CODES.get(cur["code"])},
            "hours": nxt, "days": [meteo.summary(rows, today + timedelta(days=i), tz) for i in range(7)],
            "credit": "Weather data by Open-Meteo.com (CC BY 4.0)"}  # fmt: skip


@router.get("/history")
async def history(request: Request, days: int = Query(30, ge=1, le=400)) -> dict[str, Any]:
    """Each past day as it turned out: low, high, rain, sunshine and heating degree-hours (all day and 16:00 to 19:00)."""
    tz, now = _tz(), datetime.now(UTC)
    today = now.astimezone(tz).date()
    start = today - timedelta(days=days)
    async with request.app.state.engine.connect() as conn:
        h = await home(conn)
        if h is None:
            raise HTTPException(409, "no place with a position")
        rows = await rows_between(conn, h[0], datetime(start.year, start.month, start.day, tzinfo=tz).astimezone(UTC),
                                  datetime(today.year, today.month, today.day, tzinfo=tz).astimezone(UTC))  # fmt: skip
    return {"place": h[0], "days": [meteo.summary(rows, start + timedelta(days=i), tz) for i in range(days)]}


async def ha_publish(engine: Any, place: str) -> None:
    """Home Assistant sensors: the temperature now, tomorrow evening's (16:00 to 19:00), and heating degree-hours."""
    import json

    from app.mqtt import publish
    from app.mqtt_config import load

    s = get_settings()
    conf, _ = await load(engine)
    if not conf.active:
        return
    tz, now = _tz(), datetime.now(UTC)
    today = now.astimezone(tz).date()
    async with engine.connect() as conn:
        rows = await rows_between(conn, place, datetime(today.year, today.month, today.day, tzinfo=tz).astimezone(UTC), now + timedelta(days=3))
    if not rows:
        return
    cur = min(rows, key=lambda r: abs((r["at"] - now).total_seconds()))
    t0, t1 = meteo.summary(rows, today, tz), meteo.summary(rows, today + timedelta(days=1), tz)
    state = {"temp_c": cur["temp_c"], "words": meteo.CODES.get(cur["code"]), "today_dh": t0["degree_hours"],
             "tomorrow_dh": t1["degree_hours"], "tomorrow_evening_c": t1["evening_c"], "tomorrow_evening_dh": t1["evening_degree_hours"],
             "today_low_c": t0["low_c"], "today_high_c": t0["high_c"]}  # fmt: skip
    topic = f"{s.mqtt_topic_prefix}/weather/state"
    dev = {"identifiers": ["world_assistant_weather"], "name": "Weather (World Assistant)", "manufacturer": "Open-Meteo via World Assistant"}
    sensors = [("temperature", "Temperature", "temp_c", "°C", "temperature"),
               ("today_degree_hours", "Heating degree-hours today", "today_dh", "°C·h", None),
               ("tomorrow_degree_hours", "Heating degree-hours tomorrow", "tomorrow_dh", "°C·h", None),
               ("tomorrow_evening", "Tomorrow evening temperature", "tomorrow_evening_c", "°C", "temperature"),
               ("tomorrow_evening_degree_hours", "Heating degree-hours tomorrow evening", "tomorrow_evening_dh", "°C·h", None)]  # fmt: skip
    msgs = []
    for key, name, field, unit, dc in sensors:
        uid = f"wa_weather_{key}"
        cfg = {"name": name, "unique_id": uid, "default_entity_id": f"sensor.{uid}", "device": dev, "state_topic": topic,
               "value_template": f"{{{{ value_json.{field} }}}}", "unit_of_measurement": unit, "state_class": "measurement",
               **({"device_class": dc} if dc else {})}  # fmt: skip
        msgs.append((f"{s.mqtt_discovery_prefix}/sensor/{uid}/config", json.dumps(cfg), True))
    msgs.append((topic, json.dumps(state), True))
    await publish(msgs, conf)


async def weather_sync(ctx: dict[str, Any]) -> Any:
    """Every hour: the forecast (and the last two days, as they turned out). Once a day, the past filled in from the
    archive back 92 days where missing (the archive runs about five days behind)."""
    engine = ctx["engine"]
    async with engine.connect() as conn:
        h = await home(conn)
    if h is None:
        return "no home place"

    async def work() -> dict[str, Any]:
        place, lat, lon = h
        now = datetime.now(UTC)
        out: dict[str, Any] = {}
        async with httpx.AsyncClient(headers={"User-Agent": "WorldAssistant"}) as client:
            rows = await meteo.forecast(client, lat, lon)
            past = [r for r in rows if r["at"] < now - timedelta(hours=1)]
            ahead = [r for r in rows if r["at"] >= now - timedelta(hours=1)]
            async with engine.begin() as conn:
                out["forecast"] = await keep(conn, place, ahead, "forecast")
                out["observed"] = await keep(conn, place, past, "observed")
                have = (await conn.execute(text(
                    "SELECT count(DISTINCT date_trunc('day', at)) FROM weather_hour WHERE place = :p AND source = 'observed' "
                    "AND at > now() - make_interval(days => :d)"), {"p": place, "d": BACKFILL_DAYS})).scalar() or 0  # fmt: skip
            if have < BACKFILL_DAYS - 3:
                end = (now - timedelta(days=5)).date()
                arch = await meteo.archive(client, lat, lon, end - timedelta(days=BACKFILL_DAYS), end)
                async with engine.begin() as conn:
                    out["archive"] = await keep(conn, place, [r for r in arch if r["temp_c"] is not None], "observed")
        try:
            await ha_publish(engine, place)
        except Exception as exc:  # noqa: BLE001 - Home Assistant is optional
            out["ha"] = str(exc)[:120]
        return out

    return await tracked(engine, "weather_sync", work)
