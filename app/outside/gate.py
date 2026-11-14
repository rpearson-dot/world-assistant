"""The module gate: the only part of World Assistant an outside module can reach (the wa-modgate service, on the
wa-modules network, which has no other way out).

Two doors, each needing the module's own token:

- **The module API** (port 8090, `/api/...`): what you agreed it may have, and nothing else. Its own settings; your
  home's location at the precision you chose; your places and vehicles if you ticked them; the Home Assistant
  states it declared; its own Home Assistant entities (named wa_x_<id>_…, remembered so removal can take them back);
  notifications. Each piece of personal data given is written to its history, and requests are limited per minute.
- **The way out** (port 8091, an HTTP proxy): only the outside hosts it declared and the home-network devices you
  gave addresses for. Everything else is refused, including a declared name that resolves to a private address.

It also serves the module's own pages to World Assistant (`/pages/<id>/...`, from the module's container), which
shows them in a sandboxed frame.

Run as `python -m app.outside.gate`.
"""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import hashlib
import ipaddress
import json
import logging
import socket
import time
from collections import defaultdict
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

log = logging.getLogger("wa.gate")
app = FastAPI(title="World Assistant module gate", docs_url=None, redoc_url=None, openapi_url=None)
_hits: dict[str, list[float]] = defaultdict(list)
_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


def engine() -> Any:
    if not hasattr(app.state, "engine"):
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.config import get_settings

        app.state.engine = create_async_engine(get_settings().database_url, pool_size=3, max_overflow=2)
    return app.state.engine


async def module_for(mid: str, token: str) -> dict[str, Any] | None:
    """The module with this id and token, while it is installed (cached for a minute)."""
    key = hashlib.sha256(f"{mid}:{token}".encode()).hexdigest()
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < 60:
        return hit[1]
    async with engine().connect() as conn:
        r = (await conn.execute(text(
            "SELECT id, manifest, consent, settings FROM xmod WHERE id = :i AND token_hash = :h "
            "AND state IN ('installing', 'running', 'updating', 'starting')"),
            {"i": mid, "h": hashlib.sha256(token.encode()).hexdigest()})).mappings().first()  # fmt: skip
    got = dict(r) if r else None
    _cache[key] = (time.monotonic(), got)
    return got


async def me(request: Request) -> dict[str, Any]:
    auth = request.headers.get("authorization", "")
    mid = request.headers.get("x-wa-module", "")
    if not auth.startswith("Bearer ") or not mid:
        raise HTTPException(401, "send X-WA-Module and Authorization: Bearer <WA_MODULE_TOKEN>")
    m = await module_for(mid, auth[7:])
    if m is None:
        raise HTTPException(401, "not an installed module, or the wrong token")
    limit = int((m["manifest"].get("limits") or {}).get("requests_per_minute", 60))
    now, hits = time.monotonic(), _hits[mid]
    hits[:] = [t for t in hits if now - t < 60]
    if len(hits) >= limit:
        raise HTTPException(429, f"more than {limit} requests a minute (its limit)")
    hits.append(now)
    return m


async def shared(mid: str, what: str, detail: Any = None) -> None:
    """Personal data given to a module, written to its history (Settings → Outside modules shows it)."""
    async with engine().begin() as conn:
        await conn.execute(text("INSERT INTO xmod_event (module, kind, text, data) VALUES (:m, 'shared', :t, CAST(:d AS jsonb))"),
                           {"m": mid, "t": f"given {what}", "d": json.dumps(detail, default=str) if detail is not None else None})  # fmt: skip


Me = Annotated[dict[str, Any], Depends(me)]


def agreed(m: dict[str, Any], key: str) -> Any:
    return ((m.get("consent") or {}).get("data") or {}).get(key)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/me")
async def api_me(m: Me) -> dict[str, Any]:
    """Who it is, its settings, and what you agreed it may have."""
    c = m.get("consent") or {}
    return {"id": m["id"], "version": m["manifest"].get("version"), "settings": m.get("settings") or {},
            "agreed": {"data": c.get("data") or {}, "home_devices": c.get("home_devices") or {}, "hosts": c.get("hosts") or [],
                       "home_assistant_read": c.get("home_assistant_read") or [], "home_assistant_write": c.get("home_assistant_write") or [],
                       "notifications": bool(c.get("notifications"))}}  # fmt: skip


