"""The charger API (/v1/ocpp): how each charger stands, its settings, starting and stopping, other commands, and its
record (events, messages, sessions). Plus the worker's watch: a phone notification when the charger restarts, reports
a fault, or stays disconnected."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth import require_token
from app.jobs import tracked
from app.modules.ocpp import protocol, server, store

router = APIRouter(prefix="/v1/ocpp", tags=["ocpp"], dependencies=[Depends(require_token)])
QUIET_S = 900  # disconnected this long is worth a notification


def address(host_header: str | None) -> str:
    """The address a charger on the home network uses: this server's home-network address and port 8010. Opened by
    a home-network address it is that one; opened another way (through Tailscale or a name) it cannot be known here,
    so it is left for you to fill in."""
    import ipaddress

    host = (host_header or "").split(",")[0].strip().rsplit(":", 1)[0].strip("[]")
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private and not ip.is_loopback and not str(ip).startswith("100."):
            return f"ws://{ip}:8010/ocpp/"
    except ValueError:
        pass
    return "ws://<this server's home network address>:8010/ocpp/"


@router.get("")
async def overview(request: Request) -> dict[str, Any]:
    """Every charger World Assistant has heard from, with its state, the last week's restarts, drops and faults, and
    the address to give a charger."""
    async with request.app.state.engine.connect() as conn:
        st = await store.settings(conn)
        rows = await store.chargers(conn)
        week = {r[0]: {"boots": r[1], "drops": r[2], "faults": r[3]} for r in (await conn.execute(text(
            "SELECT charger, count(*) FILTER (WHERE kind = 'boot'), count(*) FILTER (WHERE kind = 'disconnected'), "
            "count(*) FILTER (WHERE kind = 'fault') FROM ocpp_event WHERE at > now() - interval '7 days' GROUP BY charger"))).all()}  # fmt: skip
        sessions = {r[0]: dict(r._mapping) for r in (await conn.execute(text(
            "SELECT DISTINCT ON (charger) charger, transaction_id, started_at, stopped_at, meter_start_wh, last_meter_wh, "
            "meter_stop_wh, last_power_w, reason FROM ocpp_session ORDER BY charger, started_at DESC"))).all()}  # fmt: skip
    out = []
    for r in rows:
        held = bool(await request.app.state.redis.get(server.holder_key(r["id"])))
        out.append({**{k: r[k] for k in ("id", "status", "error_code", "connected", "heartbeat_s")},
                    "connected": bool(r["connected"]) and held, "first_seen": r["first_seen"], "last_seen": r["last_seen"],
                    "connected_at": r["connected_at"], "booted_at": r["booted_at"], "boot": r["boot"], "meter": r["meter"],
                    "status_info": r["status_info"], "config": r["config"], "week": week.get(r["id"], {"boots": 0, "drops": 0, "faults": 0}),
                    "session": sessions.get(r["id"])})  # fmt: skip
    return {"settings": st, "chargers": out, "address": address(request.headers.get("host"))}


class SettingsIn(BaseModel):
    chargers: list[str] = Field(default_factory=list, max_length=10)
    meter_every_s: int = Field(60, ge=15, le=900)
    publish_ha: bool = True


@router.put("/settings")
async def put_settings(request: Request, body: SettingsIn) -> dict[str, Any]:
    """Which chargers may connect (by id; none listed: any on the home network), how often to ask for meter readings
    while charging, and whether to publish the charger to Home Assistant."""
    ids = [c.strip() for c in body.chargers if c.strip()]
    if any(len(c) > 48 or not c.replace("-", "").replace("_", "").isalnum() for c in ids):
        raise HTTPException(422, "a charger id is letters, digits, - and _ (up to 48)")
    async with request.app.state.engine.connect() as conn:
        mode = (await store.settings(conn)).get("mode", "auto")
    st = {"chargers": ids, "meter_every_s": body.meter_every_s, "publish_ha": body.publish_ha, "mode": mode}
    async with request.app.state.engine.begin() as conn:
        await store.save_settings(conn, st)
    return st


class ModeIn(BaseModel):
    mode: str = Field(pattern="^(off|on|auto)$")


@router.put("/mode")
async def put_mode(request: Request, body: ModeIn) -> dict[str, Any]:
    """The master switch: off (the charger refuses to charge), on (charges whenever the car is plugged in) or auto
    (Home Assistant's charging plan decides). Kept, and applied to every connected charger now."""
    async with request.app.state.engine.begin() as conn:
        st = await store.settings(conn)
        await store.save_settings(conn, {**st, "mode": body.mode})
        ids = [r["id"] for r in await store.chargers(conn)]
    applied = {}
    for cid in ids:
        try:
            applied[cid] = await server.ask(request.app.state.redis, cid, "WA.Mode", {"mode": body.mode})
        except LookupError as exc:
            applied[cid] = {"ok": False, "error": str(exc)}
    return {"mode": body.mode, "applied": applied}


async def _send(request: Request, cid: str, action: str, args: dict[str, Any]) -> dict[str, Any]:
    tid = None
    if action == "RemoteStopTransaction" and "transaction_id" not in args:
        async with request.app.state.engine.connect() as conn:
            tid = await store.open_transaction(conn, cid)
    try:
        payload = protocol.command_payload(action, args, transaction_id=tid)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        out = await server.ask(request.app.state.redis, cid, action, payload)
    except LookupError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"action": action, "sent": payload, **out}


@router.post("/{cid}/start")
async def start(request: Request, cid: str) -> dict[str, Any]:
    """Start charging now (OCPP RemoteStartTransaction on connector 1)."""
    return await _send(request, cid, "RemoteStartTransaction", {})


