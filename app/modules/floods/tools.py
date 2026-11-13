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


async def floods_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.floods.floods import at

    name, lat, lon = await _point(request, args.get("place"))
    km = float(args.get("km") or 0)
    r = await _wrap(at(request, place=None, lat=lat, lon=lon, within_km=km))
    where = f"{name}" if not km else f"within {km:g} km of {name}"
    if not r["count"]:
        return {"text": f"No flood warnings or alerts cover {where}.", "data": r}
    bits = [f"{w['severity'] or 'warning'} for {w['area']}" for w in r["warnings"][:4]]
    return {"text": f"{r['count']} flood warning(s) cover {where}: " + "; ".join(bits) + ".", "data": r}


TOOLS: list[Tool] = [
    Tool("floods_near", "Environment Agency flood warnings and alerts covering a saved place (or within some km of it).",
         {"type": "object", "properties": _props(place=PLACE, km=("number", "0 (default): the place must be inside the warned area"))},
         floods_tool),
]
