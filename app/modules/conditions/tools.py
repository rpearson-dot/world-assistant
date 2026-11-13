"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import (
    Tool,
    ToolError,
    _props,
    _wrap,
)


async def conditions_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.conditions.conditions import all_conditions, trip_conditions

    days = int(args.get("days") or 7)
    trip = str(args.get("trip") or "") or None
    if trip:
        r = await _wrap(trip_conditions(request, trip, None, days))
        return {"text": r["summary"], "data": r}
    res = await all_conditions(request, days)
    done = [t for t in res["trips"] if "now" in t]
    if not done:
        raise ToolError("No trip has a route yet. Build one on the Places page, then ask again.")
    return {"text": " ".join(t["summary"] for t in done), "data": res}


TOOLS: list[Tool] = [
    Tool("road_conditions", "Road works, closures, incidents and flood warnings on a saved trip's route, now and planned. No trip means all of them.",
         {"type": "object", "properties": _props(trip=("string", "id of a saved routed trip; default: all"),
                                                  days=("number", "how far ahead to look for planned works, default 7"))}, conditions_tool),
]