@app.get("/api/home")
async def api_home(m: Me) -> dict[str, Any]:
    """Your home's location, at the precision you agreed: exact (latitude and longitude) or postcode district (its
    district, such as KT17, and the middle of that district)."""
    level = agreed(m, "home_location")
    if not level:
        raise HTTPException(403, "you did not agree to share your home's location")
    async with engine().connect() as conn:
        h = (await conn.execute(text(
            "SELECT lat, lon, postcode FROM place WHERE lat IS NOT NULL ORDER BY (kind = 'home') DESC, (id = 'home') DESC, name LIMIT 1"))).first()  # fmt: skip
        if h is None:
            raise HTTPException(404, "no home place is set")
        if level == "exact":
            out = {"precision": "exact", "lat": round(h[0], 5), "lon": round(h[1], 5)}
        else:
            district = (h[2] or "").upper().split()[0] if h[2] and " " in h[2] else None
            mid_pt = None
            if district:
                mid_pt = (await conn.execute(text("SELECT avg(lat), avg(lon) FROM postcode WHERE pc LIKE :d"),
                                             {"d": district + "%"})).first()  # fmt: skip
            if not district or not mid_pt or mid_pt[0] is None:
                out = {"precision": "postcode district", "district": district, "lat": round(h[0], 2), "lon": round(h[1], 2)}
            else:
                out = {"precision": "postcode district", "district": district, "lat": round(mid_pt[0], 3), "lon": round(mid_pt[1], 3)}
    await shared(m["id"], f"your home's location ({out['precision']})")
    return out


@app.get("/api/places")
async def api_places(m: Me) -> dict[str, Any]:
    if not agreed(m, "places"):
        raise HTTPException(403, "you did not agree to share your places")
    async with engine().connect() as conn:
        rows = (await conn.execute(text("SELECT id, name, kind, lat, lon FROM place ORDER BY name"))).mappings().all()
    await shared(m["id"], f"your saved places ({len(rows)})")
    return {"places": [dict(r) for r in rows]}


@app.get("/api/vehicles")
async def api_vehicles(m: Me) -> dict[str, Any]:
    if not agreed(m, "vehicles"):
        raise HTTPException(403, "you did not agree to share your vehicles")
    async with engine().connect() as conn:
        rows = (await conn.execute(text("SELECT id, name, kind, registration FROM vehicle ORDER BY name"))).mappings().all()
    await shared(m["id"], f"your vehicles ({len(rows)})")
    return {"vehicles": [dict(r) for r in rows]}


@app.get("/api/ha/states")
async def api_ha_states(ids: str, m: Me) -> dict[str, Any]:
    """Home Assistant states, only of the entities it declared (patterns such as sensor.inverter_*)."""
    from app import ha_stats

    allowed = (m.get("consent") or {}).get("home_assistant_read") or []
    want = [i.strip() for i in ids.split(",") if i.strip()][:50]
    refused = [i for i in want if not any(fnmatch.fnmatchcase(i, p) for p in allowed)]
    if refused:
        raise HTTPException(403, f"not declared: {', '.join(refused)}")
    try:
        got = await ha_stats.states(engine(), want)
    except Exception as exc:
        raise HTTPException(502, f"Home Assistant: {exc}") from exc
    return {"states": {k: {"state": v.get("state"), "attributes": v.get("attributes"), "last_changed": v.get("last_changed")}
                       for k, v in got.items()}}  # fmt: skip


class EntityIn(BaseModel):
    component: str = Field(pattern="^(sensor|binary_sensor)$")
    object_id: str = Field(pattern=r"^[a-z0-9_]{1,60}$")
    name: str = Field(max_length=80)
    state: Any = None
    unit: str | None = Field(None, max_length=20)
    device_class: str | None = Field(None, max_length=40)
    attributes: dict[str, Any] = Field(default_factory=dict)


