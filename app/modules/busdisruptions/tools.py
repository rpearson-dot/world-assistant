"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import Tool, _props, _wrap


async def bus_disruptions_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.busdisruptions.bus_disruptions import near

    place = str(args.get("place") or "") or None
    line = str(args.get("line") or "") or None
    r = await _wrap(near(request, place=place, line=line, within_km=float(args["km"]) if args.get("km") else None, limit=20))
    if not r["count"]:
        return {"text": "No bus disruptions published" + (f" for the {line}" if line else "") + " near your places.", "data": r}
    bits = [(d["summary"] or d["reason"] or "disruption") + (f" (lines {', '.join(d['lines'][:4])})" if d["lines"] else "")
            for d in r["disruptions"][:4]]  # fmt: skip
    return {"text": f"{r['count']} bus disruption(s): " + "; ".join(bits) + ".", "data": r}


TOOLS: list[Tool] = [
    Tool("bus_disruptions", "Published bus disruptions near your saved places: diversions, closed stops, changes.",
         {"type": "object", "properties": _props(place=("string", "only those nearest this saved place"),
                                                 line=("string", "only those affecting this line"),
                                                 km=("number", "only within this distance of the place"))}, bus_disruptions_tool),
]
