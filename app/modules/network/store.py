"""Keeping network events: each batch of events written, devices updated (by MAC address), the day's counts by device,
and the first sighting of a device noted (for the new-device notification). Pi-hole's lookups name only an address,
so they are put against the device that address was last given to by the router (its DHCP lease).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

KEY = "network.settings"
DEFAULTS: dict[str, Any] = {"keep_days": 30, "pihole_url": "", "notify_new": True, "lists": ["samsung", "lgwebos", "amazon", "apple", "roku",
                                                                                           "xiaomi", "huawei", "winoffice", "tiktok",
                                                                                           "vivo", "oppo-realme"]}  # fmt: skip
# The manufacturers' lists (HaGeZi's "native trackers", GPL-3.0), by their file names, as people would say them
LIST_NAMES = {"samsung": "Samsung", "lgwebos": "LG webOS", "amazon": "Amazon", "apple": "Apple", "roku": "Roku", "xiaomi": "Xiaomi",
              "huawei": "Huawei", "winoffice": "Microsoft Windows and Office", "tiktok": "TikTok", "vivo": "Vivo", "oppo-realme": "Oppo and Realme"}


async def settings(conn: Any) -> dict[str, Any]:
    v = (await conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": KEY})).scalar()
    v = json.loads(v) if isinstance(v, str) else v
    return {**DEFAULTS, **(v or {})}


async def macs_for(conn: Any, ips: set[str]) -> dict[str, str]:
    if not ips:
        return {}
    rows = (await conn.execute(text(
        "SELECT DISTINCT ON (ip) ip, mac FROM net_device WHERE ip = ANY(:i) ORDER BY ip, last_seen DESC"), {"i": sorted(ips)})).all()  # fmt: skip
    return {r[0]: r[1] for r in rows}


async def keep(conn: Any, events: list[dict[str, Any]]) -> list[str]:
    """Write events; update devices; count the day. Returns the MACs seen for the first time."""
    if not events:
        return []
    need = {e["ip"] for e in events if e.get("ip") and not e.get("mac")}
    known = await macs_for(conn, need)
    for e in events:
        if not e.get("mac") and e.get("ip") in known:
            e["mac"] = known[e["ip"]]
    await conn.execute(text(
        "INSERT INTO net_event (at, source, kind, mac, ip, action, dest, port, proto, detail) "
        "VALUES (:at, :source, :kind, :mac, :ip, :action, :dest, :port, :proto, CAST(:detail AS jsonb))"),
        [{**{k: e.get(k) for k in ("at", "source", "kind", "mac", "ip", "action", "dest", "port", "proto")},
          "detail": json.dumps(e.get("detail") or {}, default=str)} for e in events])  # fmt: skip
    new: list[str] = []
    per: dict[str, dict[str, Any]] = {}
    for e in events:  # for each device: the newest event, and the newest address and name the router gave it
        if not e.get("mac") or e["kind"] not in ("dhcp", "client", "firewall", "dns"):
            continue
        p = per.setdefault(e["mac"], {"at": e["at"], "what": None, "ip": None, "name": None, "conn": None})
        if e["at"] >= p["at"]:
            p["at"], p["what"] = e["at"], f"{e['kind']} {e.get('action') or ''}".strip()
        p["what"] = p["what"] or f"{e['kind']} {e.get('action') or ''}".strip()
        if e["kind"] in ("dhcp", "client"):
            p["ip"], p["name"] = e.get("ip") or p["ip"], e.get("name") or p["name"]
            p["conn"] = (e.get("detail") or {}).get("ssid") or (e.get("detail") or {}).get("ap") or p["conn"]
    for m, p in per.items():
        fresh = (await conn.execute(text(
            "INSERT INTO net_device (mac, ip, name, first_seen, last_seen, last_what, connection) VALUES (:m, :ip, :n, :at, :at, :w, :c) "
            "ON CONFLICT (mac) DO UPDATE SET ip = coalesce(EXCLUDED.ip, net_device.ip), name = coalesce(EXCLUDED.name, net_device.name), "
            "last_seen = greatest(net_device.last_seen, EXCLUDED.last_seen), last_what = EXCLUDED.last_what, "
            "connection = coalesce(EXCLUDED.connection, net_device.connection) RETURNING (xmax = 0)"),
            {"m": m, "ip": p["ip"], "n": p["name"], "at": p["at"], "w": p["what"], "c": p["conn"]})).scalar()  # fmt: skip
        if fresh:
            new.append(m)
    counts = Counter((e["at"].astimezone(UTC).date(), e.get("mac") or f"ip:{e.get('ip')}", e["kind"], e.get("action") or "-")
                     for e in events if e["kind"] in ("dns", "firewall", "client", "dhcp"))  # fmt: skip
    if counts:
        await conn.execute(text(
            "INSERT INTO net_day (day, mac, kind, action, n) VALUES (:d, :m, :k, :a, :n) "
            "ON CONFLICT (day, mac, kind, action) DO UPDATE SET n = net_day.n + EXCLUDED.n"),
            [{"d": d, "m": m, "k": k, "a": a, "n": n} for (d, m, k, a), n in counts.items()])  # fmt: skip
    return new


async def housekeep(conn: Any) -> dict[str, Any]:
    st = await settings(conn)
    gone = 0
    for _ in range(200):  # a batch at a time, so a large table is not locked for long
        n = (await conn.execute(text(
            "DELETE FROM net_event WHERE id IN (SELECT id FROM net_event WHERE at < now() - make_interval(days => :d) LIMIT 20000)"),
            {"d": int(st["keep_days"])})).rowcount  # fmt: skip
        gone += n
        if n < 20000:
            break
    await conn.execute(text("DELETE FROM net_day WHERE day < current_date - 400"))
    return {"events_deleted": gone, "at": datetime.now(UTC).isoformat()}
