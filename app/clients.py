"""Clients: one token per thing that calls World Assistant (Home Assistant, your phone's Shortcuts, an AI assistant
through MCP, a script), each with only the access it needs, and each revocable on its own. The main token
(api_token) still works for everything; the web app on your home network or tailnet still needs no token.

Access:
- read: GET only.
- assistant: read, plus asking the assistant (/v1/ask, /mcp).
- full: everything.
A client can also be limited to some paths ("/v1/ocpp", "/v1/energy").

Only a SHA-256 hash of each token is kept; the token is shown once, when it is made. The audit log records every
change made through the API (anything but GET), and every refused token, with who it was.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth import require_token

log = logging.getLogger("wa.clients")
ACCESS = ("read", "assistant", "full")
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
ASSISTANT_PATHS = ("/v1/ask", "/mcp")
CACHE_S = 30
TOUCH_S = 300
KEEP_AUDIT_DAYS = 400


@dataclass(frozen=True)
class Client:
    id: int
    name: str
    access: str
    paths: tuple[str, ...]


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def allowed(c: Client, method: str, path: str) -> str | None:
    """None when the client may make this request, else why not."""
    if c.paths and not any(path == p or path.startswith(p.rstrip("/") + "/") for p in c.paths):
        return f"{c.name} may use only {', '.join(c.paths)}"
    if c.access == "full" or method.upper() in READ_METHODS:
        return None
    if c.access == "assistant" and any(path == p or path.startswith(p + "/") for p in ASSISTANT_PATHS):
        return None
    return f"{c.name} may only read" + (" and ask the assistant" if c.access == "assistant" else "")


_cache: dict[str, tuple[float, Client | None]] = {}
_touched: dict[int, float] = {}


async def lookup(engine: Any, token: str) -> Client | None:
    """The client a token belongs to (cached briefly), or None."""
    if not token or len(token) > 200:
        return None
    h = digest(token)
    hit = _cache.get(h)
    if hit and time.monotonic() - hit[0] < CACHE_S:
        c = hit[1]
    else:
        try:
            async with engine.connect() as conn:
                row = (await conn.execute(text(
                    "SELECT id, name, access, paths FROM api_client WHERE token_hash = :h AND revoked_at IS NULL"), {"h": h})).first()  # fmt: skip
        except Exception:  # noqa: BLE001 - no table yet (before the migration): no clients
            row = None
        c = Client(row[0], row[1], row[2], tuple(row[3] or ())) if row else None
        _cache[h] = (time.monotonic(), c)
    if c is not None and time.monotonic() - _touched.get(c.id, 0) > TOUCH_S:
        _touched[c.id] = time.monotonic()
        asyncio.get_running_loop().create_task(_touch(engine, c.id))
    return c


async def _touch(engine: Any, cid: int) -> None:
    with contextlib.suppress(Exception):
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE api_client SET last_used_at = now() WHERE id = :i"), {"i": cid})


def forget() -> None:
    _cache.clear()


# --- the audit log ----------------------------------------------------------------------------------------------


class Audit:
    """ASGI middleware: after each change through the API (anything but GET) and each refused token, a row in the
    audit log saying who, what and how it went. Written after the answer has been sent."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not (scope["path"].startswith("/v1/") or scope["path"].startswith("/mcp")):
            return await self.app(scope, receive, send)
        status = {"code": 500}

        async def keep(message: Any) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, keep)
        finally:
            method, code = scope["method"], status["code"]
            if method not in READ_METHODS or code in (401, 403):
                state = scope.get("state") or {}
                who = state.get("client") or ("no token" if code == 401 else "unknown")
                app = scope.get("app")
                engine = getattr(getattr(app, "state", None), "engine", None)
                if engine is not None and scope["path"] not in ("/v1/health/watch",):
                    from app.localnet import _peer

                    try:
                        from starlette.requests import HTTPConnection

                        peer = str(_peer(HTTPConnection(scope)) or "")
                    except Exception:  # noqa: BLE001
                        peer = ""
                    # after the answer has gone: the caller does not wait on this
                    await _write(engine, who, method, scope["path"][:300], code, peer)


