"""The home energy planner (/v1/planner, the Energy → Planner tab).

- **Evening demand:** how closely the house's evening use (16:00 to 19:00) follows the cold, from Home Assistant's
  long-term statistics and the weather module's history, and what tomorrow evening is likely to need.
- **Charging, in shadow:** every five minutes, what World Assistant would do with the car (charge, wait, full,
  unplugged, off) and why, beside Home Assistant's plan at the same moment. With "act" on (and the charger on World
  Assistant's OCPP, set to Auto), World Assistant starts and stops the charger itself and Home Assistant's own
  start and stop are set aside.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth import require_token
from app.config import get_settings
from app.jobs import tracked
from app.modules.planner import charge, demand, hass

router = APIRouter(prefix="/v1/planner", tags=["planner"], dependencies=[Depends(require_token)])
KEY = "planner.settings"
ACTING = "wa:planner:acting"  # set while World Assistant acts; the charger's Home Assistant switch then stands aside
DEFAULTS: dict[str, Any] = {
    "demand_statistic": "", "demand_days": 90, "evening_from": 16, "evening_to": 19,
    "battery_entity": "sensor.office_inverter_battery_soc", "grid_entity": "", "export_is_negative": True,
    "solar_start_w": 3500, "solar_stop_w": 2500, "act": False,
}  # fmt: skip
HA_IDS = ("sensor.wa_car_charging_plan", "sensor.wa_charger_status", "sensor.wa_car_charge_level", "sensor.wa_charger_power",
          "input_number.wa_ev_target_soc", "input_number.wa_ev_min_soc", "input_boolean.wa_ev_low_enabled")  # fmt: skip


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().local_tz)


async def settings(conn: Any) -> dict[str, Any]:
    v = (await conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": KEY})).scalar()
    v = json.loads(v) if isinstance(v, str) else v
    return {**DEFAULTS, **(v or {})}


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --- evening demand -----------------------------------------------------------------------------------------------


async def demand_report(engine: Any, redis: Any = None) -> dict[str, Any]:
    async with engine.connect() as conn:
        st = await settings(conn)
    if redis is not None:
        cached = await redis.get("wa:planner:demand")
        if cached:
            return json.loads(cached)
    tz, now = _tz(), datetime.now(UTC)
    out: dict[str, Any] = {"statistic": st["demand_statistic"], "window": f"{st['evening_from']:02d}:00 to {st['evening_to']:02d}:00"}
    try:
        choices = await hass.energy_statistics(engine)
    except Exception as exc:  # noqa: BLE001 - shown on the page
        return {**out, "error": f"Home Assistant: {exc}"}
    out["choices"] = choices
    stat = st["demand_statistic"] or next((c["id"] for c in choices if "load" in c["id"].lower()), "")
    if not stat:
        return {**out, "error": "choose the statistic that is the house's total use (the inverter's load energy)"}
    out["statistic"] = stat
    start = now - timedelta(days=int(st["demand_days"]))
    hourly = await hass.hourly_kwh(engine, stat, start, now)
    eve = demand.evenings(hourly, tz, st["evening_from"], st["evening_to"])
    from app.modules.weather import meteo
    from app.modules.weather.api import home, rows_between

    async with engine.connect() as conn:
        h = await home(conn)
        wrows = await rows_between(conn, h[0], start, now + timedelta(days=2)) if h else []
    points, table = [], []
    for day, kwh in sorted(eve.items()):
        d = datetime.fromisoformat(day).date()
        s = meteo.summary(wrows, d, tz)
        if s["hours"] >= 20:
            dh = meteo.degree_hours(meteo.window(wrows, datetime(d.year, d.month, d.day, st["evening_from"], tzinfo=tz).astimezone(UTC),
                                                 datetime(d.year, d.month, d.day, st["evening_to"], tzinfo=tz).astimezone(UTC)))
            points.append((dh, kwh))
            table.append({"day": day, "evening_kwh": kwh, "evening_degree_hours": dh})
    f = demand.fit(points)
    tomorrow = (now.astimezone(tz) + timedelta(days=1)).date()
    tdh = meteo.degree_hours(meteo.window(wrows, datetime(tomorrow.year, tomorrow.month, tomorrow.day, st["evening_from"], tzinfo=tz).astimezone(UTC),
                                          datetime(tomorrow.year, tomorrow.month, tomorrow.day, st["evening_to"], tzinfo=tz).astimezone(UTC)))
    out |= {"fit": f, "tomorrow": demand.predict(f, tdh), "days": table[-60:], "computed_at": now.isoformat()}
    try:  # what the panels should make tomorrow, from the solar forecast module when it is on
        from app.modules.solar.api import summary as solar

        out["solar_tomorrow_kwh"] = (await solar(engine))["tomorrow_kwh"]
    except Exception:  # noqa: BLE001 - optional
        out["solar_tomorrow_kwh"] = None
    if redis is not None:
        await redis.set("wa:planner:demand", json.dumps(out, default=str), ex=6 * 3600)
    return out


@router.get("/demand")
async def get_demand(request: Request, fresh: bool = False) -> dict[str, Any]:
    """How predictable evening demand is from the cold (a straight-line fit of each evening's kWh on its heating
    degree-hours), and tomorrow evening's likely need. Worked out at most every six hours unless fresh."""
    if fresh:
        await request.app.state.redis.delete("wa:planner:demand")
    return await demand_report(request.app.state.engine, request.app.state.redis)


