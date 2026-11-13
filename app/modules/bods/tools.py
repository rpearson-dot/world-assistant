"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import (
    PLACE,
    Tool,
    _miles,
    _point,
    _props,
    _wrap,
)


async def live_buses_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.bods.bods import live

    name, lat, lon = await _point(request, args.get("place"))
    km = float(args.get("km") or 1)
    line = str(args.get("line") or "") or None
    r = await _wrap(live(request, place=None, lat=lat, lon=lon, radius_km=km, line=line, limit=50))
    if not r["count"]:
        return {"text": f"No buses{' on the ' + line if line else ''} reporting within {_miles(km)} of {name} right now.", "data": r}
    bits = [f"{b['line'] or 'a bus'}" + (f" to {b['destination']}" if b.get("destination") else "") + f", {_miles(b['km'])} away"
            for b in r["buses"][:5]]  # fmt: skip
    return {"text": f"{r['count']} bus(es) near {name} now: " + "; ".join(bits) + ".", "data": r}


TOOLS: list[Tool] = [
    Tool("live_buses_near", "Buses reporting their position near a saved place right now, anywhere in Great Britain.",
         {"type": "object", "properties": _props(place=PLACE, km=("number", "how far, default 1"),
                                                 line=("string", "only this line, e.g. 213"))}, live_buses_tool),
]