async def _write(engine: Any, who: str, method: str, path: str, code: int, peer: str) -> None:
    with contextlib.suppress(Exception):
        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO audit_log (client, method, path, status, peer) VALUES (:c, :m, :p, :s, :peer)"),
                               {"c": who[:80], "m": method, "p": path, "s": code, "peer": peer[:60] or None})


# --- managing clients (/v1/clients: the main token, the home network or a full client) -------------------------


def _full_only(request: Request) -> None:
    who = getattr(request.state, "client_access", "full")
    if who != "full":
        raise HTTPException(403, "only the main token, the home network or a full client manages clients")


router = APIRouter(prefix="/v1", tags=["clients"], dependencies=[Depends(require_token), Depends(_full_only)])


@router.get("/clients")
async def list_clients(request: Request) -> dict[str, Any]:
    """Every client: name, access, paths, the token's last four characters, made, last used, revoked."""
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT id, name, hint, access, paths, created_at, last_used_at, revoked_at FROM api_client ORDER BY revoked_at NULLS FIRST, name"))).mappings().all()  # fmt: skip
    return {"clients": [dict(r) for r in rows], "access": list(ACCESS)}


class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    access: str = Field("read", pattern="^(read|assistant|full)$")
    paths: list[str] = Field(default_factory=list, max_length=20)


@router.post("/clients")
async def make_client(request: Request, body: ClientIn) -> dict[str, Any]:
    """A new client and its token. The token is shown this once: copy it into the client now."""
    paths = [p.strip() for p in body.paths if p.strip()]
    if any(not re.fullmatch(r"/(v1|mcp)[A-Za-z0-9/_-]*", p) for p in paths):
        raise HTTPException(422, "a path starts /v1 or /mcp, e.g. /v1/ocpp")
    token = "wac_" + secrets.token_urlsafe(32)
    try:
        async with request.app.state.engine.begin() as conn:
            cid = (await conn.execute(text(
                "INSERT INTO api_client (name, token_hash, hint, access, paths) VALUES (:n, :h, :hint, :a, CAST(:p AS jsonb)) RETURNING id"),
                {"n": body.name.strip(), "h": digest(token), "hint": token[-4:], "a": body.access, "p": json.dumps(paths)})).scalar_one()  # fmt: skip
    except Exception as exc:
        raise HTTPException(409, f"there is already a client called {body.name.strip()!r}") from exc
    forget()
    return {"id": cid, "name": body.name.strip(), "access": body.access, "paths": paths, "token": token,
            "note": "Shown once: copy it now. World Assistant keeps only a hash of it."}  # fmt: skip


@router.post("/clients/{cid}/revoke")
async def revoke(request: Request, cid: int) -> dict[str, Any]:
    """Stop a client's token working, now. The client stays listed, with when it was revoked."""
    async with request.app.state.engine.begin() as conn:
        n = (await conn.execute(text("UPDATE api_client SET revoked_at = now() WHERE id = :i AND revoked_at IS NULL"), {"i": cid})).rowcount
    forget()
    if not n:
        raise HTTPException(404, "no such client, or already revoked")
    return {"revoked": cid}


@router.get("/audit")
async def audit(request: Request, days: int = Query(7, ge=1, le=400), client: str | None = None,
                limit: int = Query(500, ge=1, le=5000)) -> dict[str, Any]:
    """Changes made through the API, and refused tokens, newest first."""
    q = "SELECT at, client, method, path, status, peer FROM audit_log WHERE at > now() - make_interval(days => :d)"
    args: dict[str, Any] = {"d": days, "n": limit}
    if client:
        q += " AND client = :c"
        args["c"] = client
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(q + " ORDER BY at DESC LIMIT :n"), args)).mappings().all()
    return {"entries": [dict(r) for r in rows]}


async def audit_housekeep(ctx: dict[str, Any]) -> Any:
    from app.jobs import tracked

    async def work() -> dict[str, Any]:
        async with ctx["engine"].begin() as conn:
            n = (await conn.execute(text("DELETE FROM audit_log WHERE at < now() - make_interval(days => :d)"), {"d": KEEP_AUDIT_DAYS})).rowcount
        return {"deleted": n}

    return await tracked(ctx["engine"], "audit_housekeep", work)
