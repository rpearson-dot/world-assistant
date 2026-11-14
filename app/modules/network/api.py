"""The home network (/v1/network, In the home → Network): every device the router has seen, what each did (joining
and leaving, DNS lookups through Pi-hole, firewall decisions), your Pi-hole groups, and suggested blocks for what gets
past your Pi-hole lists that does not serve you. Nothing goes to Home Assistant.

Changing Pi-hole happens only when you press Add to Pi-hole (POST /v1/network/devices/{mac}/block), for exactly the
domains you ticked, and Undo takes back exactly what was added.
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
from app.modules.network import pihole as PH
from app.modules.network import store, suggest

router = APIRouter(prefix="/v1/network", tags=["network"], dependencies=[Depends(require_token)])
LISTS = "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/domains/native.{name}.txt"
LEARN_DAYS = 7


def _label(d: dict[str, Any]) -> str:
    return d.get("alias") or d.get("name") or d.get("ip") or d["mac"]


async def _pihole(conn: Any) -> PH.Pihole:
    st = await store.settings(conn)
    pw = get_settings().pihole_app_password
    if not st["pihole_url"] or not pw:
        raise HTTPException(409, "Pi-hole is not set up: its address on this page, and its app password on the Keys page")
    return PH.Pihole(st["pihole_url"], pw)


@router.get("/devices")
async def devices(request: Request) -> dict[str, Any]:
    """Every device, newest first: its name, address, when first and last seen, and today's lookups, blocks and
    firewall decisions; with your Pi-hole groups when Pi-hole is set up."""
    async with request.app.state.engine.connect() as conn:
        rows = [dict(r) for r in (await conn.execute(text(
            "SELECT d.*, coalesce(sum(n) FILTER (WHERE kind = 'dns' AND action = 'allow'), 0) AS lookups, "
            "coalesce(sum(n) FILTER (WHERE kind = 'dns' AND action = 'block'), 0) AS blocked, "
            "coalesce(sum(n) FILTER (WHERE kind = 'firewall' AND action = 'block'), 0) AS firewall_blocks "
            "FROM net_device d LEFT JOIN net_day y ON y.mac = d.mac AND y.day = current_date GROUP BY d.mac ORDER BY d.last_seen DESC"))).mappings().all()]  # fmt: skip
        try:
            ph = await _pihole(conn)
        except HTTPException:
            ph = None
    groups: dict[str, list[str]] = {}
    if ph is not None:
        try:
            async with ph:
                gs, cs = await ph.groups(), await ph.clients()
            for d in rows:
                groups[d["mac"]] = PH.groups_of(cs, gs, d)
        except (PH.PiholeError, httpx.HTTPError):
            groups = {}
    for d in rows:
        d["label"], d["groups"] = _label(d), groups.get(d["mac"])
    return {"devices": rows, "pihole": ph is not None}


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
    pihole_url: str = Field("", max_length=200)
    notify_new: bool = True


@router.get("/settings")
async def get_settings_(request: Request) -> dict[str, Any]:
    async with request.app.state.engine.connect() as conn:
        st = await store.settings(conn)
    port = get_settings().syslog_port
    return {**st, "syslog_port": port, "pihole_password_set": bool(get_settings().pihole_app_password)}


@router.put("/settings")
async def put_settings(request: Request, body: SettingsIn) -> dict[str, Any]:
    """How long events are kept, Pi-hole's address on your network (such as http://10.0.2.29:8080), and the new-device
    notification."""
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
            raise HTTPException(422, "Pi-hole's address on your home network, such as http://10.0.2.29:8080")
    async with request.app.state.engine.begin() as conn:
        st = {**await store.settings(conn), "keep_days": body.keep_days, "pihole_url": url, "notify_new": body.notify_new}
        await conn.execute(text(
            "INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"), {"k": store.KEY, "v": json.dumps(st)})  # fmt: skip
    return st


@router.post("/pihole/test")
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
        evs = PH.events(qs)
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


async def network_housekeep(ctx: dict[str, Any]) -> Any:
    async def work() -> dict[str, Any]:
        async with ctx["engine"].begin() as conn:
            return await store.housekeep(conn)

    return await tracked(ctx["engine"], "network_housekeep", work)
