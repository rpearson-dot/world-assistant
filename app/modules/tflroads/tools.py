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


async def road_disruptions_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.tflroads.tflroads import at

    name, lat, lon = await _point(request, args.get("place"))
    km = float(args.get("km") or 0.5)
    r = await _wrap(at(request, place=None, lat=lat, lon=lon, radius_m=max(10.0, min(20000.0, km * 1000))))
    if not r["count"]:
        return {"text": f"No TfL road disruptions within {km:g} km of {name}.", "data": r}
    bits = []
    for d in r["disruptions"][:4]:
        what = d["sub_category"] or d["category"] or "disruption"
        bits.append(f"{what}{' (closure)' if d['closure'] else ''} at {d['location'] or 'a road'}, {d['metres']} m away")
    return {"text": f"{r['count']} TfL road disruption(s) near {name}: " + "; ".join(bits) + ".", "data": r}


TOOLS: list[Tool] = [
    Tool("road_disruptions_near", "TfL road works, closures and incidents near a saved place in London.",
         {"type": "object", "properties": _props(place=PLACE, km=("number", "how far, default 0.5"))}, road_disruptions_tool),
]
