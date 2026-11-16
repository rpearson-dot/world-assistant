"""The assistant's tool for the Ubiquiti side: which devices talk to the most outside hosts, from the UniFi gateway's
firewall log, to spot a chatty IoT device. Reads the network module's record; changes nothing."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy import text

from app.assistant_base import Tool, _props
from app.modules.network import firewall as fw


async def chatty_devices(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT coalesce(d.alias, d.name, d.ip, d.mac) AS label, e.dest, count(*) AS n "
            "FROM net_device d JOIN net_event e ON e.mac = d.mac "
            "WHERE e.kind = 'firewall' AND e.dest IS NOT NULL AND e.at > now() - interval '7 days' "
            "GROUP BY label, e.dest"))).mappings().all()  # fmt: skip
    by: dict[str, dict[str, int]] = {}
    for r in rows:
        if fw.is_private(r["dest"]):
            continue
        d = by.setdefault(r["label"], {"hosts": 0, "conns": 0})
        d["hosts"] += 1
        d["conns"] += r["n"]
    ranked = sorted(({"device": k, **v} for k, v in by.items()), key=lambda x: -x["hosts"])[:10]
    if not ranked:
        return {"text": "No outside connections in the UniFi firewall log over the last week (is the gateway's syslog pointed here?).",
                "data": {"devices": []}}
    lead = ranked[0]
    words = ", ".join(f"{d['device']} ({d['hosts']} hosts)" for d in ranked[:5])
    return {"text": f"Chattiest devices over the last week, by outside hosts reached (UniFi firewall log): {words}. "
                    f"{lead['device']} reaches the most ({lead['hosts']} hosts, {lead['conns']} connections) — a candidate "
                    f"for a firewall rule if it only needs local control.",
            "data": {"devices": ranked}}


TOOLS: list[Tool] = [
    Tool("chatty_devices", "Which devices on the home network talk to the most outside hosts, from the UniFi gateway's "
                           "firewall log — to spot a chatty IoT device worth a firewall block.",
         {"type": "object", "properties": _props()}, chatty_devices),
]
