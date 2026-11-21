"""The Logs API (/v1/logs): the raw diagnostic feeds, read straight from their tables. This module is core and never
switched off, so the logs stay readable even when the feature they belong to has been turned off — which is often
exactly when you need them, having turned a module off over a fault. Reading only; nothing here changes anything."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text

from app.auth import require_token
from app.modules.ocpp import syslog

router = APIRouter(prefix="/v1/logs", tags=["logs"], dependencies=[Depends(require_token)])


@router.get("/chargers")
async def chargers(request: Request) -> dict[str, Any]:
    """Every charger that has ever connected, newest first — for the picker on the charger and OCPP log pages."""
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT id, last_seen, connected FROM ocpp_charger ORDER BY last_seen DESC NULLS LAST"))).mappings().all()
    return {"chargers": [{"id": r["id"], "last_seen": r["last_seen"], "connected": r["connected"]} for r in rows]}


@router.get("/charger/{cid}")
async def charger(request: Request, cid: str, limit: int = Query(200, ge=1, le=1000),
                  before: int | None = Query(None, description="only lines older than this id (for paging)"),
                  severity: int | None = Query(None, ge=0, le=7, description="only this urgent or more (0 worst, 7 debug)")) -> dict[str, Any]:
    """The charger's own syslog (ocpp_log), newest first. Paged by `before` (the oldest id you already have)."""
    q = "SELECT id, at, facility, severity, message, raw, from_ip FROM ocpp_log WHERE charger = :c"
    args: dict[str, Any] = {"c": cid, "n": limit}
    if before is not None:
        q += " AND id < :b"
        args["b"] = before
    if severity is not None:
        q += " AND severity IS NOT NULL AND severity <= :s"
        args["s"] = severity
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(q + " ORDER BY id DESC LIMIT :n"), args)).all()
    return {"charger": cid, "lines": [{"id": r[0], "at": r[1], "facility": r[2], "severity": r[3],
                                       "severity_word": syslog.severity_word(r[3]), "message": r[4], "raw": r[5],
                                       "from": r[6]} for r in rows]}  # fmt: skip


@router.get("/ocpp/{cid}")
async def ocpp(request: Request, cid: str, limit: int = Query(200, ge=1, le=2000),
               before: int | None = Query(None, description="only frames older than this id (for paging)"),
               action: str | None = None) -> dict[str, Any]:
    """The OCPP protocol frames either way (ocpp_message), newest first, exactly as they went."""
    q = "SELECT id, at, direction, kind, unique_id, action, raw FROM ocpp_message WHERE charger = :c"
    args: dict[str, Any] = {"c": cid, "n": limit}
    if before is not None:
        q += " AND id < :b"
        args["b"] = before
    if action:
        q += " AND action = :a"
        args["a"] = action
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(q + " ORDER BY id DESC LIMIT :n"), args)).all()
    return {"charger": cid, "messages": [{"id": r[0], "at": r[1], "direction": r[2], "kind": r[3], "unique_id": r[4],
                                          "action": r[5], "raw": r[6]} for r in rows]}  # fmt: skip


@router.get("/network")
async def network(request: Request, kind: str | None = None, action: str | None = None,
                  hours: int = Query(6, ge=1, le=168), limit: int = Query(300, ge=1, le=2000),
                  before: int | None = Query(None, description="only events older than this id (for paging)")) -> dict[str, Any]:
    """The network event stream (net_event) — DNS, firewall, clients joining and leaving, DHCP — newest first."""
    q = ("SELECT e.id, e.at, e.source, e.kind, e.action, e.mac, e.ip, e.dest, e.port, e.proto, e.detail, "
         "coalesce(d.alias, d.name) AS device FROM net_event e LEFT JOIN net_device d ON d.mac = e.mac "
         "WHERE e.at > now() - make_interval(hours => :h)")
    args: dict[str, Any] = {"h": hours, "n": limit}
    if kind:
        q += " AND e.kind = :k"
        args["k"] = kind
    if action:
        q += " AND e.action = :a"
        args["a"] = action
    if before is not None:
        q += " AND e.id < :b"
        args["b"] = before
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(q + " ORDER BY e.id DESC LIMIT :n"), args)).mappings().all()
    return {"events": [dict(r) for r in rows]}