@app.post("/api/ha/entity")
async def api_ha_entity(body: EntityIn, m: Me) -> dict[str, Any]:
    """Publish one of its own entities to Home Assistant (wa_x_<id>_…, matching what it declared)."""
    from app.config import get_settings
    from app.mqtt import publish
    from app.mqtt_config import load

    mid = m["id"]
    uid = body.object_id if body.object_id.startswith(f"wa_x_{mid}_") else f"wa_x_{mid}_{body.object_id}"
    entity = f"{body.component}.{uid}"
    allowed = (m.get("consent") or {}).get("home_assistant_write") or []
    if not any(fnmatch.fnmatchcase(entity, p) for p in allowed):
        raise HTTPException(403, f"{entity} is not one it declared")
    s = get_settings()
    conf, _ = await load(engine())
    if not conf.active:
        raise HTTPException(409, "no MQTT broker is set for Home Assistant")
    state_t, attr_t = f"{s.mqtt_topic_prefix}/x/{mid}/{uid}/state", f"{s.mqtt_topic_prefix}/x/{mid}/{uid}/attributes"
    cfg_t = f"{s.mqtt_discovery_prefix}/{body.component}/{uid}/config"
    cfg = {"name": body.name, "unique_id": uid, "default_entity_id": entity, "state_topic": state_t, "json_attributes_topic": attr_t,
           "device": {"identifiers": [f"wa_x_{mid}"], "name": f"{m['manifest'].get('name')} (outside module)", "manufacturer": "World Assistant outside module"},
           **({"unit_of_measurement": body.unit} if body.unit else {}), **({"device_class": body.device_class} if body.device_class else {})}  # fmt: skip
    msgs = [(cfg_t, json.dumps(cfg), True), (state_t, "" if body.state is None else str(body.state), True),
            (attr_t, json.dumps(body.attributes)[:8000], True)]  # fmt: skip
    async with engine().begin() as conn:
        for t, _, _ in msgs:
            await conn.execute(text("INSERT INTO xmod_published (module, topic) VALUES (:m, :t) ON CONFLICT DO NOTHING"), {"m": mid, "t": t})
    ok = await publish(msgs, conf)
    return {"entity": entity, "published": ok}


class NoteIn(BaseModel):
    title: str = Field(max_length=80)
    body: str = Field(max_length=500)
    key: str = Field("", max_length=60, pattern=r"^[A-Za-z0-9_.:-]*$")


@app.post("/api/notify")
async def api_notify(body: NoteIn, m: Me) -> dict[str, Any]:
    """A notification to your phone, if it declared notifications (each `key` is sent once)."""
    from app import notify

    if not (m.get("consent") or {}).get("notifications"):
        raise HTTPException(403, "it did not declare notifications")
    key = body.key or hashlib.sha1(f"{body.title}|{body.body}".encode()).hexdigest()[:12]
    res = await notify.send(engine(), "modules", f"x:{m['id']}:{key}", f"{m['manifest'].get('name')}: {body.title}", body.body)
    return {"sent": res}


def gate_key() -> str:
    """What World Assistant's api sends to fetch a module's page: modules (which share this network) never have it."""
    from app.config import get_settings

    return hashlib.sha256(("wa-gate-pages:" + get_settings().api_token).encode()).hexdigest()


@app.api_route("/pages/{mid}/{path:path}", methods=["GET", "POST"])
async def pages(mid: str, path: str, request: Request) -> Response:
    """A module's own page, from its container (World Assistant shows it in a sandboxed frame). Only for World
    Assistant's api: another module cannot fetch it."""
    import hmac

    if not hmac.compare_digest(request.headers.get("x-wa-gate", ""), gate_key()):
        raise HTTPException(403, "only World Assistant fetches modules' pages")
    async with engine().connect() as conn:
        ok = (await conn.execute(text("SELECT 1 FROM xmod WHERE id = :i AND state = 'running'"), {"i": mid})).first()
    if not ok:
        raise HTTPException(404, "no such running module")
    host = "wa-x-" + mid.replace("_", "-")
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.request(request.method, f"http://{host}:8080/{path}", params=dict(request.query_params),
                            content=await request.body(), headers={"content-type": request.headers.get("content-type", "")})  # fmt: skip
    keep = {k: v for k, v in r.headers.items() if k.lower() in ("content-type", "cache-control")}
    return Response(r.content[:5_000_000], status_code=r.status_code, headers=keep)


