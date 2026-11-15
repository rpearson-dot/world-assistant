"""Air conditioning (/v1/airstage): your Fujitsu Airstage units on the home network, read every two minutes, changed
from the page, the assistant or Home Assistant. The units are set up here by name, address and device id."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
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

log = logging.getLogger("wa.airstage")
router = APIRouter(prefix="/v1/airstage", tags=["airstage"], dependencies=[Depends(require_token)])
KEY = "airstage.units"
QUIET_MIN = 30


async def units(conn: Any) -> list[dict[str, str]]:
    v = (await conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": KEY})).scalar()
    v = json.loads(v) if isinstance(v, str) else v
    return list(v or [])


def _state(uid: str, raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    """A unit's state from a kept reading, or why it could not be read (one odd reading never stops the page)."""
    try:
        return unit.state(raw), None
    except Exception as exc:
        log.exception("air conditioning: unit %s sent a reading that could not be read: %r", uid, raw)
        return None, f"its answer could not be read ({type(exc).__name__}: {exc})"[:200]


async def latest(conn: Any) -> dict[str, dict[str, Any]]:
    rows = (await conn.execute(text(
        "SELECT DISTINCT ON (unit) unit, at, ok, error, raw FROM airstage_reading ORDER BY unit, at DESC"))).all()  # fmt: skip
    good = {r[0]: (r[1], r[2]) for r in (await conn.execute(text(
        "SELECT DISTINCT ON (unit) unit, at, raw FROM airstage_reading WHERE ok ORDER BY unit, at DESC"))).all()}  # fmt: skip
    out = {}
    for r in rows:
        st, why = _state(r[0], good[r[0]][1]) if r[0] in good else (None, None)
        out[r[0]] = {"at": r[1], "ok": r[2] and why is None, "error": why or r[3],
                     "good_at": good.get(r[0], (None, None))[0], "state": st}  # fmt: skip
    return out


@router.get("")
async def overview(request: Request) -> dict[str, Any]:
    """Every unit: on or off, mode, temperatures, fan, extras, error code, filter, and when it last answered."""
    async with request.app.state.engine.connect() as conn:
        us, last = await units(conn), await latest(conn)
    return {"units": [{**u, **last.get(u["id"], {"at": None, "ok": None, "error": None, "good_at": None, "state": None})} for u in us]}


class UnitIn(BaseModel):
    id: str | None = Field(default=None, max_length=32)  # kept when editing a unit, so its history and HA entity stay
    name: str = Field(min_length=1, max_length=40)
    ip: str = Field(max_length=45)
    device_id: str = Field(min_length=4, max_length=32)


def _clean(u: UnitIn, taken: set[str]) -> dict[str, str]:
    """One unit checked: a private address, a plain device id, and a stable id (kept if given, else minted) that does
    not change when the unit is renamed."""
    import secrets

    try:
        ip = ipaddress.ip_address(u.ip.strip())
    except ValueError as exc:
        raise HTTPException(422, f"{u.name}: the address is an IP address such as 10.0.2.150") from exc
    if not ip.is_private or ip.is_loopback:
        raise HTTPException(422, f"{u.name}: only an address on your home network")
    if not re.fullmatch(r"[A-Za-z0-9:_-]+", u.device_id):
        raise HTTPException(422, f"{u.name}: the device id is letters and digits")
    uid = u.id if u.id and re.fullmatch(r"[a-z0-9_]{1,32}", u.id) else ""
    while not uid or uid in taken:
        uid = "u" + secrets.token_hex(3)
    taken.add(uid)
    return {"id": uid, "name": u.name.strip(), "ip": str(ip), "device_id": u.device_id.replace(":", "").strip()}


async def _save(conn: Any, out: list[dict[str, str]]) -> None:
    await conn.execute(text(
        "INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"), {"k": KEY, "v": json.dumps(out)})  # fmt: skip


@router.put("/units")
async def put_units(request: Request, body: list[UnitIn]) -> dict[str, Any]:
    """The whole set of units: each with a name, the address on your home network, and the device id (the Airstage
    app's unit details, or the label on the unit's Wi-Fi adapter). A unit keeps its id when renamed (send it back),
    so its history and its Home Assistant entity stay. Only private addresses: a unit is never asked over the
    internet."""
    taken: set[str] = set()
    names: set[str] = set()
    out = []
    for u in body[:12]:
        if u.name.strip().lower() in names:
            raise HTTPException(422, f"two units called {u.name}")
        names.add(u.name.strip().lower())
        out.append(_clean(u, taken))
    async with request.app.state.engine.begin() as conn:
        await _save(conn, out)
    return {"units": out}


@router.post("/units")
async def add_unit(request: Request, body: UnitIn) -> dict[str, Any]:
    """Add one air conditioner. It is read within two minutes."""
    async with request.app.state.engine.begin() as conn:
        us = await units(conn)
        if len(us) >= 12:
            raise HTTPException(422, "up to 12 units")
        if any(x["name"].strip().lower() == body.name.strip().lower() for x in us):
            raise HTTPException(422, f"a unit is already called {body.name}")
        u = _clean(UnitIn(name=body.name, ip=body.ip, device_id=body.device_id), {x["id"] for x in us})
        await _save(conn, [*us, u])
    return {"unit": u}


@router.patch("/units/{uid}")
async def edit_unit(request: Request, uid: str, body: UnitIn) -> dict[str, Any]:
    """Rename one air conditioner or change its address or device id. Its id does not change, so its history and its
    Home Assistant entity stay."""
    async with request.app.state.engine.begin() as conn:
        us = await units(conn)
        if not any(x["id"] == uid for x in us):
            raise HTTPException(404, "no such unit")
        if any(x["id"] != uid and x["name"].strip().lower() == body.name.strip().lower() for x in us):
            raise HTTPException(422, f"another unit is called {body.name}")
        u = _clean(UnitIn(id=uid, name=body.name, ip=body.ip, device_id=body.device_id), {x["id"] for x in us if x["id"] != uid})
        await _save(conn, [u if x["id"] == uid else x for x in us])
    return {"unit": u}


@router.delete("/units/{uid}")
async def remove_unit(request: Request, uid: str) -> dict[str, Any]:
    """Remove one air conditioner. Its past readings are left and age out on their own."""
    async with request.app.state.engine.begin() as conn:
        us = await units(conn)
        kept = [x for x in us if x["id"] != uid]
        if len(kept) == len(us):
            raise HTTPException(404, "no such unit")
        await _save(conn, kept)
    with contextlib.suppress(Exception):
        await ha.retract(request.app.state.engine, uid)
    return {"units": kept}


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


@router.post("/set-all")
async def set_all(request: Request, body: SetIn) -> dict[str, Any]:
    """Apply the same change to every unit at once (all to one temperature, all off, all to a mode). Each unit is
    changed on its own: one that refuses or does not answer is reported, the rest still change."""
    want = {k: v for k, v in body.model_dump().items() if v is not None}
    if not want:
        raise HTTPException(422, "nothing to change")
    engine = request.app.state.engine
    async with engine.connect() as conn:
        us = await units(conn)

    async def one(u: dict[str, str]) -> dict[str, Any]:
        try:
            await change(engine, u["id"], dict(want))
            return {"id": u["id"], "name": u["name"], "ok": True}
        except (LookupError, ValueError, unit.UnitError) as exc:
            return {"id": u["id"], "name": u["name"], "ok": False, "error": str(exc)}

    results = list(await asyncio.gather(*(one(u) for u in us)))
    return {"set": want, "results": results, "changed": sum(1 for r in results if r["ok"])}


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
