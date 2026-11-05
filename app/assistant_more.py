"""More assistant tools: shopping, floods, London roads, live buses and bus disruptions, stops, the graph, and adding
places and trips. They join the tools in app/assistant.py (served over MCP at /mcp and to Siri at /v1/ask).

Some act, not just answer: add_to_list, price_list, send_to_basket, tick_off_order, clear_list, add_place and add_trip. None of them spends money:
the basket is filled, never checked out; places and trips are World Assistant's own records.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import text

from app.assistant_base import Tool, ToolError, _miles, _need, _props, _wrap
from app.places import place_or_default

LABEL = {"sainsburys": "Sainsbury's", "ocado": "Ocado"}


def _money(v: float) -> str:
    return f"£{v:.2f}"


async def _point(request: Request, place: str | None) -> tuple[str, float, float]:
    """A saved place's name and position (the default place when none is given)."""
    pid = await _wrap(place_or_default(request, place or None))
    async with request.app.state.engine.connect() as conn:
        row = (await conn.execute(text("SELECT name, lat, lon FROM place WHERE id = :p"), {"p": pid})).first()
    if row is None:
        raise ToolError(f"No saved place {pid!r}. Try list_options.")
    return str(row[0]), float(row[1]), float(row[2])


def _retailer(args: dict[str, Any]) -> str:
    r = str(args.get("retailer") or "").strip().lower().replace("'", "").replace(" ", "")
    r = {"sainsbury": "sainsburys", "sainsburys": "sainsburys", "ocado": "ocado"}.get(r, r)
    if r not in LABEL:
        raise ToolError("Say which shop: sainsburys or ocado.")
    return r


# --- shopping ----------------------------------------------------------------------------------------------------


def price_text(res: dict[str, Any]) -> str:
    t = res["totals"]
    bits = []
    for r in res["retailers"]:
        x = t["by_retailer"][r]
        bits.append(f"{x['label']} {_money(x['total'])} for {x['items']} item(s)" + (f", {x['missing']} not found" if x["missing"] else ""))
    out = "; ".join(bits) + "."
    if t.get("cheaper"):
        out += f" {LABEL[t['cheaper']]} is cheaper by {_money(t['saving'])} on the {t['compared_items']} item(s) both have."
    elif len(res["retailers"]) > 1 and t.get("compared_items"):
        out += " They come to the same on the items both have."
    return out


async def shopping_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.shopping import LAST_KEY, _get, current_items

    items = await _wrap(current_items(request))
    if str(args.get("about") or "list") == "prices":
        last = await _get(request, LAST_KEY)
        if not last:
            return {"text": "The list has not been priced yet: use price_list.", "data": {"items": items}}
        return {"text": f"Priced at {last['priced_at'][11:16]} UTC on {last['priced_at'][:10]}: " + price_text(last), "data": last}
    if not items:
        return {"text": "The shopping list is empty.", "data": {"items": []}}
    names = [i["name"] + (f" ({i['description']})" if i.get("description") else "") for i in items]
    return {"text": f"{len(items)} item(s) on the shopping list: " + ", ".join(names) + ".", "data": {"items": items}}


async def add_to_list_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.shopping import add_item

    raw = _need(args, "item")
    note = str(args.get("note") or "")
    # "milk, eggs and bread": one call, several items
    names = [n.strip() for n in re.split(r",|\band\b|\n", raw) if n.strip()] if args.get("split", True) else [raw]
    added, already = [], []
    for n in names[:20]:
        r = await _wrap(add_item(request, n, note if len(names) == 1 else ""))
        (added if r["added"] else already).append(r["item"]["name"])
    parts = []
    if added:
        parts.append("Added " + ", ".join(added) + " to the shopping list.")
    if already:
        parts.append(", ".join(already) + (" is" if len(already) == 1 else " are") + " already on it.")
    return {"text": " ".join(parts), "data": {"added": added, "already": already}}


async def price_list_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.shopping import PriceIn, price

    res = await _wrap(price(PriceIn(refresh=bool(args.get("fresh"))), request))
    return {"text": price_text(res), "data": {"priced_at": res["priced_at"], "totals": res["totals"]}}


