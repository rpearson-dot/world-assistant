"""The assistant's tool for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask)."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import Tool, _props, _wrap


async def car_status(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.ovms.api import overview

    o = await _wrap(overview(request))
    parts = []
    for c in o["cars"]:
        bits = [f"{c['id']}:"]
        if c["soc"] is not None:
            bits.append(f"battery {c['soc']:.0f}%")
        if c["range_km"] is not None:
            bits.append(f"about {c['range_km'] * 0.621:.0f} miles")
        if c["charging"]:
            bits.append("charging")
        if c["locked"] is False:
            bits.append("unlocked")
        bits += [w["text"] for w in c["warnings"]]
        parts.append(" ".join(bits[:1]) + " " + ", ".join(bits[1:]) + ".")
    return {"text": " ".join(parts) or "No car has reported through OVMS.", "data": o}


TOOLS: list[Tool] = [
    Tool("car_status", "The car's battery, range, charging, lock state and any warnings, from its OVMS module.",
         {"type": "object", "properties": _props()}, car_status),
]
