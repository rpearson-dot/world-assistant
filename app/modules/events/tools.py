"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import (
    Tool,
    _props,
    _wrap,
)
from app.modules.events.events import near as events_near


async def events_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    place = str(args.get("place") or "") or None
    days = int(args.get("days") or 7)
    kind = str(args.get("type") or "").upper() or None
    res = await _wrap(
        events_near(request, place=place, radius_km=8.0, days=days, type=kind, keyword=None, limit=5)
    )
    ev = res["events"]
    where = res.get("place") or place
    if not ev:
        return {"text": f"I found no events near {where} in the next {days} days.", "data": res}
    parts = []
    for e in ev[:5]:
        when = (e.get("date") or "").strip()
        parts.append(f"{e.get('name') or 'an event'}" + (f" at {e['venue']}" if e.get("venue") else "")
                     + (f" on {when}" if when else ""))  # fmt: skip
    more = f", and {res['total'] - len(parts)} more" if res["total"] > len(parts) else ""
    return {
        "text": f"{res['total']} events near {where} in the next {days} days: " + "; ".join(parts) + more + ".",
        "data": res,
    }


TOOLS: list[Tool] = [
    Tool("events_near", "What's on near a saved place: gigs, club nights, comedy, theatre, sport and more.",
         {"type": "object", "properties": _props(
             place=("string", "id of a saved place; default: home"), days=("number", "how many days ahead, default 7"),
             type=("string", "optional: LIVE, CLUB, FEST, COMEDY, THEATRE, SPORT, KIDS, ARTS"))}, events_tool),
]
