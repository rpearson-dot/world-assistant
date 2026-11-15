"""Your network's devices (/v1/network, Your network → Network): every device seen by the UniFi gateway log or
Pi-hole, and what each did (joining and leaving, DNS lookups, firewall decisions). The two sources are modules of
their own (unifi, pihole: app/modules/network/pihole_api.py); this is what they share. Nothing goes to Home Assistant.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth import require_token
from app.config import get_settings
from app.jobs import tracked
from app.modules.network import store

router = APIRouter(prefix="/v1/network", tags=["network"], dependencies=[Depends(require_token)])


def _label(d: dict[str, Any]) -> str:
    return d.get("alias") or d.get("name") or d.get("ip") or d["mac"]


@router.get("/devices")
async def devices(request: Request) -> dict[str, Any]:
    """Every device, newest first: its name, address, when first and last seen, and today's lookups, blocks and
    firewall decisions (its Pi-hole groups: /v1/pihole/device-groups)."""
    async with request.app.state.engine.connect() as conn:
        rows = [dict(r) for r in (await conn.execute(text(
            "SELECT d.*, coalesce(sum(n) FILTER (WHERE kind = 'dns' AND action = 'allow'), 0) AS lookups, "
            "coalesce(sum(n) FILTER (WHERE kind = 'dns' AND action = 'block'), 0) AS blocked, "
            "coalesce(sum(n) FILTER (WHERE kind = 'firewall' AND action = 'block'), 0) AS firewall_blocks "
            "FROM net_device d LEFT JOIN net_day y ON y.mac = d.mac AND y.day = current_date GROUP BY d.mac ORDER BY d.last_seen DESC"))).mappings().all()]  # fmt: skip
    from app.modules import gate

    for d in rows:
        d["label"] = _label(d)
    return {"devices": rows, "pihole": await gate.is_enabled("pihole", request.app.state.engine)}


@router.get("/devices/{mac}")
async def device(request: Request, mac: str, hours: int = Query(24, ge=1, le=24 * 30)) -> dict[str, Any]:
    """One device's timeline (newest first) and its most looked-up and most blocked domains."""
    async with request.app.state.engine.connect() as conn:
        d = (await conn.execute(text("SELECT * FROM net_device WHERE mac = :m"), {"m": mac.lower()})).mappings().first()
        if d is None:
            raise HTTPException(404, "no such device")
        ev = (await conn.execute(text(
            "SELECT at, source, kind, action, dest, port, proto, detail FROM net_event WHERE mac = :m AND at > now() - make_interval(hours => :h) "
            "ORDER BY at DESC LIMIT 500"), {"m": mac.lower(), "h": hours})).mappings().all()  # fmt: skip
        top = (await conn.execute(text(
            "SELECT dest, action, count(*) AS n FROM net_event WHERE mac = :m AND kind = 'dns' AND at > now() - make_interval(hours => :h) "
            "GROUP BY dest, action ORDER BY n DESC LIMIT 40"), {"m": mac.lower(), "h": hours})).mappings().all()  # fmt: skip
        blocks = (await conn.execute(text("SELECT domain, added_at FROM net_block WHERE mac = :m ORDER BY domain"), {"m": mac.lower()})).mappings().all()
    return {"device": {**dict(d), "label": _label(dict(d))}, "events": [dict(e) for e in ev], "top": [dict(t) for t in top],
            "added_blocks": [dict(b) for b in blocks]}  # fmt: skip


@router.get("/events")
async def events(request: Request, kind: str | None = None, action: str | None = None, hours: int = Query(1, ge=1, le=168),
                 limit: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:  # fmt: skip
    """The newest events: kind dns, firewall, client, dhcp or system; action allow, block, join, leave, lease."""
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT e.at, e.source, e.kind, e.action, e.mac, e.ip, e.dest, e.port, e.proto, e.detail, coalesce(d.alias, d.name) AS device "
            "FROM net_event e LEFT JOIN net_device d ON d.mac = e.mac WHERE e.at > now() - make_interval(hours => :h) "
            "AND (CAST(:k AS text) IS NULL OR e.kind = :k) AND (CAST(:a AS text) IS NULL OR e.action = :a) ORDER BY e.at DESC LIMIT :l"),
            {"h": hours, "k": kind, "a": action, "l": limit})).mappings().all()  # fmt: skip
    return {"events": [dict(r) for r in rows]}


class AliasIn(BaseModel):
    alias: str = Field("", max_length=60)


@router.put("/devices/{mac}/alias")
async def alias(request: Request, mac: str, body: AliasIn) -> dict[str, Any]:
    async with request.app.state.engine.begin() as conn:
        n = (await conn.execute(text("UPDATE net_device SET alias = nullif(:a, '') WHERE mac = :m"), {"a": body.alias.strip(), "m": mac.lower()})).rowcount
    if not n:
        raise HTTPException(404, "no such device")
    return {"mac": mac.lower(), "alias": body.alias.strip() or None}


class SettingsIn(BaseModel):
    keep_days: int = Field(30, ge=1, le=365)
    notify_new: bool = True


@router.get("/settings")
async def get_settings_(request: Request) -> dict[str, Any]:
    async with request.app.state.engine.connect() as conn:
        st = await store.settings(conn)
    port = get_settings().syslog_port
    return {**st, "syslog_port": port}


@router.put("/settings")
async def put_settings(request: Request, body: SettingsIn) -> dict[str, Any]:
    """How long events are kept, and the new-device notification."""
    async with request.app.state.engine.begin() as conn:
        st = {**await store.settings(conn), "keep_days": body.keep_days, "notify_new": body.notify_new}
        await store.save_settings(conn, st)
    return st


# --- jobs ------------------------------------------------------------------------------------------------------------


async def network_housekeep(ctx: dict[str, Any]) -> Any:
    async def work() -> dict[str, Any]:
        async with ctx["engine"].begin() as conn:
            return await store.housekeep(conn)

    return await tracked(ctx["engine"], "network_housekeep", work)
