"""The assistant's tool for this module, served over MCP (/mcp) and to Siri (/v1/ask)."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import Tool, _props


async def nest_status(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.nest.api import latest

    async with request.app.state.engine.connect() as conn:
        cur = await latest(conn)
    if not cur:
        return {"text": "There's no thermostat reading yet — the Nest keys may still need setting up on the Keys page.", "data": {"thermostats": []}}
    bits = []
    for t in cur:
        part = t["name"]
        if t.get("ambient_c") is not None:
            part += f": {t['ambient_c']:.0f}°C"
        if t.get("setpoint_c") is not None:
            part += f", set to {t['setpoint_c']:.0f}°C"
        if t.get("hvac") == "HEATING":
            part += ", heating now"
        elif (t.get("mode") or "").upper() == "OFF":
            part += ", off"
        bits.append(part)
    return {"text": "; ".join(bits) + ".", "data": {"thermostats": cur}}


TOOLS: list[Tool] = [
    Tool("nest_status", "What the Nest thermostat shows: room temperature, the target, and whether it is heating now.",
         {"type": "object", "properties": _props()}, nest_status),
]
