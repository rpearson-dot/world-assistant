"""Solar forecast (/v1/solar, the Energy → Solar tab): forecast.solar's expected output of your arrays for today and
tomorrow, refreshed each hour; each day's total as forecast the evening before, kept, and set against what the panels
really made (from Home Assistant's long-term statistics) so its accuracy is known before anything relies on it.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth import require_token
from app.config import get_settings
from app.jobs import tracked
from app.modules.solar import fsolar

router = APIRouter(prefix="/v1/solar", tags=["solar"], dependencies=[Depends(require_token)])
KEY = "solar.settings"
DEFAULTS: dict[str, Any] = {"planes": [], "pv_statistic": ""}


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().local_tz)


async def settings(conn: Any) -> dict[str, Any]:
    v = (await conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": KEY})).scalar()
    v = json.loads(v) if isinstance(v, str) else v
    return {**DEFAULTS, **(v or {})}


async def _home(conn: Any) -> tuple[float, float] | None:
    r = (await conn.execute(text(
        "SELECT lat, lon FROM place WHERE lat IS NOT NULL ORDER BY (kind = 'home') DESC, (id = 'home') DESC, name LIMIT 1"))).first()  # fmt: skip
    return (float(r[0]), float(r[1])) if r else None


async def rows(conn: Any, start: datetime, end: datetime) -> list[dict[str, Any]]:
    got = (await conn.execute(text(
        "SELECT plane, period_start, watts, wh FROM solar_forecast WHERE period_start >= :a AND period_start < :b "
        "ORDER BY plane, period_start"), {"a": start, "b": end})).all()  # fmt: skip
    out = []
    for i, r in enumerate(got):
        nxt = got[i + 1] if i + 1 < len(got) and got[i + 1][0] == r[0] else None
        out.append({"plane": r[0], "start": r[1], "end": nxt[1] if nxt else r[1] + timedelta(minutes=30),
                    "watts": float(r[2] or 0), "wh": float(r[3] or 0)})  # fmt: skip
    return out


def _day_bounds(d: date, tz: Any) -> tuple[datetime, datetime]:
    a = datetime(d.year, d.month, d.day, tzinfo=tz)
    return a.astimezone(UTC), (a + timedelta(days=1)).astimezone(UTC)


async def summary(engine: Any) -> dict[str, Any]:
    tz, now = _tz(), datetime.now(UTC)
    today = now.astimezone(tz).date()
    async with engine.connect() as conn:
        rs = await rows(conn, _day_bounds(today, tz)[0], _day_bounds(today + timedelta(days=1), tz)[1])
        fetched = (await conn.execute(text("SELECT max(fetched_at) FROM solar_forecast"))).scalar()
    per = fsolar.days(rs, tz)
    return {"today_kwh": per.get(today.isoformat(), 0.0), "tomorrow_kwh": per.get((today + timedelta(days=1)).isoformat()),
            "remaining_today_kwh": fsolar.remaining(rs, now, tz), "power_now_w": fsolar.now_watts(rs, now),
            "today_hours": fsolar.hours(rs, tz, today), "tomorrow_hours": fsolar.hours(rs, tz, today + timedelta(days=1)),
            "fetched_at": fetched}  # fmt: skip


@router.get("")
async def solar(request: Request) -> dict[str, Any]:
    """Today's and tomorrow's expected kWh, what is still to come today, the power expected now, and each hour."""
    async with request.app.state.engine.connect() as conn:
        st = await settings(conn)
    if not st["planes"]:
        raise HTTPException(503, "no solar arrays set: add them on Energy → Solar (or copy them from Home Assistant)")
    return {**await summary(request.app.state.engine), "planes": st["planes"]}


@router.get("/accuracy")
async def accuracy(request: Request, days: int = 14) -> dict[str, Any]:
    """Each recent day: the total forecast the evening before, and what the panels made (Home Assistant's
    statistic chosen on the page). How far off it usually is says how far to trust tomorrow's."""
    from app import ha_stats as hass

    days = max(3, min(days, 60))
    tz, now = _tz(), datetime.now(UTC)
    today = now.astimezone(tz).date()
    async with request.app.state.engine.connect() as conn:
        st = await settings(conn)
        kept = {r[0].isoformat(): r[1] for r in (await conn.execute(text(
            "SELECT day, day_ahead_kwh FROM solar_day WHERE day >= :d AND day < :t ORDER BY day"),
            {"d": today - timedelta(days=days), "t": today})).all()}  # fmt: skip
    out: dict[str, Any] = {"statistic": st["pv_statistic"], "days": []}
    try:
        choices = await hass.energy_statistics(request.app.state.engine)
    except Exception as exc:  # noqa: BLE001 - shown on the page
        return {**out, "error": f"Home Assistant: {exc}", "forecast_only": kept}
    out["choices"] = choices
    stat = st["pv_statistic"] or next((c["id"] for c in choices if any(w in c["id"].lower() for w in ("pv", "solar"))), "")
    if not stat:
        return {**out, "error": "choose the statistic that is your panels' total output", "forecast_only": kept}
    out["statistic"] = stat
    start = _day_bounds(today - timedelta(days=days), tz)[0]
    made: dict[str, float] = {}
    for at, kwh in await hass.hourly_kwh(request.app.state.engine, stat, start, _day_bounds(today, tz)[0]):
        k = at.astimezone(tz).date().isoformat()
        made[k] = made.get(k, 0.0) + kwh
    errs = []
    for d in sorted(set(kept) | set(made)):
        f, m = kept.get(d), made.get(d)
        out["days"].append({"day": d, "forecast_kwh": f, "made_kwh": round(m, 2) if m is not None else None})
        if f is not None and m is not None:
            errs.append(abs(f - m))
    out["typical_miss_kwh"] = round(sum(errs) / len(errs), 1) if errs else None
    return out


