"""Pi-hole (/v1/pihole, Your network → Network): its lookups read each minute and put against each device, the
devices it knows (so it works without the UniFi gateway log), your Pi-hole groups, and suggested blocks for what gets
past your Pi-hole lists that does not serve you.

Changing Pi-hole happens only when you press Add to Pi-hole (POST /v1/pihole/devices/{mac}/block), for exactly the
domains you ticked, and Undo takes back exactly what was added. Your groups (Kids, Adults, IoT) are never changed and
no device is moved out of them.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth import require_token
from app.config import get_settings
from app.jobs import tracked
from app.modules.network import store
from app.modules.network.api import _label
from app.modules.pihole import client as PH
from app.modules.pihole import suggest

router = APIRouter(prefix="/v1/pihole", tags=["pihole"], dependencies=[Depends(require_token)])
LISTS = "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/domains/native.{name}.txt"
LEARN_DAYS = 7


async def _pihole(conn: Any) -> PH.Pihole:
    st = await store.settings(conn)
    pw = get_settings().pihole_app_password
    if not st["pihole_url"] or not pw:
        raise HTTPException(409, "Pi-hole is not set up: its address and app password (Network page, Settings)")
    return PH.Pihole(st["pihole_url"], pw)


@router.get("/device-groups")
async def device_groups(request: Request) -> dict[str, Any]:
    """Each device's Pi-hole groups (Kids, Adults, IoT...), by MAC, for the Network page."""
    async with request.app.state.engine.connect() as conn:
        ph = await _pihole(conn)
        devs = [dict(r) for r in (await conn.execute(text("SELECT mac, ip, name, alias FROM net_device"))).mappings().all()]
    try:
        async with ph:
            gs, cs = await ph.groups(), await ph.clients()
    except (PH.PiholeError, httpx.HTTPError) as exc:
        raise HTTPException(502, f"Pi-hole: {exc}") from exc
    return {"groups": {d["mac"]: PH.groups_of(cs, gs, d) for d in devs}}


class PiholeIn(BaseModel):
    pihole_url: str = Field("", max_length=200)


@router.get("/settings")
async def get_settings_(request: Request) -> dict[str, Any]:
    """Pi-hole's address and whether its app password is on the Keys page."""
    async with request.app.state.engine.connect() as conn:
        st = await store.settings(conn)
    return {"pihole_url": st["pihole_url"], "pihole_password_set": bool(get_settings().pihole_app_password)}


@router.put("/settings")
async def put_settings(request: Request, body: PiholeIn) -> dict[str, Any]:
    """Pi-hole's address on your network (such as http://10.0.2.49:8080)."""
    import ipaddress
    from urllib.parse import urlparse

    url = body.pihole_url.strip().rstrip("/")
    if url:
        u = urlparse(url)
        try:
            ok = u.scheme in ("http", "https") and ipaddress.ip_address(u.hostname or "").is_private
        except ValueError:
            ok = False
        if not ok:
            raise HTTPException(422, "Pi-hole's address on your home network, such as http://10.0.2.49:8080")
    async with request.app.state.engine.begin() as conn:
        st = {**await store.settings(conn), "pihole_url": url}
        await store.save_settings(conn, st)
    return {"pihole_url": url, "pihole_password_set": bool(get_settings().pihole_app_password)}


@router.get("/devices/{mac}/suggestions")
async def suggestions(request: Request, mac: str, days: int = Query(LEARN_DAYS, ge=1, le=30)) -> dict[str, Any]:
    """Suggested blocks for this device: what got past your Pi-hole lists over the last days that does not serve you,
    each with why, and what blocking it may break. Things it needs are shown as "keep"."""
    mac = mac.lower()
    async with request.app.state.engine.connect() as conn:
        d = (await conn.execute(text("SELECT first_seen FROM net_device WHERE mac = :m"), {"m": mac})).first()
        if d is None:
            raise HTTPException(404, "no such device")
        rows = [dict(r) for r in (await conn.execute(text(
            "SELECT dest AS domain, count(*) AS n, count(DISTINCT extract(hour FROM at)) AS hours FROM net_event "
            "WHERE mac = :m AND kind = 'dns' AND action = 'allow' AND dest IS NOT NULL AND at > now() - make_interval(days => :d) "
            "GROUP BY dest"), {"m": mac, "d": days})).mappings().all()]  # fmt: skip
        doms = [r["domain"] for r in rows]
        bases = sorted({suggest.base(x) for x in doms})
        refs: dict[str, list[str]] = {}
        for dom, lst in (await conn.execute(text("SELECT domain, list FROM net_reference WHERE domain = ANY(:d)"),
                                            {"d": sorted(set(doms) | set(bases))})).all():  # fmt: skip
            refs.setdefault(dom, []).append(store.LIST_NAMES.get(lst, lst))
        mine = set((await conn.execute(text("SELECT domain FROM net_block WHERE mac = :m"), {"m": mac})).scalars().all())
    watched = (datetime.now(UTC) - d[0]).total_seconds() / 86400
    out = suggest.for_device(rows, refs, min(days, max(watched, 1)), mine)
    out["learning"] = watched < LEARN_DAYS
    out["watched_days"] = round(watched, 1)
    return out