# --- charging, in shadow ---------------------------------------------------------------------------------------------


async def gather(engine: Any, redis: Any) -> tuple[charge.Now, dict[str, Any]]:
    """What is true now, from World Assistant's own modules where they are on, else from Home Assistant."""
    async with engine.connect() as conn:
        st = await settings(conn)
    n = charge.Now(settings=st)
    info: dict[str, Any] = {}
    ids = list(HA_IDS) + [e for e in (st["battery_entity"], st["grid_entity"]) if e]
    try:
        ha = await hass.states(engine, ids)
    except Exception as exc:  # noqa: BLE001 - recorded with the decision
        ha, info["ha_error"] = {}, str(exc)[:160]

    def hv(eid: str) -> Any:
        return (ha.get(eid) or {}).get("state")

    info["ha_plan"] = hv("sensor.wa_car_charging_plan")
    n.status, n.soc = hv("sensor.wa_charger_status"), _num(hv("sensor.wa_car_charge_level"))
    n.car_w = (_num(hv("sensor.wa_charger_power")) or 0.0) * 1000
    n.target = _num(hv("input_number.wa_ev_target_soc")) or 80.0
    n.low, n.low_on = _num(hv("input_number.wa_ev_min_soc")) or 20.0, hv("input_boolean.wa_ev_low_enabled") == "on"
    n.battery_soc = _num(hv(st["battery_entity"])) if st["battery_entity"] else None
    g = _num(hv(st["grid_entity"])) if st["grid_entity"] else None
    n.export_w = None if g is None else (-g if st["export_is_negative"] else g)
    try:  # the charger on World Assistant's OCPP: its own state and the master switch
        from app.modules.ocpp import store as ostore

        async with engine.connect() as conn:
            o = await ostore.settings(conn)
            rows = [r for r in await ostore.chargers(conn) if r["connected"]]
        n.mode = o.get("mode", "auto")
        if rows:
            c = rows[0]
            n.status, info["charger"] = c["status"], c["id"]
            n.car_w = float((c["meter"] or {}).get("power_w") or 0) if c["status"] == "Charging" else 0.0
    except Exception as exc:  # noqa: BLE001 - no OCPP module: Home Assistant's charger
        info["ocpp"] = f"not used: {exc}"[:120]
    try:  # the car's own reading when Home Assistant has none
        if n.soc is None:
            from app.modules.ovms import store as vstore

            async with engine.connect() as conn:
                for v in await vstore.vehicles(conn):
                    m = await vstore.metrics(conn, v)
                    if "v.b.soc" in m:
                        n.soc = _num(m["v.b.soc"][0])
                        break
    except Exception as exc:  # noqa: BLE001
        info["ovms"] = f"not used: {exc}"[:120]
    try:  # the cheap rate and free hours, from the Octopus module
        from app.modules.octopus import sessions as sess
        from app.modules.octopus import store as estore
        from app.modules.octopus import summary as esum

        now = datetime.now(UTC)
        tariff, periods = await estore.load(engine)
        s = esum.summarise(tariff, periods, now)
        n.cheap, n.cheap_pence = bool(s.get("cheap_now")), (s.get("now") or {}).get("pence")
        n.free = bool(sess.summarise(await estore.load_sessions(engine), now)["free"].get("now_joined"))
    except Exception as exc:  # noqa: BLE001
        info["octopus_error"] = str(exc)[:120]
    last = await redis.get("wa:planner:solar") if redis is not None else None
    n.solar_before = (last.decode() if isinstance(last, bytes) else last) == "1"
    return n, info


