"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import (
    Tool,
    _need,
    _props,
    _wrap,
)
from app.modules.tfl.tfl import departures as board_departures


async def trains(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    r = await _wrap(board_departures(_need(args, "board"), request))
    deps = r["departures"][: int(args.get("count") or 3)]
    if not deps:
        return {"text": f"No upcoming trains found on {r['board']}.", "data": r}
    bits = []
    for d in deps:
        mins = d.get("minutes")
        when = (
            "now"
            if mins is not None and mins <= 0
            else f"in {mins} minutes"
            if mins is not None
            else ""
        )
        extra = ", cancelled" if d.get("status") == "Cancelled" else (
            f", {d['delay_minutes']} minutes late" if (d.get("delay_minutes") or 0) > 0 else "")  # fmt: skip
        plat = f", platform {d['platform']}" if d.get("platform") else ""
        bits.append(f"{d.get('destination') or 'a train'} {when}{plat}{extra}".strip())
    return {"text": f"{r['board']}: " + "; ".join(bits) + ".", "data": r}


TOOLS: list[Tool] = [
    Tool("trains", "Next trains on a saved departure board.",
         {"type": "object", "properties": _props(board=("string", "board id"),
                                                 count=("integer", "how many, default 3")),
          "required": ["board"]}, trains),
]