# --- the way out: an HTTP proxy that lets through only what was declared and agreed --------------------------------


def allowed_host(host: str, port: int, consent: dict[str, Any]) -> bool:
    """Is host:port one it may reach? Declared outside hosts on 80 or 443 (a *. host covers its subdomains), or a
    device address you gave it (on the port given, or any port when none was)."""
    host = host.lower().rstrip(".")
    for d in (consent.get("home_devices") or {}).values():
        ip, _, p = d.partition(":")
        if host == ip and (not p or int(p) == port):
            return True
    if port not in (80, 443):
        return False
    for h in consent.get("hosts") or []:
        if host == h or (h.startswith("*.") and host.endswith(h[1:])):
            return True
    return False


def private(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast


async def _resolve_ok(host: str, consent: dict[str, Any]) -> bool:
    """A declared outside name must not lead to the home network (only the devices you gave are home addresses)."""
    devices = {d.partition(":")[0] for d in (consent.get("home_devices") or {}).values()}
    if host in devices:
        return True
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    return all(not private(i[4][0]) for i in infos)


async def _pipe(a: asyncio.StreamReader, b: asyncio.StreamWriter) -> None:
    try:
        while data := await a.read(65536):
            b.write(data)
            await b.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        b.close()


async def _proxy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    async def refuse(code: int, why: str) -> None:
        writer.write(f"HTTP/1.1 {code} Refused\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n{why}\n".encode())
        await writer.drain()
        writer.close()

    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
    except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        writer.close()
        return
    lines = head.decode("latin-1").split("\r\n")
    method, target, version = (lines[0].split(" ") + ["", "", ""])[:3]
    headers = {k.strip().lower(): v.strip() for k, _, v in (h.partition(":") for h in lines[1:] if h)}
    try:
        user, _, token = base64.b64decode(headers.get("proxy-authorization", "").removeprefix("Basic ").strip()).decode().partition(":")
    except ValueError:
        user, token = "", ""
    m = await module_for(user, token) if user and token else None
    if m is None:
        await refuse(407, "the module's own token is needed (HTTP_PROXY carries it)")
        return
    consent = m.get("consent") or {}
    if method == "CONNECT":
        host, _, port_s = target.rpartition(":")
        port, rest = int(port_s or 443), b""
    else:
        u = httpx.URL(target)
        host, port = u.host, u.port or 80
        path = u.raw_path.decode() or "/"
        fwd = [f"{method} {path} {version}"] + [h for h in lines[1:] if h and not h.lower().startswith(("proxy-", "connection:"))]
        rest = ("\r\n".join(fwd) + "\r\nConnection: close\r\n\r\n").encode()
    host = host.strip("[]")
    if not allowed_host(host, port, consent) or not await _resolve_ok(host, consent):
        log.warning("module %s tried to reach %s:%s, which it did not declare", m["id"], host, port)
        async with engine().begin() as conn:
            await conn.execute(text("INSERT INTO xmod_event (module, kind, text) VALUES (:m, 'refused', :t)"),
                               {"m": m["id"], "t": f"tried to reach {host}:{port}, which it did not declare"})  # fmt: skip
        await refuse(403, f"{host}:{port} is not one this module declared")
        return
    try:
        up_r, up_w = await asyncio.wait_for(asyncio.open_connection(host, port), 15)
    except (OSError, TimeoutError):
        await refuse(502, f"{host}:{port} did not answer")
        return
    if method == "CONNECT":
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
    else:
        up_w.write(rest)
        await up_w.drain()
    await asyncio.gather(_pipe(reader, up_w), _pipe(up_r, writer))


async def main() -> None:
    import uvicorn

    proxy = await asyncio.start_server(_proxy, "0.0.0.0", 8091)
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8090, log_level="warning"))
    async with proxy:
        await asyncio.gather(proxy.serve_forever(), server.serve())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
