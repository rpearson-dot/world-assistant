"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import (
    Tool,
    _miles,
    _props,
    _wrap,
)
from app.modules.fuel.fuel import near as fuel_near


async def fuel_cheapest(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    place = str(args.get("place") or "") or None
    fuel = str(args.get("fuel") or "E10").upper()
    radius = float(args.get("radius_km") or 5)
    brands = str(args.get("brands") or "") or None
    supers = bool(args.get("supermarkets"))
    res = await _wrap(fuel_near(request, place=place, lat=None, lon=None, radius_km=radius, fuel=fuel,
                                limit=3, max_age_hours=48.0, brands=brands,
                                supermarkets=supers, fill_litres=40.0))  # fmt: skip
    st = res["stations"]
    if not st:
        return {
            "text": f"No {fuel} prices found within {_miles(radius)} of {res.get('place') or place}.",
            "data": res,
        }
    lines = []
    for i, s in enumerate(st):
        who = s["brand"] or s["name"] or "a station"
        where = f", {s['city']}" if s.get("city") else ""
        lines.append(f"{who}{where}, {s['pence']:.1f} pence, {_miles(s['distance_km'])} away")
        if i == 0 and len(st) > 1:
            lines[0] = "cheapest is " + lines[0]
    text_out = f"{fuel} near {res.get('place') or place}: " + "; ".join(lines) + "."
    c = res.get("comparison")
    if c and not c["same_station"]:
        a = c["cheapest_overall"]
        text_out += (
            f" The absolute cheapest is {a['brand'] or a['name']} at {a['pence']:.1f} pence;"
            f" the selected brands cost {c['extra_pence_per_litre']:.1f} pence a litre more."
        )
    return {"text": text_out, "data": res}


TOOLS: list[Tool] = [
    Tool("fuel_cheapest", "Cheapest fuel near a saved place.",
         {"type": "object", "properties": _props(
             place=("string", "id of a saved place; default: home"), fuel=("string", "E10 (default), E5, B7_STANDARD, B7_PREMIUM"),
             radius_km=("number", "search radius in km, default 5"),
             brands=("string", "comma-separated brands to limit to, e.g. Tesco,Asda"),
             supermarkets=("boolean", "limit to supermarkets"))}, fuel_cheapest),
]
