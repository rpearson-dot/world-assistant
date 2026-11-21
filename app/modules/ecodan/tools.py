"""The assistant's tool for this module, served over MCP (/mcp) and to Siri (/v1/ask)."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import Tool, _props


async def ecodan_status(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.ecodan.api import latest

    async with request.app.state.engine.connect() as conn:
        cur = await latest(conn)
    if not cur:
        return {"text": "There's no heat-pump reading yet — the MELCloud login may still need setting up on the Keys page.", "data": {"units": []}}
    bits = []
    for u in cur:
        part = u["name"]
        if u.get("tank_c") is not None:
            part += f": tank {u['tank_c']:.0f}°C"
        if u.get("tank_target_c") is not None:
            part += f" (target {u['tank_target_c']:.0f}°C)"
        if u.get("forced_hot_water"):
            part += ", heating water now"
        bits.append(part)
    return {"text": "; ".join(bits) + ".", "data": {"units": cur}}


TOOLS: list[Tool] = [
    Tool("ecodan_status", "What the Ecodan heat pump shows: hot-water tank temperature and target, and whether it is heating water now.",
         {"type": "object", "properties": _props()}, ecodan_status),
]