async def send_to_basket_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.shopping import send_to_basket

    r = _retailer(args)
    out = await _wrap(send_to_basket(r, request))
    res = out.get("results", [])
    counts = {s: sum(1 for x in res if x.get("status") == s) for s in ("added", "raised", "already", "failed")}
    words = {"added": "added", "raised": "topped up", "already": "already there", "failed": "failed"}
    said = ", ".join(f"{n} {words[s]}" for s, n in counts.items() if n)
    tail = f" The {LABEL[r]} basket now has {out['basket']['count']} items, {_money(out['basket']['total'])}." if out.get("basket") else ""
    failed = [x.get("item") or x.get("name") for x in res if x.get("status") == "failed"]
    fail = (" Not added: " + ", ".join(failed) + ".") if failed else ""
    return {"text": f"{LABEL[r]} basket: {said}.{fail}{tail} Nothing is ordered: check out on their site or app.", "data": out}


def _order_line(o: dict[str, Any]) -> str:
    when = (o.get("slot_start") or "")[:10] or "no date"
    return f"{when}, {(o.get('status') or '').lower().replace('_', ' ')}, {_money(o.get('total') or 0)}"


async def orders_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.shopping import orders

    r = _retailer(args)
    d = await _wrap(orders(r, request))
    os_ = d.get("orders") or []
    if not os_:
        return {"text": f"No recent {LABEL[r]} orders" + (" delivered yet." if r == "ocado" else "."), "data": d}
    bits = []
    for o in os_[:3]:
        on = o.get("on_list") or []
        bits.append(_order_line(o) + (f", covers {len(on)} item(s) on the list ({', '.join(i['name'] for i in on[:6])})" if on else ""))
    return {"text": f"Recent {LABEL[r]} orders: " + "; ".join(bits) + ".", "data": d}


async def tick_off_order_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.shopping import KeysIn, orders, tick_off

    r = _retailer(args)
    d = await _wrap(orders(r, request))
    os_ = d.get("orders") or []
    want = str(args.get("order") or "")
    o = next((x for x in os_ if x["id"] == want), None) if want else (os_[0] if os_ else None)
    if o is None:
        raise ToolError(f"No {LABEL[r]} order " + (f"{want!r}" if want else "to tick off") + ". Try orders first.")
    keys = [i["key"] for i in o.get("on_list") or [] if args.get("include_name_matches", True) or i["how"] == "product"]
    if not keys:
        return {"text": f"Nothing on the list is in the {LABEL[r]} order of {_order_line(o)}.", "data": {"order": o}}
    res = await _wrap(tick_off(KeysIn(item_keys=keys), request))
    return {"text": f"Took {res['removed']} item(s) off the list, from the {LABEL[r]} order of {_order_line(o)}: "
                    + ", ".join(res["items"]) + ".", "data": {"order": o, **res}}  # fmt: skip


async def clear_list_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.shopping import ClearIn, clear_list

    if args.get("confirm") is not True:
        raise ToolError("Clearing the whole list needs confirm: true. Check with the person first.")
    res = await _wrap(clear_list(ClearIn(confirm=True), request))
    return {"text": f"Cleared the shopping list: {res['removed']} item(s) taken off. They stay in KitchenOwl's recent items.",
            "data": res}


# --- around a place ----------------------------------------------------------------------------------------------


async def floods_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.floods import at

    name, lat, lon = await _point(request, args.get("place"))
    km = float(args.get("km") or 0)
    r = await _wrap(at(request, place=None, lat=lat, lon=lon, within_km=km))
    where = f"{name}" if not km else f"within {km:g} km of {name}"
    if not r["count"]:
        return {"text": f"No flood warnings or alerts cover {where}.", "data": r}
    bits = [f"{w['severity'] or 'warning'} for {w['area']}" for w in r["warnings"][:4]]
    return {"text": f"{r['count']} flood warning(s) cover {where}: " + "; ".join(bits) + ".", "data": r}


