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


async def ev_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.gridserve.ev import along_trip, car_connector, sites

    connector = args.get("connector") or None
    free = bool(args.get("free_only"))
    note = ""
    if not connector:  # the car's own, when you have said which it takes
        connector, whose = await _wrap(car_connector(request.app.state.engine, args.get("vehicle") or None))
        note = f" (for {whose}'s {connector}; ask for any connector to see all)" if connector else ""
    lead = f"For {note[6:-1]}: " if note else ""
    if args.get("trip"):
        r = await _wrap(along_trip(request, str(args["trip"]), within_km=float(args.get("km") or 2), connector=connector,
                                   free=free, vehicle=None))  # fmt: skip
        if not r["count"]:
            return {"text": f"No GRIDSERVE chargers within {r['within_km']:g} km of {r['trip']['name']}'s route.", "data": r}
        return {"text": f"{r['count']} GRIDSERVE site(s) along {r['trip']['name']}{note}: "
                        + " ".join(x["summary"] for x in r["sites"][:6]), "data": r}  # fmt: skip
    q = args.get("name") or None
    r = await _wrap(sites(request, place=args.get("place"), km=float(args.get("km") or 25), connector=connector, free=free,
                          vehicle=None, q=q, limit=5))  # fmt: skip
    if not r["count"]:
        where = f"called {q!r}" if q else f"within {r['km']:g} km"
        return {"text": f"No GRIDSERVE chargers {where}" + (f" with {r['connector']}" if r["connector"] else "") + ".",
                "data": r}  # fmt: skip
    if q:  # asked about a site by name: how reliable it has been too
        return {"text": lead + r["sites"][0]["summary"] + " " + r["sites"][0]["reliability_summary"]
                + "".join(" " + x["summary"] for x in r["sites"][1:3]), "data": r}  # fmt: skip
    return {"text": lead + " ".join(x["summary"] for x in r["sites"][:5]), "data": r}


TOOLS: list[Tool] = [
    Tool("ev_chargers", "GRIDSERVE EV chargers near a saved place, along a trip, or by name: how many work and are free "
         "now, by connector (CCS, CHAdeMO, Type 2), how fast, and the price per kWh.",
         {"type": "object", "properties": _props(
             place=("string", "id of a saved place; default: home"), trip=("string", "id of a trip: chargers along its route"),
             name=("string", "part of a site's name, town or postcode, e.g. Rugby"),
             connector=("string", "ccs, chademo, type2, or any; default: the car's own (set on the Vehicles page)"),
             vehicle=("string", "id of a saved vehicle whose connector to use"),
             free_only=("boolean", "only sites with a free charger now"),
             km=("number", "near a place: how far, default 25; along a trip: how far off the route, default 2"))}, ev_tool),
]
