"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import (
    KM_PER_MILE,
    Tool,
    _need,
    _props,
    _wrap,
)
from app.modules.vehicles.vehicles import trip_cost


async def trip_cost_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    trip, vehicle = _need(args, "trip"), _need(args, "vehicle")
    r = await _wrap(trip_cost(trip, request, vehicle, 2.0))
    miles = r["distance_km"] / KM_PER_MILE
    if r.get("kind") == "ev":
        t = f"{r['trip']} is {miles:.0f} miles, about {r['kwh']:.0f} kilowatt hours in the {r['vehicle']}."
        t += (
            " It makes it on one charge."
            if r["one_way_on_a_full_charge"]
            else " That is more than one charge."
        )
        if not r["return_on_a_full_charge"] and r["one_way_on_a_full_charge"]:
            t += " The return needs a charge on the way."
        if "cost_gbp_at_home_rate" in r:
            where = "the cheapest rate on your tariff" if r.get("home_rate_from", "yours") != "yours" else "your home rate"
            t += f" About {r['cost_gbp_at_home_rate']:.2f} pounds at {where}."
    else:
        t = f"{r['trip']} is {miles:.0f} miles, about {r['litres']:.0f} litres in the {r['vehicle']}."
        if "cost_gbp_typical" in r:
            t += (f" Around {r['cost_gbp_typical']:.2f} pounds at typical prices on the route, "
                  f"or {r['cost_gbp_cheapest']:.2f} at the cheapest station.")  # fmt: skip
        elif "cost_gbp_at_own_price" in r:
            t += f" About {r['cost_gbp_at_own_price']:.2f} pounds at your price of {r['own_pence_per_litre']:.0f}p a litre."
        elif r.get("note"):
            t += " " + r["note"]
    return {"text": t, "data": r}


TOOLS: list[Tool] = [
    Tool("trip_cost", "Fuel or energy needed, and cost, for a saved routed trip in a saved vehicle.",
         {"type": "object", "properties": _props(trip=("string", "trip id"), vehicle=("string", "vehicle id")),
          "required": ["trip", "vehicle"]}, trip_cost_tool),
]
