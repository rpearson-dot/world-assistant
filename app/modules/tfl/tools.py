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
from app.modules.tfl.tfl import board_departures, resolve_board


async def trains(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    # The "board" may be a saved board's id or name, a CRS code (SNL), or a station name (Stoneleigh) resolved live.
    resolved = await _wrap(resolve_board(request, _need(args, "board")))
    if "candidates" in resolved:
        which = "; ".join(f"{c['name']} ({c['id']})" for c in resolved["candidates"])
        return {"text": f"More than one station matches that — which one: {which}? "
                        "Say the three-letter code to be sure.", "data": resolved}  # fmt: skip
    r = await _wrap(board_departures(request, resolved))
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
    Tool("trains", "Next trains from a saved board, or from any station by name or code — National Rail anywhere in "
         "Great Britain (e.g. Stoneleigh, Waterloo, SNL) and London Tube, DLR, Overground and Elizabeth line (e.g. "
         "Oxford Circus). Stations are looked up live. If a name matches more than one station, it asks which.",
         {"type": "object", "properties": _props(
             board=("string", ("a saved board's id or name, or a station name or CRS code, "
                               "e.g. 'Stoneleigh', 'Waterloo', 'SNL' or 'Oxford Circus'")),
             count=("integer", "how many, default 3")),
          "required": ["board"]}, trains,
         title="Next trains", summary="Next trains from a saved board or any National Rail, Tube, DLR, Overground or Elizabeth line station.",
         phrases=("next trains from {board}", "when's my train", "next train from {board}"), kind="query"),
]
