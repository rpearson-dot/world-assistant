"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import (
    PLACE,
    Tool,
    _point,
    _props,
    _wrap,
)


async def stops_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.naptan.stops import near

    name, lat, lon = await _point(request, args.get("place"))
    km = float(args.get("km") or 0.5)
    kind = str(args.get("kind") or "") or None
    r = await _wrap(near(request, lat=lat, lon=lon, radius_km=km, kind=kind, limit=10))
    if not r["stops"]:
        return {"text": f"No {kind.replace('_', ' ') if kind else 'stops'} within {km:g} km of {name}.", "data": r}
    bits = [f"{s['name']}" + (f" ({s['indicator']})" if s.get("indicator") else "") + f", {round(s['km'] * 1000)} m"
            for s in r["stops"][:6]]  # fmt: skip
    return {"text": f"Nearest {kind.replace('_', ' ') + 's' if kind else 'stops'} to {name}: " + "; ".join(bits) + ".", "data": r}


TOOLS: list[Tool] = [
    Tool("stops_near", "The nearest bus stops, stations and other stops to a saved place (NaPTAN).",
         {"type": "object", "properties": _props(place=PLACE, km=("number", "how far, default 0.5"),
                                                 kind=("string", "optional: bus_stop, bus_station, rail, metro, ferry or air"))},
         stops_tool),
]