async def road_disruptions_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.tflroads import at

    name, lat, lon = await _point(request, args.get("place"))
    km = float(args.get("km") or 0.5)
    r = await _wrap(at(request, place=None, lat=lat, lon=lon, radius_m=max(10.0, min(20000.0, km * 1000))))
    if not r["count"]:
        return {"text": f"No TfL road disruptions within {km:g} km of {name}.", "data": r}
    bits = []
    for d in r["disruptions"][:4]:
        what = d["sub_category"] or d["category"] or "disruption"
        bits.append(f"{what}{' (closure)' if d['closure'] else ''} at {d['location'] or 'a road'}, {d['metres']} m away")
    return {"text": f"{r['count']} TfL road disruption(s) near {name}: " + "; ".join(bits) + ".", "data": r}


async def live_buses_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.bods import live

    name, lat, lon = await _point(request, args.get("place"))
    km = float(args.get("km") or 1)
    line = str(args.get("line") or "") or None
    r = await _wrap(live(request, place=None, lat=lat, lon=lon, radius_km=km, line=line, limit=50))
    if not r["count"]:
        return {"text": f"No buses{' on the ' + line if line else ''} reporting within {_miles(km)} of {name} right now.", "data": r}
    bits = [f"{b['line'] or 'a bus'}" + (f" to {b['destination']}" if b.get("destination") else "") + f", {_miles(b['km'])} away"
            for b in r["buses"][:5]]  # fmt: skip
    return {"text": f"{r['count']} bus(es) near {name} now: " + "; ".join(bits) + ".", "data": r}


async def bus_disruptions_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.bus_disruptions import near

    place = str(args.get("place") or "") or None
    line = str(args.get("line") or "") or None
    r = await _wrap(near(request, place=place, line=line, within_km=float(args["km"]) if args.get("km") else None, limit=20))
    if not r["count"]:
        return {"text": "No bus disruptions published" + (f" for the {line}" if line else "") + " near your places.", "data": r}
    bits = [(d["summary"] or d["reason"] or "disruption") + (f" (lines {', '.join(d['lines'][:4])})" if d["lines"] else "")
            for d in r["disruptions"][:4]]  # fmt: skip
    return {"text": f"{r['count']} bus disruption(s): " + "; ".join(bits) + ".", "data": r}


async def stops_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.stops import near

    name, lat, lon = await _point(request, args.get("place"))
    km = float(args.get("km") or 0.5)
    kind = str(args.get("kind") or "") or None
    r = await _wrap(near(request, lat=lat, lon=lon, radius_km=km, kind=kind, limit=10))
    if not r["stops"]:
        return {"text": f"No {kind.replace('_', ' ') if kind else 'stops'} within {km:g} km of {name}.", "data": r}
    bits = [f"{s['name']}" + (f" ({s['indicator']})" if s.get("indicator") else "") + f", {round(s['km'] * 1000)} m"
            for s in r["stops"][:6]]  # fmt: skip
    return {"text": f"Nearest {kind.replace('_', ' ') + 's' if kind else 'stops'} to {name}: " + "; ".join(bits) + ".", "data": r}


async def connections_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.graph_routes import disruption, neighbours

    pid = await _wrap(place_or_default(request, args.get("place") or None))
    d = await _wrap(disruption(place=pid, limit=50))
    n = await _wrap(neighbours(id=pid, label=None, limit=200))
    kinds: dict[str, int] = {}
    for row in n["neighbours"]:
        kinds[row["label"]] = kinds.get(row["label"], 0) + 1
    joined = ", ".join(f"{v} {k.lower()}{'s' if v != 1 else ''}" for k, v in sorted(kinds.items())) or "nothing yet"
    return {"text": f"{pid} is joined to {joined}. {d['summary']}", "data": {"neighbours": n["neighbours"], **d}}


async def postcode_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.postcodes import near, one

    if args.get("postcode"):
        r = await _wrap(one(request, str(args["postcode"])))
        return {"text": f"{r['postcode']} is at {r['lat']:.5f}, {r['lon']:.5f}.", "data": r}
    name, lat, lon = await _point(request, args.get("place"))
    r = await _wrap(near(request, lat=lat, lon=lon, radius_km=0.5, limit=3))
    found = r.get("postcodes") or []
    if not found:
        return {"text": f"No postcode found within half a km of {name}.", "data": r}
    return {"text": f"The nearest postcode to {name} is {found[0]['postcode']}.", "data": r}


# --- adding ------------------------------------------------------------------------------------------------------


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]


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


