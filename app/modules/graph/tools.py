"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import PLACE, Tool, _props, _wrap
from app.places import place_or_default


async def connections_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.graph.graph_routes import disruption, neighbours

    pid = await _wrap(place_or_default(request, args.get("place") or None))
    d = await _wrap(disruption(place=pid, limit=50))
    n = await _wrap(neighbours(id=pid, label=None, limit=200))
    kinds: dict[str, int] = {}
    for row in n["neighbours"]:
        kinds[row["label"]] = kinds.get(row["label"], 0) + 1
    joined = ", ".join(f"{v} {k.lower()}{'s' if v != 1 else ''}" for k, v in sorted(kinds.items())) or "nothing yet"
    return {"text": f"{pid} is joined to {joined}. {d['summary']}", "data": {"neighbours": n["neighbours"], **d}}


TOOLS: list[Tool] = [
    Tool("connections", "How a saved place joins up in the knowledge graph (trips, roads, stations near it) and what is "
         "disrupting the trips from it: closures and flood warnings several steps away.",
         {"type": "object", "properties": _props(place=PLACE)}, connections_tool),
]