def _accepted(r: dict[str, Any]) -> bool:
    return bool(r.get("ok")) and ((r.get("answer") or {}).get("status", "Accepted") == "Accepted")


async def act(engine: Any, redis: Any, plan: str, info: dict[str, Any]) -> str | None:
    """Start or stop the charger to follow the plan (only in Auto, with the charger on World Assistant's OCPP)."""
    from app.modules.ocpp import protocol, server
    from app.modules.ocpp import store as ostore

    cid = info.get("charger")
    if not cid:
        return "not acted: the charger is not on World Assistant's OCPP"
    async with engine.connect() as conn:
        if (await ostore.settings(conn)).get("mode", "auto") != "auto":
            return None  # Off and On are the switch's own
        tid = await ostore.open_transaction(conn, cid)
    await redis.set(server.wanted_key(cid), "1" if plan == "charge" else "0", ex=86400)
    if plan == "charge" and tid is None:
        r = await server.ask(redis, cid, "RemoteStartTransaction", protocol.command_payload("RemoteStartTransaction", {}, transaction_id=None))
        return "started" if _accepted(r) else f"start refused: {r.get('error') or r.get('answer')}"
    if plan in ("wait", "full") and tid is not None:
        r = await server.ask(redis, cid, "RemoteStopTransaction", {"transactionId": tid})
        return "stopped" if _accepted(r) else f"stop refused: {r.get('error') or r.get('answer')}"
    return None


async def planner_tick(ctx: dict[str, Any]) -> Any:
    """Every five minutes: the decision, beside Home Assistant's, kept; and acted on when "act" is on."""
    return await tracked(ctx["engine"], "planner_tick", lambda: tick(ctx["engine"], ctx["redis"]))


async def tick(engine: Any, redis: Any) -> dict[str, Any]:
    n, info = await gather(engine, redis)
    plan, why = charge.decide(n)
    await redis.set("wa:planner:solar", "1" if charge.solar_spare(n) else "0", ex=900)
    acted = None
    if n.settings.get("act"):
        await redis.set(ACTING, "1", ex=900)
        try:
            acted = await act(engine, redis, plan, info)
        except Exception as exc:  # noqa: BLE001 - recorded
            acted = f"could not act: {exc}"[:200]
    inputs = {k: getattr(n, k) for k in ("mode", "status", "soc", "target", "cheap", "free", "battery_soc", "export_w", "car_w")} | info
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO planner_decision (plan, why, ha_plan, mode, acted, inputs) VALUES (:p, :w, :h, :m, :a, CAST(:i AS jsonb))"),
            {"p": plan, "w": why, "h": info.get("ha_plan"), "m": n.mode, "a": acted, "i": json.dumps(inputs, default=str)})  # fmt: skip
        await conn.execute(text("DELETE FROM planner_decision WHERE at < now() - interval '400 days'"))
    return {"plan": plan, "ha": info.get("ha_plan"), "acted": acted}


