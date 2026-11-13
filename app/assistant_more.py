"""More of the core's assistant tools: bank holidays, and adding places and trips. They join the tools in
app/assistant.py (served over MCP at /mcp and to Siri at /v1/ask). Each module's own tools are in its folder
(app/modules/<id>/tools.py) and are gathered from the registry.

Some act, not just answer: add_to_list, price_list, send_to_basket, tick_off_order, clear_list, add_place and add_trip. None of them spends money:
the basket is filled, never checked out; places and trips are World Assistant's own records.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import text

from app.assistant_base import Tool, ToolError, _need, _props, _slug, _wrap

# --- adding ------------------------------------------------------------------------------------------------------




async def add_place_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.places import PlaceIn, put_place

    name = _need(args, "name")
    pid = _slug(str(args.get("id") or name))
    if not pid:
        raise ToolError("Give the place a name with a letter or digit in it.")
    postcode = str(args.get("postcode") or "") or None
    lat, lon = args.get("lat"), args.get("lon")
    if not postcode and (lat is None or lon is None):
        raise ToolError("Give a postcode, or lat and lon.")
    async with request.app.state.engine.connect() as conn:
        exists = (await conn.execute(text("SELECT 1 FROM place WHERE id = :p"), {"p": pid})).first()
    if exists and not args.get("replace"):
        raise ToolError(f"A place {pid!r} is already saved. Say replace to change it, or give another name.")
    try:
        body = PlaceIn(name=name, kind=str(args.get("kind") or "other"), postcode=postcode,
                       lat=float(lat) if lat is not None else None, lon=float(lon) if lon is not None else None)  # fmt: skip
    except ValueError as exc:
        raise ToolError(f"That place is not right: {exc}") from exc
    r = await _wrap(put_place(pid, body, request))
    return {"text": f"Saved {name} as {pid}" + (f", at {r['postcode']}" if r.get("postcode") else "") + ".", "data": r}


async def add_trip_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.places import TripIn, build_route, put_trip

    a, b = _need(args, "from_place"), _need(args, "to_place")
    tid = _slug(str(args.get("id") or f"{a}-to-{b}"))
    r = await _wrap(put_trip(tid, TripIn(from_place=a, to_place=b, name=args.get("name") or None), request))
    try:
        route = await build_route(tid, request)
        extra = (f" Routed: {route.get('distance_km', 0):.0f} km." if isinstance(route, dict) and route.get("distance_km")
                 else " Routed.")  # fmt: skip
    except HTTPException as exc:
        extra = f" It is saved, but not routed yet ({exc.detail}), so road conditions and costs wait for a route."
    return {"text": f"Saved the trip {r.get('name') or tid} as {tid}.{extra}", "data": r}






# --- Bank holidays ---------------------------------------------------------------------------------------------------
async def bank_holidays_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from datetime import date as _date

    from app.bankholidays import upcoming

    r = await _wrap(upcoming(request))
    nxt = r["next"][: int(args.get("count") or 3)]
    say = "; ".join(f"{_date.fromisoformat(x['date']).strftime('%A %-d %B %Y')}: {x['title']}" for x in nxt)
    head = f"Today is {r['today']}. " if r.get("today") else ""
    return {"text": head + (f"The next bank holidays: {say}." if say else "No bank holidays are listed ahead."), "data": r}


MORE: list[Tool] = [
    Tool("bank_holidays", "The next UK bank holidays (from GOV.UK), and whether today is one.",
         {"type": "object", "properties": _props(count=("integer", "how many, default 3"))}, bank_holidays_tool),
    Tool("add_place", "Save a new place (by postcode, or lat and lon), so the other tools can use it.",
         {"type": "object", "properties": _props(name=("string", "what to call it, e.g. Mum's"), postcode=("string", "UK postcode"),
                                                 lat=("number", "latitude, instead of a postcode"), lon=("number", "longitude"),
                                                 kind=("string", "home, work, school, family or other"),
                                                 id=("string", "optional id; default made from the name"),
                                                 replace=("boolean", "change a place already saved under that id")),
          "required": ["name"]}, add_place_tool),
    Tool("add_trip", "Save a trip between two saved places and route it, for road conditions, chargers along it and cost.",
         {"type": "object", "properties": _props(from_place=("string", "id of the start place"), to_place=("string", "id of the end place"),
                                                 name=("string", "optional name"), id=("string", "optional id")),
          "required": ["from_place", "to_place"]}, add_trip_tool),
]

