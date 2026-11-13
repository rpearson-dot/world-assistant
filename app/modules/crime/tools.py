"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import Tool, ToolError, _point_of, _props


# --- Crime and police -----------------------------------------------------------------------------------------------
async def crime_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.crime.crime import CrimeError, look_up, words

    lat, lon = args.get("lat"), args.get("lon")
    if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))):
        lat, lon = await _point_of(request, args)
    try:
        d = await look_up(float(lat), float(lon), max(1, min(12, int(args.get("months") or 12))))
    except CrimeError as exc:
        raise ToolError(str(exc)) from exc
    return {"text": words(d), "data": d}


TOOLS: list[Tool] = [
    Tool("crime", "Street-level crime reported within a mile of a place over the last 12 months the police have published "
         "(about two months behind), e.g. to judge an area before moving: what kinds, how many a month, what happened "
         "to them, and the local police team with "
         "its phone number, priorities and coming events. From data.police.uk: England, Wales and Northern Ireland.",
         {"type": "object", "properties": _props(lat=("number", "latitude"), lon=("number", "longitude"),
                                                 postcode=("string", "a UK postcode instead"),
                                                 place=("string", "a saved place's name instead, e.g. home"),
                                                 months=("integer", "how many months, 1 to 12 (default 12)"))}, crime_tool),
]
