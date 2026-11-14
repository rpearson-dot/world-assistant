"""Air conditioning (/v1/airstage): your Fujitsu Airstage units on the home network, read every two minutes, changed
from the page, the assistant or Home Assistant. The units are set up here by name, address and device id."""

from __future__ import annotations

import ipaddress
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth import require_token
from app.jobs import tracked
from app.modules.airstage import ha, unit

router = APIRouter(prefix="/v1/airstage", tags=["airstage"], dependencies=[Depends(require_token)])
KEY = "airstage.units"
QUIET_MIN = 30


async def units(conn: Any) -> list[dict[str, str]]:
    v = (await conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": KEY})).scalar()
    v = json.loads(v) if isinstance(v, str) else v
    return list(v or [])


async def latest(conn: Any) -> dict[str, dict[str, Any]]:
    rows = (await conn.execute(text(
        "SELECT DISTINCT ON (unit) unit, at, ok, error, raw FROM airstage_reading ORDER BY unit, at DESC"))).all()  # fmt: skip
    good = {r[0]: (r[1], r[4]) for r in (await conn.execute(text(
        "SELECT DISTINCT ON (unit) unit, at, raw FROM airstage_reading WHERE ok ORDER BY unit, at DESC"))).all()}  # fmt: skip
    return {r[0]: {"at": r[1], "ok": r[2], "error": r[3], "good_at": good.get(r[0], (None, None))[0],
                   "state": unit.state(good[r[0]][1]) if r[0] in good else None} for r in rows}  # fmt: skip


@router.get("")
async def overview(request: Request) -> dict[str, Any]:
    """Every unit: on or off, mode, temperatures, fan, extras, error code, filter, and when it last answered."""
    async with request.app.state.engine.connect() as conn:
        us, last = await units(conn), await latest(conn)
    return {"units": [{**u, **last.get(u["id"], {"at": None, "ok": None, "error": None, "good_at": None, "state": None})} for u in us]}


class UnitIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    ip: str = Field(max_length=45)
    device_id: str = Field(min_length=4, max_length=32)


@router.put("/units")
async def put_units(request: Request, body: list[UnitIn]) -> dict[str, Any]:
    """The units: a name, the address on your home network, and the device id (the Airstage app's unit details, or
    the label on the unit's Wi-Fi adapter). Only private addresses: a unit is never asked about over the internet."""
    out = []
    for u in body[:12]:
        try:
            ip = ipaddress.ip_address(u.ip.strip())
        except ValueError as exc:
            raise HTTPException(422, f"{u.name}: the address is an IP address such as 10.0.2.150") from exc
        if not ip.is_private or ip.is_loopback:
            raise HTTPException(422, f"{u.name}: only an address on your home network")
        if not re.fullmatch(r"[A-Za-z0-9:_-]+", u.device_id):
            raise HTTPException(422, f"{u.name}: the device id is letters and digits")
        uid = re.sub(r"[^a-z0-9]+", "_", u.name.lower()).strip("_")[:30] or "unit"
        if any(x["id"] == uid for x in out):
            raise HTTPException(422, f"two units called {u.name}")
        out.append({"id": uid, "name": u.name.strip(), "ip": str(ip), "device_id": u.device_id.replace(":", "").strip()})
    async with request.app.state.engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"), {"k": KEY, "v": json.dumps(out)})  # fmt: skip
    return {"units": out}


class SetIn(BaseModel):
    power: bool | None = None
    mode: str | None = None
    target_c: float | None = Field(None, ge=10, le=32)
    fan: str | None = None
    swing_vertical: bool | None = None
    swing_horizontal: bool | None = None
    economy: bool | None = None
    powerful: bool | None = None
    low_noise: bool | None = None
    minimum_heat: bool | None = None


async def change(engine: Any, uid: str, want: dict[str, Any], who: str = "page") -> dict[str, Any]:
    async with engine.connect() as conn:
        u = next((x for x in await units(conn) if x["id"] == uid), None)
        cur = (await latest(conn)).get(uid, {}).get("state") or {}
    if u is None:
        raise LookupError("no such unit")
    params = unit.changes(want, cur)  # ValueError for a change the unit would refuse
    async with httpx.AsyncClient() as client:
        await unit.write(client, u["ip"], u["device_id"], params)
        raw = await unit.read(client, u["ip"], u["device_id"])
    await _keep(engine, uid, raw, None)
    await ha.publish(engine, [u], {uid: unit.state(raw)})
    return {"unit": uid, "set": dict(params), "by": who, "state": unit.state(raw)}


@router.post("/{uid}/set")
async def set_unit(request: Request, uid: str, body: SetIn) -> dict[str, Any]:
    """Change a unit: any of power, mode (auto, cool, dry, fan_only, heat), target_c (half degrees), fan (auto,
    quiet, low, medium_low, medium, medium_high, high), swing and the extras. Read back afterwards."""
    want = {k: v for k, v in body.model_dump().items() if v is not None}
    if not want:
        raise HTTPException(422, "nothing to change")
    try:
        return await change(request.app.state.engine, uid, want)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except unit.UnitError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/{uid}/history")
async def history(request: Request, uid: str, hours: int = Query(24, ge=1, le=24 * 60)) -> dict[str, Any]:
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT at, ok, power, mode, target_c, indoor_c, outdoor_c, fan, energy FROM airstage_reading "
            "WHERE unit = :u AND at > now() - make_interval(hours => :h) ORDER BY at"), {"u": uid, "h": hours})).mappings().all()  # fmt: skip
    return {"unit": uid, "readings": [dict(r) for r in rows]}


