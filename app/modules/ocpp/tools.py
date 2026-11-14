"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import Tool, _props, _wrap


async def charger(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.ocpp.api import overview

    d = await _wrap(overview(request))
    if not d["chargers"]:
        return {"text": "No charger has connected to World Assistant yet.", "data": d}
    parts = []
    for c in d["chargers"]:
        m, s = c.get("meter") or {}, c.get("session")
        line = f"The charger {c['id']} is " + ((c["status"] or "connected") if c["connected"] else "not connected")
        if c["connected"] and c["status"] == "Charging" and m.get("power_w") is not None:
            line += f" at {m['power_w'] / 1000:.1f} kW"
        if c.get("error_code") and c["error_code"] != "NoError":
            line += f", reporting {c['error_code']}"
        w = c["week"]
        line += f". This week: {w['boots']} restarts, {w['drops']} drops, {w['faults']} faults."
        if s and not s.get("stopped_at"):
            line += " A session is in progress."
        parts.append(line)
    return {"text": " ".join(parts), "data": d}


TOOLS: list[Tool] = [
    Tool("charger", "The car charger: whether it is connected and charging, its power, and how often it has restarted, "
         "dropped off or reported a fault this week (World Assistant's own OCPP server).",
         {"type": "object", "properties": _props()}, charger),
]