@router.get("/charging")
async def charging(request: Request, days: int = Query(3, ge=1, le=60)) -> dict[str, Any]:
    """The latest decision and why; how often it matched Home Assistant's plan; and each time they differed."""
    async with request.app.state.engine.connect() as conn:
        st = await settings(conn)
        rows = (await conn.execute(text(
            "SELECT at, plan, why, ha_plan, mode, acted, inputs FROM planner_decision WHERE at > now() - make_interval(days => :d) "
            "ORDER BY at"), {"d": days})).mappings().all()  # fmt: skip
    rows = [dict(r) for r in rows]
    judged = [(r, charge.same(r["plan"], r["ha_plan"])) for r in rows]
    agree = [x for _, x in judged if x is not None]
    differ, prev = [], None
    for r, x in judged:  # each run of disagreement once, at its start
        if x is False and (prev is None or prev[0]["plan"] != r["plan"] or prev[0]["ha_plan"] != r["ha_plan"] or prev[1] is not False):
            differ.append({"at": r["at"], "planner": r["plan"], "home_assistant": r["ha_plan"], "why": r["why"]})
        prev = (r, x)
    return {"act": st["act"], "latest": rows[-1] if rows else None, "checks": len(rows),
            "agreed_pct": round(100 * sum(agree) / len(agree)) if agree else None, "differences": differ[-50:]}  # fmt: skip


@router.get("/reviews")
async def reviews(request: Request) -> dict[str, Any]:
    """Each month (the last 12): what charging the car really cost and what it would have with the planner in charge,
    worked out on the 1st. Also the month so far."""
    from app.modules.planner import review as R

    tz = _tz()
    today = datetime.now(tz).date()
    async with request.app.state.engine.connect() as conn:
        past = await R.months(conn)
    so_far = await R.review(request.app.state.engine, today.replace(day=1), today + timedelta(days=1), tz)
    so_far["summary"] = R.summary(so_far).replace(datetime.now(tz).strftime("%B") + ":", "So far this month:", 1)
    return {"so_far": so_far, "months": past}


class SettingsIn(BaseModel):
    demand_statistic: str = Field("", max_length=200)
    demand_days: int = Field(90, ge=14, le=365)
    evening_from: int = Field(16, ge=0, le=23)
    evening_to: int = Field(19, ge=1, le=24)
    battery_entity: str = Field("", max_length=200)
    grid_entity: str = Field("", max_length=200)
    export_is_negative: bool = True
    solar_start_w: int = Field(3500, ge=500, le=20000)
    solar_stop_w: int = Field(2500, ge=0, le=20000)
    act: bool = False


@router.get("/settings")
async def get_settings_(request: Request) -> dict[str, Any]:
    async with request.app.state.engine.connect() as conn:
        return await settings(conn)


@router.put("/settings")
async def put_settings(request: Request, body: SettingsIn) -> dict[str, Any]:
    """The demand statistic and evening; the house battery and grid power entities for solar to spare (and which
    sign is export); and "act": World Assistant starts and stops the charger itself (Auto only)."""
    if body.evening_to <= body.evening_from:
        raise HTTPException(422, "the evening must end after it starts")
    for e in (body.battery_entity, body.grid_entity):
        if e and (e.count(".") != 1 or " " in e):
            raise HTTPException(422, f"{e!r} is not an entity id such as sensor.inverter_grid_power")
    st = body.model_dump()
    async with request.app.state.engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"), {"k": KEY, "v": json.dumps(st)})  # fmt: skip
    await request.app.state.redis.delete("wa:planner:demand")
    if not st["act"]:
        await request.app.state.redis.delete(ACTING)
    return st


async def demand_job(ctx: dict[str, Any]) -> Any:
    """Each morning: the demand fit worked out afresh (it also serves the page for six hours)."""
    async def work() -> dict[str, Any]:
        await ctx["redis"].delete("wa:planner:demand")
        r = await demand_report(ctx["engine"], ctx["redis"])
        return {"verdict": (r.get("fit") or {}).get("verdict"), "error": r.get("error")}

    return await tracked(ctx["engine"], "planner_demand", work)