async def _keep(engine: Any, uid: str, raw: dict[str, Any] | None, error: str | None) -> None:
    s = unit.state(raw) if raw else {}
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO airstage_reading (unit, ok, error, power, mode, target_c, indoor_c, outdoor_c, fan, energy, error_code, raw) "
            "VALUES (:u, :ok, :e, :p, :m, :t, :i, :o, :f, :en, :ec, CAST(:raw AS jsonb))"),
            {"u": uid, "ok": raw is not None, "e": error, "p": s.get("power"), "m": s.get("mode"), "t": s.get("target_c"),
             "i": s.get("indoor_c"), "o": s.get("outdoor_c"), "f": s.get("fan"), "en": s.get("energy"),
             "ec": s.get("error_code"), "raw": json.dumps(raw or {})})  # fmt: skip


async def airstage_poll(ctx: dict[str, Any]) -> Any:
    """Every two minutes: read each unit, keep the reading (or the failure), publish to Home Assistant, and send a
    notification for an error code, a filter to clean, or a unit that has not answered for half an hour."""
    from app import notify

    engine = ctx["engine"]
    async with engine.connect() as conn:
        us = await units(conn)
    if not us:
        async def nothing() -> dict[str, Any]:
            return {"not_set_up": "No units added yet: add each one's address and device id on the Air conditioning page"}

        return await tracked(engine, "airstage_poll", nothing)

    async def work() -> dict[str, Any]:
        states, failed = {}, 0
        async with httpx.AsyncClient() as client:
            for u in us:
                try:
                    raw = await unit.read(client, u["ip"], u["device_id"])
                except unit.UnitError as exc:
                    failed += 1
                    await _keep(engine, u["id"], None, str(exc))
                    continue
                await _keep(engine, u["id"], raw, None)
                states[u["id"]] = unit.state(raw)
        await ha.publish(engine, us, states)
        async with engine.connect() as conn:
            last = await latest(conn)
        now = datetime.now(UTC)
        for u in us:
            s, info = states.get(u["id"]), last.get(u["id"], {})
            if s and s["error_code"]:
                await notify.send(engine, "home", f"airstage:err:{u['id']}:{s['error_code']}:{now:%Y%m%d}", f"{u['name']}: error",
                                  f"The air conditioner reports error code {s['error_code']}.", tags=["warning"], click="/aircon")  # fmt: skip
            if s and s["filter_due"]:
                await notify.send(engine, "home", f"airstage:filter:{u['id']}:{now:%Y%m}", f"{u['name']}: clean the filter",
                                  "The unit's filter sign is on.", tags=["broom"], click="/aircon")  # fmt: skip
            good = info.get("good_at")
            if not s and (good is None or now - good > timedelta(minutes=QUIET_MIN)):
                stamp = f"{good:%Y%m%d%H%M}" if good else "never"
                await notify.send(engine, "home", f"airstage:quiet:{u['id']}:{stamp}", f"{u['name']} not answering",
                                  f"The air conditioner has not answered since {good.astimezone():%H:%M %d %b}." if good else
                                  "The air conditioner has never answered: check its address and device id.", click="/aircon")  # fmt: skip
        return {"units": len(us), "failed": failed}

    return await tracked(engine, "airstage_poll", work)


async def airstage_housekeep(ctx: dict[str, Any]) -> Any:
    async def work() -> dict[str, Any]:
        async with ctx["engine"].begin() as conn:
            n = (await conn.execute(text("DELETE FROM airstage_reading WHERE at < now() - interval '730 days'"))).rowcount
        return {"deleted": n}

    return await tracked(ctx["engine"], "airstage_housekeep", work)
