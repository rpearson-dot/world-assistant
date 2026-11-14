"""Discovery (/v1/discovery, In the home → Discovery): what each device on your network probably is, and the module
that would suit it. Reads what the Network module gathered and the IEEE register of adapter makers (fetched each
week). Changes nothing: each suggestion links to the module's page.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from app.auth import require_token
from app.jobs import tracked
from app.modules.discovery import identify as ID

router = APIRouter(prefix="/v1/discovery", tags=["discovery"], dependencies=[Depends(require_token)])
OUI_URL = "https://standards-oui.ieee.org/oui/oui.csv"


async def _looked_after(conn: Any) -> dict[str, set[str]]:
    """The addresses (or MACs) each module already looks after."""
    out: dict[str, set[str]] = {}
    v = (await conn.execute(text("SELECT value FROM app_setting WHERE key = 'airstage.units'"))).scalar()
    v = json.loads(v) if isinstance(v, str) else v
    out["airstage"] = {u.get("ip") for u in v or [] if u.get("ip")}
    for mod, sql in (("ocpp", "SELECT count(*) FROM ocpp_charger"), ("ovms", "SELECT count(*) FROM ovms_metric")):
        try:
            async with conn.begin_nested():
                n = (await conn.execute(text(sql))).scalar() or 0
        except Exception:  # noqa: BLE001 - the module's tables are not there
            n = 0
        out[mod] = {"*"} if n else set()
    return out


@router.get("")
async def discovery(request: Request) -> dict[str, Any]:
    """Every device with what it probably is (likely or possibly, and why), its adapter's maker, and what World
    Assistant could do with it."""
    from app.modules import gate, registry

    mods = list(registry.modules())
    enabled = await gate.enabled(request.app.state.engine)
    names = {m.id: m.name for m in mods}
    pages = {m.id: (m.link if m.link.startswith("/") else next((p for p in m.prefixes if not p.startswith("/v1")), None)) for m in mods}
    async with request.app.state.engine.connect() as conn:
        devs = [dict(r) for r in (await conn.execute(text(
            "SELECT mac, ip, name, alias, first_seen, last_seen FROM net_device ORDER BY last_seen DESC"))).mappings().all()]  # fmt: skip
        doms: dict[str, list[str]] = {}
        for mac, dest in (await conn.execute(text(
            "SELECT mac, dest FROM (SELECT mac, dest, count(*) AS n, row_number() OVER (PARTITION BY mac ORDER BY count(*) DESC) AS r "
            "FROM net_event WHERE kind = 'dns' AND mac IS NOT NULL AND dest IS NOT NULL AND at > now() - interval '7 days' GROUP BY mac, dest) x "
            "WHERE r <= 200"))).all():  # fmt: skip
            doms.setdefault(mac, []).append(dest.lower())
        ports: dict[str, list[int]] = {}
        for mac, port in (await conn.execute(text(
            "SELECT DISTINCT mac, port FROM net_event WHERE kind = 'firewall' AND port IS NOT NULL AND at > now() - interval '7 days'"))).all():  # fmt: skip
            ports.setdefault(mac, []).append(int(port))
        prefixes = sorted({ID.re.sub(r"[^0-9a-f]", "", d["mac"])[:6].upper() for d in devs})
        oui = {r[0]: r[1] for r in (await conn.execute(text("SELECT prefix, vendor FROM net_oui WHERE prefix = ANY(:p)"), {"p": prefixes})).all()}
        cared = await _looked_after(conn)
    out = []
    for d in devs:
        found = ID.identify(d, doms.get(d["mac"], []), ports.get(d["mac"], []), oui)
        cared_here = {k: (v if "*" not in v else {d.get("ip"), d["mac"]}) for k, v in cared.items()}
        sug = ID.suggestion(found, d, enabled, cared_here, names, pages) if found["kind"] else None
        out.append({**d, "label": d.get("alias") or d.get("name") or d.get("ip") or d["mac"], **found, "suggestion": sug})
    known = sum(1 for x in out if x["kind"])
    return {"devices": out, "identified": known, "oui_loaded": bool(oui) or not devs,
            "suggestions": sum(1 for x in out if x["suggestion"] and x["suggestion"].get("state") in ("off", "set up"))}  # fmt: skip


async def discovery_oui(ctx: dict[str, Any]) -> Any:
    """Each week: the IEEE register of MAC address prefixes (about 4 MB), so a device's adapter maker is known."""
    engine = ctx["engine"]

    async def work() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=120, headers={"User-Agent": "WorldAssistant"}) as client:
            r = await client.get(OUI_URL)
            r.raise_for_status()
        rows = ID.oui_rows(r.text)
        if len(rows) < 1000:
            raise ValueError(f"the register looks wrong ({len(rows)} rows)")
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM net_oui"))
            for i in range(0, len(rows), 5000):
                await conn.execute(text("INSERT INTO net_oui (prefix, vendor) VALUES (:p, :v) ON CONFLICT (prefix) DO UPDATE SET vendor = EXCLUDED.vendor"),
                                   [{"p": p, "v": v} for p, v in rows[i:i + 5000]])  # fmt: skip
        return {"prefixes": len(rows)}

    return await tracked(engine, "discovery_oui", work)