PLACE = ("string", "id of a saved place; default: home")

MORE: list[Tool] = [
    Tool("shopping", "The family shopping list (KitchenOwl): what is on it, or what it came to at Sainsbury's and Ocado when last priced.",
         {"type": "object", "properties": _props(about=("string", "list (default) or prices"))}, shopping_tool),
    Tool("add_to_list", "Add items to the family shopping list (KitchenOwl). Several at once: 'milk, eggs and bread'.",
         {"type": "object", "properties": _props(item=("string", "what to add; commas or 'and' for several"),
                                                 note=("string", "optional note for a single item, e.g. 2 or semi-skimmed")),
          "required": ["item"]}, add_to_list_tool),
    Tool("price_list", "Price the shopping list now at Sainsbury's and Ocado: each total and which is cheaper. Takes up to a "
         "minute or two for a long list; searches are reused for six hours.",
         {"type": "object", "properties": _props(fresh=("boolean", "search again even if prices are under six hours old"))},
         price_list_tool),
    Tool("send_to_basket", "Put the last priced list's products in a shop's basket, each up to the quantity on the list "
         "(topped up, never doubled). Nothing is ordered or paid: you check out on their site or app.",
         {"type": "object", "properties": _props(retailer=("string", "sainsburys or ocado")), "required": ["retailer"]},
         send_to_basket_tool),
    Tool("orders", "Recent orders at a shop (Sainsbury's: upcoming ones too; Ocado: delivered ones only) and which items "
         "on the shopping list each covers.",
         {"type": "object", "properties": _props(retailer=("string", "sainsburys or ocado")), "required": ["retailer"]}, orders_tool),
    Tool("tick_off_order", "Take the items an order covers off the shopping list (the newest order, or the one given).",
         {"type": "object", "properties": _props(retailer=("string", "sainsburys or ocado"), order=("string", "order id; default: the newest"),
                                                 include_name_matches=("boolean", "also items matched only by name, default true")),
          "required": ["retailer"]}, tick_off_order_tool),
    Tool("clear_list", "Empty the whole shopping list. Only with confirm: true, after checking with the person.",
         {"type": "object", "properties": _props(confirm=("boolean", "must be true")), "required": ["confirm"]}, clear_list_tool),
    Tool("floods_near", "Environment Agency flood warnings and alerts covering a saved place (or within some km of it).",
         {"type": "object", "properties": _props(place=PLACE, km=("number", "0 (default): the place must be inside the warned area"))},
         floods_tool),
    Tool("road_disruptions_near", "TfL road works, closures and incidents near a saved place in London.",
         {"type": "object", "properties": _props(place=PLACE, km=("number", "how far, default 0.5"))}, road_disruptions_tool),
    Tool("live_buses_near", "Buses reporting their position near a saved place right now, anywhere in Great Britain.",
         {"type": "object", "properties": _props(place=PLACE, km=("number", "how far, default 1"),
                                                 line=("string", "only this line, e.g. 213"))}, live_buses_tool),
    Tool("bus_disruptions", "Published bus disruptions near your saved places: diversions, closed stops, changes.",
         {"type": "object", "properties": _props(place=("string", "only those nearest this saved place"),
                                                 line=("string", "only those affecting this line"),
                                                 km=("number", "only within this distance of the place"))}, bus_disruptions_tool),
    Tool("stops_near", "The nearest bus stops, stations and other stops to a saved place (NaPTAN).",
         {"type": "object", "properties": _props(place=PLACE, km=("number", "how far, default 0.5"),
                                                 kind=("string", "optional: bus_stop, bus_station, rail, metro, ferry or air"))},
         stops_tool),
    Tool("connections", "How a saved place joins up in the knowledge graph (trips, roads, stations near it) and what is "
         "disrupting the trips from it: closures and flood warnings several steps away.",
         {"type": "object", "properties": _props(place=PLACE)}, connections_tool),
    Tool("postcode", "Where a UK postcode is, or the nearest postcode to a saved place (from the local Code-Point Open table).",
         {"type": "object", "properties": _props(postcode=("string", "a UK postcode, e.g. SW1A 2AA"), place=PLACE)},
         postcode_tool),
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