class BlockIn(BaseModel):
    domains: list[str] = Field(min_length=1, max_length=200)
    group_id: int | None = Field(None, description="one of your Pi-hole groups (every device in it); none: this device only")


@router.post("/devices/{mac}/block")
async def block(request: Request, mac: str, body: BlockIn) -> dict[str, Any]:
    """Add the domains you ticked to Pi-hole: for this device only (its own extra group, beside the groups it is in)
    or for one of your groups. Your groups and lists are never changed otherwise."""
    import re

    mac = mac.lower()
    doms = sorted({x.strip().lower().strip(".") for x in body.domains})
    if any(not re.fullmatch(r"([a-z0-9_-]+\.)+[a-z0-9-]{2,}", x) for x in doms):
        raise HTTPException(422, "each is a domain name such as metrics.example.com")
    async with request.app.state.engine.begin() as conn:
        d = (await conn.execute(text("SELECT * FROM net_device WHERE mac = :m"), {"m": mac})).mappings().first()
        if d is None or not d["ip"]:
            raise HTTPException(404, "no such device, or no address known for it yet")
        ph = await _pihole(conn)
        dev = {"mac": mac, "ip": d["ip"], "name": d["name"], "label": _label(dict(d))}
        try:
            async with ph:
                done = await ph.block(dev, doms, body.group_id)
        except (PH.PiholeError, httpx.HTTPError) as exc:
            raise HTTPException(502, f"Pi-hole: {exc}") from exc
        if body.group_id is None:
            await conn.execute(text("INSERT INTO net_block (mac, domain) VALUES (:m, :d) ON CONFLICT DO NOTHING"),
                               [{"m": mac, "d": x} for x in doms])  # fmt: skip
        else:
            await conn.execute(text("INSERT INTO net_block (mac, domain) VALUES (:m, :d) ON CONFLICT DO NOTHING"),
                               [{"m": f"group:{body.group_id}", "d": x} for x in doms])  # fmt: skip
    return done


class UnblockIn(BaseModel):
    domains: list[str] = Field(default_factory=list, max_length=200)  # none: everything added for it


@router.post("/devices/{mac}/unblock")
async def unblock(request: Request, mac: str, body: UnblockIn) -> dict[str, Any]:
    """Undo: the domains World Assistant added for this device taken out of Pi-hole (all of them by default, and then
    its own group too)."""
    mac = mac.lower()
    async with request.app.state.engine.begin() as conn:
        d = (await conn.execute(text("SELECT * FROM net_device WHERE mac = :m"), {"m": mac})).mappings().first()
        mine = set((await conn.execute(text("SELECT domain FROM net_block WHERE mac = :m"), {"m": mac})).scalars().all())
        doms = sorted(mine & {x.lower() for x in body.domains}) if body.domains else sorted(mine)
        if d is None or not doms:
            raise HTTPException(404, "nothing World Assistant added for this device")
        ph = await _pihole(conn)
        try:
            async with ph:
                await ph.unblock(doms, {"mac": mac, "ip": d["ip"], "name": d["name"], "label": _label(dict(d))}, all_gone=set(doms) == mine)
        except (PH.PiholeError, httpx.HTTPError) as exc:
            raise HTTPException(502, f"Pi-hole: {exc}") from exc
        await conn.execute(text("DELETE FROM net_block WHERE mac = :m AND domain = ANY(:d)"), {"m": mac, "d": doms})
    return {"removed": doms}


