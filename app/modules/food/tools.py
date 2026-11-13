"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import Tool, ToolError, _point_of, _props


# --- Food standards --------------------------------------------------------------------------------------------------
async def food_hygiene_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.food.food import FoodError, hygiene, hygiene_words

    lat, lon = args.get("lat"), args.get("lon")
    if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))):
        lat, lon = await _point_of(request, args)
    try:
        d = await hygiene(float(lat), float(lon), float(args.get("miles") or 0.5), args.get("name") or None, 10)
    except FoodError as exc:
        raise ToolError(str(exc)) from exc
    return {"text": hygiene_words(d), "data": d}


async def food_alerts_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.food.food import FoodError, alerts_words, check, get_alerts

    if args.get("check"):
        try:
            await check(request)
        except FoodError as exc:
            raise ToolError(str(exc)) from exc
    d = await get_alerts(request, 20)
    latest = "; ".join(f"{a['created']}: {a['title']}" for a in d["alerts"][:5])
    return {"text": alerts_words({"alerts": d["alerts"]}) + (f" Latest: {latest}." if latest else ""), "data": d}


TOOLS: list[Tool] = [
    Tool("food_hygiene", "Food hygiene ratings (0 to 5, from the Food Standards Agency) of restaurants, takeaways, "
         "shops and other food businesses near a place, nearest first; give a name to look for one.",
         {"type": "object", "properties": _props(lat=("number", "latitude"), lon=("number", "longitude"),
                                                 postcode=("string", "a UK postcode instead"),
                                                 place=("string", "a saved place's name instead"),
                                                 name=("string", "the business's name, or part of it"),
                                                 miles=("number", "how far, default 0.5"))}, food_hygiene_tool),
    Tool("food_alerts", "The Food Standards Agency's latest allergy alerts and product recalls, and any that look like "
         "something on the shopping list or among the usual products (checked each morning).",
         {"type": "object", "properties": _props(check=("boolean", "check the FSA now first"))}, food_alerts_tool),
]
