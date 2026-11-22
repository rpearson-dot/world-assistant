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




def _near(args: dict[str, Any]) -> tuple[float, float] | None:
    """The caller's current position, if sent, to bias a place search towards nearby matches."""
    lat, lon = args.get("near_lat"), args.get("near_lon")
    if lat is None or lon is None:
        return None
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None


def _candidates(hits: list[Any]) -> list[dict[str, Any]]:
    return [{"label": h.label, "lat": h.lat, "lon": h.lon, "postcode": h.postcode, "kind": h.kind} for h in hits]


def _candidate_lines(hits: list[Any]) -> str:
    return "; ".join(f"{i + 1}) {h.label}" + (f" ({h.postcode})" if h.postcode else "") for i, h in enumerate(hits))


async def search_place_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.config import get_settings
    from app.geo import GeocodeError, search_places

    query = _need(args, "query")
    try:
        hits = await search_places(query, near=_near(args), base_url=get_settings().geocoder_url)
    except GeocodeError as exc:
        raise ToolError(f"Place search is unavailable right now ({exc}).") from exc
    if not hits:
        return {"text": f"I couldn't find anywhere matching {query!r}. Try adding the town or area.",
                "data": {"candidates": []}}  # fmt: skip
    return {"text": f"Found: {_candidate_lines(hits)}. To save one, add it as a place with its lat and lon.",
            "data": {"candidates": _candidates(hits)}}  # fmt: skip


async def add_place_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.config import get_settings
    from app.geo import GeocodeError, search_places
    from app.places import PlaceIn, put_place

    name = _need(args, "name")
    postcode = str(args.get("postcode") or "") or None
    lat, lon = args.get("lat"), args.get("lon")
    # No postcode and no coordinates: treat the name (or an explicit query) as a described place and look it up.
    if not postcode and (lat is None or lon is None):
        query = str(args.get("query") or name)
        try:
            hits = await search_places(query, near=_near(args), base_url=get_settings().geocoder_url)
        except GeocodeError as exc:
            raise ToolError(f"Place search is unavailable right now ({exc}). Give a postcode, or lat and lon.") from exc
        if not hits:
            raise ToolError(f"I couldn't find a place matching {query!r}. Add the town, or give a postcode.")
        if len(hits) > 1:
            return {"text": f"More than one match for {query!r}: {_candidate_lines(hits)}. "
                            "Say which, then add that one with its lat and lon.",
                    "data": {"candidates": _candidates(hits)}}  # fmt: skip
        hit = hits[0]
        lat, lon = hit.lat, hit.lon
        # The name was itself the search text (no separate label or id given): adopt the matched place's name.
        if not args.get("id") and not args.get("query"):
            name = hit.label.split(",")[0].strip() or name
    pid = _slug(str(args.get("id") or name))
    if not pid:
        raise ToolError("Give the place a name with a letter or digit in it.")
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
         {"type": "object", "properties": _props(count=("integer", "how many, default 3"))}, bank_holidays_tool,
         title="Next bank holidays", summary="The next UK bank holidays, and whether today is one.",
         phrases=("when's the next bank holiday", "is today a bank holiday"), kind="query"),
    Tool("search_place", "Find a described place (e.g. 'the O2 in London') and get a few candidates to choose from. "
         "Pass near_lat and near_lon (the caller's location) to prefer nearby matches. Nothing is saved.",
         {"type": "object", "properties": _props(query=("string", "what to look for, e.g. 'the O2 in London'"),
                                                 near_lat=("number", "the caller's latitude, to prefer nearby matches"),
                                                 near_lon=("number", "the caller's longitude")),
          "required": ["query"]}, search_place_tool,
         title="Find a place", summary="Searches for a described place and returns candidates to pick from.",
         phrases=("find {query}", "where is {query}"), kind="query"),
    Tool("add_place", "Save a new place. Give a postcode, or lat and lon, or just describe it (e.g. 'the O2 in "
         "London') and it is looked up; if more than one match, you are asked which. Pass near_lat/near_lon to prefer "
         "nearby matches.",
         {"type": "object", "properties": _props(name=("string", "what to call it, e.g. Mum's"), postcode=("string", "UK postcode"),
                                                 lat=("number", "latitude, instead of a postcode"), lon=("number", "longitude"),
                                                 query=("string", "describe the place to look up, if no postcode or coordinates"),
                                                 near_lat=("number", "the caller's latitude, to prefer nearby matches"),
                                                 near_lon=("number", "the caller's longitude"),
                                                 kind=("string", "home, work, school, family or other"),
                                                 id=("string", "optional id; default made from the name"),
                                                 replace=("boolean", "change a place already saved under that id")),
          "required": ["name"]}, add_place_tool,
         title="Save a place", summary="Saves a new place by postcode, coordinates or a described name, so other features can use it.",
         phrases=("save {name} as a place", "add a place called {name}"), kind="action"),
    Tool("add_trip", "Save a trip between two saved places and route it, for road conditions, chargers along it and cost.",
         {"type": "object", "properties": _props(from_place=("string", "id of the start place"), to_place=("string", "id of the end place"),
                                                 name=("string", "optional name"), id=("string", "optional id")),
          "required": ["from_place", "to_place"]}, add_trip_tool,
         title="Save a trip", summary="Saves and routes a trip between two saved places, for road conditions, chargers and cost.",
         phrases=("save a trip from {from_place} to {to_place}", "add a trip to {to_place}"), kind="action"),
]