@router.get("/groups")
async def groups(request: Request) -> dict[str, Any]:
    """Your Pi-hole groups (Kids, Adults, IoT...), each with its devices, and what gets past your lists for them this week."""
    async with request.app.state.engine.connect() as conn:
        ph = await _pihole(conn)
        devs = [dict(r) for r in (await conn.execute(text("SELECT mac, ip, name, alias FROM net_device"))).mappings().all()]
        week = {r[0]: r[1] for r in (await conn.execute(text(
            "SELECT mac, sum(n) FROM net_day WHERE kind = 'dns' AND action = 'allow' AND day > current_date - 7 GROUP BY mac"))).all()}  # fmt: skip
    try:
        async with ph:
            gs, cs = await ph.groups(), await ph.clients()
    except (PH.PiholeError, httpx.HTTPError) as exc:
        raise HTTPException(502, f"Pi-hole: {exc}") from exc
    out = []
    for g in gs:
        if g["id"] == 0 or str(g["name"]).startswith(PH.GROUP):
            continue
        members = [d for d in devs if g["name"] in PH.groups_of(cs, gs, d)]
        out.append({**g, "devices": [_label(d) for d in members], "lookups_week": sum(int(week.get(d["mac"], 0)) for d in members)})
    return {"groups": out}


@router.post("/test")
async def pihole_test(request: Request) -> dict[str, Any]:
    """Sign in to Pi-hole and read its groups: says whether the address and app password work."""
    async with request.app.state.engine.connect() as conn:
        ph = await _pihole(conn)
    try:
        async with ph:
            gs = await ph.groups()
    except (PH.PiholeError, httpx.HTTPError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "groups": [g["name"] for g in gs]}


# --- jobs ------------------------------------------------------------------------------------------------------------


async def network_pihole(ctx: dict[str, Any]) -> Any:
    """Every minute: Pi-hole's new lookups, each put against the device the router gave that address to."""
    engine, redis = ctx["engine"], ctx["redis"]

    async def work() -> dict[str, Any]:
        async with engine.connect() as conn:
            st = await store.settings(conn)
        pw = get_settings().pihole_app_password
        if not st["pihole_url"] or not pw:
            return {"not_set_up": "Pi-hole's address (Network page) and app password (Keys page) are not set"}
        now = datetime.now(UTC)
        raw = await redis.get("wa:network:pihole:last")
        last_t, last_id = (json.loads(raw) if raw else [(now - timedelta(minutes=10)).timestamp(), 0])
        async with PH.Pihole(st["pihole_url"], pw) as ph:
            qs = await ph.queries(last_t - 5, now.timestamp(), int(last_id))
            # the devices Pi-hole knows (its network table), every ten minutes: names and addresses without UniFi
            seen = []
            if now.minute % 10 == 0:
                try:
                    seen = PH.device_events(await ph.network_devices(), now)
                except (PH.PiholeError, httpx.HTTPError):
                    seen = []
        evs = seen + PH.events(qs)
        if evs:
            async with engine.begin() as conn:
                await store.keep(conn, evs)
            top = qs[-1]
            await redis.set("wa:network:pihole:last", json.dumps([float(top.get("time") or now.timestamp()), int(top.get("id") or 0)]))
        return {"lookups": len(evs)}

    return await tracked(engine, "network_pihole", work)


async def network_lists(ctx: dict[str, Any]) -> Any:
    """Each week: the manufacturers' tracker lists, used only to recognise what gets past your Pi-hole lists."""
    engine = ctx["engine"]

    async def work() -> dict[str, Any]:
        async with engine.connect() as conn:
            st = await store.settings(conn)
        got: dict[str, int] = {}
        async with httpx.AsyncClient(timeout=60, headers={"User-Agent": "WorldAssistant"}) as client:
            for name in st["lists"]:
                r = await client.get(LISTS.format(name=name))
                if r.status_code != 200:
                    continue
                doms = sorted({ln.strip().lower() for ln in r.text.splitlines() if ln.strip() and not ln.startswith("#")})[:200_000]
                async with engine.begin() as conn:
                    await conn.execute(text("DELETE FROM net_reference WHERE list = :l"), {"l": name})
                    for i in range(0, len(doms), 5000):
                        await conn.execute(text("INSERT INTO net_reference (domain, list) VALUES (:d, :l) ON CONFLICT DO NOTHING"),
                                           [{"d": d, "l": name} for d in doms[i:i + 5000]])  # fmt: skip
                got[name] = len(doms)
        return {"lists": got}

    return await tracked(engine, "network_lists", work)
