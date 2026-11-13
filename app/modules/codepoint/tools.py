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


async def postcode_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.codepoint.postcodes import near, one

    if args.get("postcode"):
        r = await _wrap(one(request, str(args["postcode"])))
        return {"text": f"{r['postcode']} is at {r['lat']:.5f}, {r['lon']:.5f}.", "data": r}
    name, lat, lon = await _point(request, args.get("place"))
    r = await _wrap(near(request, lat=lat, lon=lon, radius_km=0.5, limit=3))
    found = r.get("postcodes") or []
    if not found:
        return {"text": f"No postcode found within half a km of {name}.", "data": r}
    return {"text": f"The nearest postcode to {name} is {found[0]['postcode']}.", "data": r}


TOOLS: list[Tool] = [
    Tool("postcode", "Where a UK postcode is, or the nearest postcode to a saved place (from the local Code-Point Open table).",
         {"type": "object", "properties": _props(postcode=("string", "a UK postcode, e.g. SW1A 2AA"), place=PLACE)},
         postcode_tool),
]