class Plane(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    declination: float = Field(ge=0, le=90, description="tilt from flat, degrees")
    azimuth: float = Field(ge=0, lt=360, description="compass bearing it faces: 180 south, 90 east, 270 west")
    kwp: float = Field(gt=0, le=100, description="peak kW of the array")


class SettingsIn(BaseModel):
    planes: list[Plane] = Field(default_factory=list, max_length=6)
    pv_statistic: str = Field("", max_length=200)


@router.get("/settings")
async def get_settings_(request: Request) -> dict[str, Any]:
    async with request.app.state.engine.connect() as conn:
        return await settings(conn)


@router.put("/settings")
async def put_settings(request: Request, body: SettingsIn) -> dict[str, Any]:
    """Your arrays (up to 6: forecast.solar allows 12 requests an hour, one an array) and the Home Assistant
    statistic that is the panels' total output (for the accuracy check)."""
    names = [p.name for p in body.planes]
    if len(names) != len(set(names)):
        raise HTTPException(422, "two arrays have the same name")
    st = body.model_dump()
    async with request.app.state.engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"), {"k": KEY, "v": json.dumps(st)})  # fmt: skip
        await conn.execute(text("DELETE FROM solar_forecast WHERE plane <> ALL(:n)"), {"n": names})
    return st


@router.post("/from-home-assistant")
async def from_home_assistant(request: Request) -> dict[str, Any]:
    """The arrays set in Home Assistant's Forecast.Solar integration, read from its files over the share set on the
    Home Assistant page (not saved: check them, then Save)."""
    from app import ha_smb

    try:
        body = await ha_smb.read_config_file(request.app.state.engine, ".storage", "core.config_entries")
    except LookupError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ha_smb.SmbError as exc:
        raise HTTPException(502, str(exc)) from exc
    if not body:
        raise HTTPException(404, "Home Assistant's settings file was not found on the share (.storage/core.config_entries)")
    planes = fsolar.from_ha(json.loads(body))
    if not planes:
        raise HTTPException(404, "Home Assistant has no Forecast.Solar arrays set up")
    return {"planes": planes}


@router.post("/refresh")
async def refresh(request: Request) -> dict[str, Any]:
    """Fetch the forecast now (uses one of the hour's 12 requests for each array)."""
    return await tracked(request.app.state.engine, "solar_sync", lambda: sync(request.app.state.engine))


async def sync(engine: Any, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    tz, now = _tz(), datetime.now(UTC)
    async with engine.connect() as conn:
        st, home = await settings(conn), await _home(conn)
    if not st["planes"]:
        return {"not_set_up": "No solar arrays yet: add them on Energy → Solar, or copy them from Home Assistant"}
    if home is None:
        return {"not_set_up": "No home place: add one called home on the Places page"}
    own = client is None
    client = client or httpx.AsyncClient(headers={"User-Agent": "WorldAssistant"})
    got, errors = [], []
    try:
        for p in st["planes"]:
            try:
                got += await fsolar.estimate(client, home[0], home[1], p, tz)
            except (fsolar.SolarError, httpx.HTTPError) as exc:
                errors.append(f"{p['name']}: {exc}"[:200])
    finally:
        if own:
            await client.aclose()
    if errors and not got:
        raise fsolar.SolarError("; ".join(errors))
    today = now.astimezone(tz).date()
    per = fsolar.days(got, tz)
    async with engine.begin() as conn:
        if got:
            await conn.execute(text(
                "INSERT INTO solar_forecast (plane, period_start, watts, wh) VALUES (:p, :s, :w, :h) "
                "ON CONFLICT (plane, period_start) DO UPDATE SET watts = EXCLUDED.watts, wh = EXCLUDED.wh, fetched_at = now()"),
                [{"p": r["plane"], "s": r["start"], "w": r["watts"], "h": r["wh"]} for r in got])  # fmt: skip
        tomorrow = (today + timedelta(days=1)).isoformat()
        if tomorrow in per and len(errors) == 0:  # the newest forecast for tomorrow made today: the day-ahead figure
            await conn.execute(text(
                "INSERT INTO solar_day (day, day_ahead_kwh) VALUES (:d, :k) "
                "ON CONFLICT (day) DO UPDATE SET day_ahead_kwh = EXCLUDED.day_ahead_kwh, updated_at = now()"),
                {"d": today + timedelta(days=1), "k": per[tomorrow]})  # fmt: skip
        if today.isoformat() in per and len(errors) == 0:
            await conn.execute(text(
                "INSERT INTO solar_day (day, morning_kwh) VALUES (:d, :k) ON CONFLICT (day) DO UPDATE "
                "SET morning_kwh = coalesce(solar_day.morning_kwh, EXCLUDED.morning_kwh)"), {"d": today, "k": per[today.isoformat()]})  # fmt: skip
        await conn.execute(text("DELETE FROM solar_forecast WHERE period_start < now() - interval '3 days'"))
        await conn.execute(text("DELETE FROM solar_day WHERE day < current_date - 400"))
    out: dict[str, Any] = {"periods": len(got), "today_kwh": per.get(today.isoformat()), "tomorrow_kwh": per.get((today + timedelta(days=1)).isoformat())}
    if errors:
        out["errors"] = errors
    try:
        await ha_publish(engine)
    except Exception as exc:  # noqa: BLE001 - Home Assistant is optional
        out["ha"] = str(exc)[:120]
    return out


async def ha_publish(engine: Any) -> None:
    """Home Assistant sensors: expected kWh today, still to come today, tomorrow, and the power expected now."""
    from app.mqtt import publish
    from app.mqtt_config import load

    s = get_settings()
    conf, _ = await load(engine)
    if not conf.active:
        return
    state = await summary(engine)
    topic = f"{s.mqtt_topic_prefix}/solar/state"
    dev = {"identifiers": ["world_assistant_solar"], "name": "Solar forecast (World Assistant)", "manufacturer": "forecast.solar via World Assistant"}
    sensors = [("today", "Solar expected today", "today_kwh", "kWh", "energy"),
               ("remaining_today", "Solar still to come today", "remaining_today_kwh", "kWh", "energy"),
               ("tomorrow", "Solar expected tomorrow", "tomorrow_kwh", "kWh", "energy"),
               ("power_now", "Solar power expected now", "power_now_w", "W", "power")]  # fmt: skip
    msgs = []
    for key, name, field, unit, dc in sensors:
        uid = f"wa_solar_{key}"
        cfg = {"name": name, "unique_id": uid, "default_entity_id": f"sensor.{uid}", "device": dev, "state_topic": topic,
               "value_template": f"{{{{ value_json.{field} }}}}", "unit_of_measurement": unit, "device_class": dc,
               **({"state_class": "measurement"} if dc == "power" else {})}  # fmt: skip
        msgs.append((f"{s.mqtt_discovery_prefix}/sensor/{uid}/config", json.dumps(cfg), True))
    msgs.append((topic, json.dumps({k: v for k, v in state.items() if not k.endswith("hours")}, default=str), True))
    await publish(msgs, conf)


async def solar_sync(ctx: dict[str, Any]) -> Any:
    """Each hour from early morning to evening: the forecast for today and tomorrow."""
    return await tracked(ctx["engine"], "solar_sync", lambda: sync(ctx["engine"]))