@router.post("/{cid}/stop")
async def stop(request: Request, cid: str) -> dict[str, Any]:
    """Stop the charging session the charger last said it started."""
    return await _send(request, cid, "RemoteStopTransaction", {})


class CommandIn(BaseModel):
    action: str
    args: dict[str, Any] = Field(default_factory=dict)


@router.post("/{cid}/command")
async def command(request: Request, cid: str, body: CommandIn) -> dict[str, Any]:
    """Any of the commands in protocol.COMMANDS: Reset {type: Soft|Hard}, GetConfiguration {keys}, ChangeConfiguration
    {key, value}, TriggerMessage {message}, UnlockConnector, ChangeAvailability {type}, ClearCache."""
    return await _send(request, cid, body.action, body.args)


@router.get("/{cid}/events")
async def events(request: Request, cid: str, days: int = Query(7, ge=1, le=730), kind: str | None = None) -> dict[str, Any]:
    q = "SELECT at, kind, text, detail FROM ocpp_event WHERE charger = :c AND at > now() - make_interval(days => :d)"
    args: dict[str, Any] = {"c": cid, "d": days}
    if kind:
        q += " AND kind = :k"
        args["k"] = kind
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(q + " ORDER BY at DESC LIMIT 1000"), args)).all()
    return {"charger": cid, "events": [{"at": r[0], "kind": r[1], "text": r[2], "detail": r[3]} for r in rows]}


@router.get("/{cid}/messages")
async def messages(request: Request, cid: str, limit: int = Query(200, ge=1, le=2000), action: str | None = None) -> dict[str, Any]:
    """The newest messages either way, exactly as they went."""
    q = "SELECT id, at, direction, kind, unique_id, action, raw FROM ocpp_message WHERE charger = :c"
    args: dict[str, Any] = {"c": cid, "n": limit}
    if action:
        q += " AND action = :a"
        args["a"] = action
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(q + " ORDER BY id DESC LIMIT :n"), args)).all()
    return {"charger": cid, "messages": [{"id": r[0], "at": r[1], "direction": r[2], "kind": r[3], "unique_id": r[4],
                                          "action": r[5], "raw": r[6]} for r in rows]}  # fmt: skip


@router.get("/{cid}/sessions")
async def sessions(request: Request, cid: str, days: int = Query(60, ge=1, le=730)) -> dict[str, Any]:
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT transaction_id, connector, id_tag, started_at, stopped_at, meter_start_wh, coalesce(meter_stop_wh, last_meter_wh), "
            "reason FROM ocpp_session WHERE charger = :c AND started_at > now() - make_interval(days => :d) ORDER BY started_at DESC"),
            {"c": cid, "d": days})).all()  # fmt: skip
    return {"charger": cid, "sessions": [
        {"transaction": r[0], "connector": r[1], "id_tag": r[2], "started_at": r[3], "stopped_at": r[4], "reason": r[7],
         "kwh": round((r[6] - r[5]) / 1000, 2) if r[5] is not None and r[6] is not None else None} for r in rows]}  # fmt: skip


# --- the worker's watch ------------------------------------------------------------------------------------------


async def ocpp_watch(ctx: dict[str, Any]) -> Any:
    """Every 5 minutes: a notification for a restart or fault since last time, and for a charger that has been
    disconnected (or silent while connected) for 15 minutes. Each is sent once."""
    from app import notify

    engine = ctx["engine"]
    async with engine.connect() as conn:
        rows = await store.chargers(conn)
        new = (await conn.execute(text(
            "SELECT id, at, charger, kind, text FROM ocpp_event WHERE kind IN ('boot', 'fault') AND at > now() - interval '1 day' "
            "ORDER BY id"))).all()  # fmt: skip
    if not rows:
        async def nothing() -> dict[str, Any]:
            return {"not_set_up": "No charger has connected yet: point its OCPP 1.6J central system at "
                                  "ws://<this server>:8010/ocpp/<charger id> (see docs/ocpp.md)"}

        return await tracked(engine, "ocpp_watch", nothing)

    async def work() -> dict[str, Any]:
        sent = 0
        for r in new:
            res = await notify.send(engine, "home", f"ocpp:event:{r[0]}", "Charger restarted" if r[3] == "boot" else "Charger fault",
                                    f"{r[2]}: {r[4]}", tags=["electric_plug"], click="/charger")  # fmt: skip
            sent += bool(res.get("sent"))
        now = datetime.now(UTC)
        for c in rows:
            seen = c["last_seen"]
            hb = c["heartbeat_s"] or protocol.HEARTBEAT_S
            if seen is None:
                continue
            quiet = (now - seen).total_seconds()
            if quiet > max(QUIET_S, 2 * hb + 60):
                what = "disconnected" if not c["connected"] else "connected but silent"
                day = seen.strftime("%Y%m%d%H%M")
                res = await notify.send(engine, "home", f"ocpp:quiet:{c['id']}:{day}", f"Charger {what}",
                                        f"{c['id']} has said nothing since {seen.astimezone().strftime('%H:%M %d %b')}.",
                                        tags=["electric_plug", "warning"], click="/charger")  # fmt: skip
                sent += bool(res.get("sent"))
        return {"notified": sent}

    return await tracked(engine, "ocpp_watch", work)


async def ocpp_housekeep(ctx: dict[str, Any]) -> Any:
    async def work() -> dict[str, Any]:
        async with ctx["engine"].begin() as conn:
            return {"messages_deleted": await store.housekeep(conn)}

    return await tracked(ctx["engine"], "ocpp_housekeep", work)
