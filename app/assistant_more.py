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


async def air_quality_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.environment import air

    r = await _wrap(air(request, place=args.get("place") or None, km=float(args.get("km") or 20)))
    if r.get("index") is None:
        return {"text": f"No air quality reading near {r['name']}: {r.get('note', 'none found')}.", "data": r}
    bits = [f"{x['pollutant']} {x['mean']:g} {x['unit']}" + (f" ({x['mean_hours']}-hour mean)" if x["mean_hours"] > 1 else "")
            + f", index {x['index']}" for x in r["readings"].values()]  # fmt: skip
    nearest = min(r["readings"].values(), key=lambda x: x["station_km"])
    return {"text": f"Air quality near {r['name']}: {r['band'].lower()}, index {r['index']} of 10 (worst: {r['worst']}). "
                    + "; ".join(bits) + f". Nearest station: {nearest['station']}, {nearest['station_km']:g} km.", "data": r}  # fmt: skip


async def river_levels_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.environment import rain, rivers

    place = args.get("place") or None
    rv = await _wrap(rivers(request, place=place, km=float(args.get("km") or 5)))
    rn = await _wrap(rain(request, place=place, km=10))
    parts = []
    for x in rv["rivers"][:3]:
        lvl = f"{x['level_m']:.2f} m" if x["level_m"] is not None else "no reading"
        what = f"{x['river'] or x['station']} at {x['station']}: {lvl}"
        if x["state"]:
            what += f", {x['state']} for there"
        if x["trend"]:
            what += f", {x['trend']}"
        parts.append(what)
    text_ = (f"Rivers near {rv['name']}: " + "; ".join(parts) + ".") if parts else f"No river gauge near {rv['name']}."
    if rn["gauges"]:
        g = rn["gauges"][0]
        text_ += f" Rain at {g['gauge']}: {g['last_hour_mm']:g} mm in the last hour, {g['last_24h_mm']:g} mm in 24 hours."
    return {"text": text_, "data": {"rivers": rv, "rain": rn}}


async def tides_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.environment import tides

    r = await _wrap(tides(request, place=args.get("place") or None, km=float(args.get("km") or 20)))
    if not r.get("gauge"):
        return {"text": f"No tide gauge near {r['name']}.", "data": r}
    tr = f", {r['trend']}" if r.get("trend") else ""
    return {"text": f"The tide gauge at {r['gauge']} ({r['km']:g} km from {r['name']}) reads {r['level_m']:.2f} {r['unit']}{tr}. "
                    "That is the measured level, not a forecast.", "data": r}  # fmt: skip


RIVER_LIST = {"type": "array", "items": {"type": "string"},
              "description": "river names, e.g. [\"Hogsmill\", \"Thames\"]; any part of the name matches"}


async def storm_overflows_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.environment import overflows

    rv = args.get("rivers")
    rivers = ",".join(rv) if isinstance(rv, list) else (str(rv) if rv else None)
    r = await _wrap(overflows(request, place=args.get("place") or None, km=float(args.get("km") or 3), rivers=rivers,
                              river_km=15))  # fmt: skip
    where = f"within {r['km']:g} km of {r['name']}" + (f" or on the {', '.join(r['rivers'])} within 15 km" if r["rivers"] else "")
    if not r["count"]:
        return {"text": f"No storm overflows {where}.", "data": r}

    def say(o: dict[str, Any]) -> str:
        on = f" into the {o['river']}" if o.get("river") else ""
        return f"{o['name']}{on} ({o['km']:g} km)"

    now = r["discharging"]
    parts = [f"{len(now)} of {r['count']} storm overflows {where} {'is' if len(now) == 1 else 'are'} discharging now"
             + (": " + "; ".join(say(o) + (f", for {o['hours']:g} hours" if o.get("hours") is not None else "") for o in now[:5]) if now else "")]  # fmt: skip
    if r["recent"]:
        parts.append(f"{len(r['recent'])} more did in the last 48 hours: " + "; ".join(say(o) for o in r["recent"][:5]))
    if r["offline"]:
        parts.append(f"{r['offline']} {'monitor is' if r['offline'] == 1 else 'monitors are'} offline, so could be spilling unseen")
    return {"text": ". ".join(parts) + ". (Thames Water's monitors, via the National Storm Overflow Hub.)", "data": r}


async def watch_rivers_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.environment import Rivers, put_rivers

    rv = args.get("rivers") or []
    r = await _wrap(put_rivers(request, Rivers(rivers=[str(x) for x in (rv if isinstance(rv, list) else [rv])])))
    if not r["rivers"]:
        return {"text": "Not watching any rivers; storm overflows near home are still reported.", "data": r}
    return {"text": f"Watching storm overflows on the {', '.join(r['rivers'])} (within 15 km of home).", "data": r}


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


# --- Setting up a bus tracker --------------------------------------------------------------------------------------
_HM = re.compile(r"^([01]?\d|2[0-3]):?([0-5]\d)$")


def _hhmm(v: Any, what: str) -> str | None:
    if v in (None, ""):
        return None
    m = _HM.match(str(v).strip().replace(".", ":"))
    if not m:
        raise ToolError(f"{what} should be a time like 08:30, not {v!r}")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _stop_matches(query: str, stops: list[dict[str, Any]]) -> list[int]:
    """Positions of the stops a name means: an exact name first, else every stop containing all its words."""
    from app.integrations.bus.setup import norm

    q = norm(query)
    exact = [i for i, s in enumerate(stops) if norm(s["name"]) == q or s["id"] == query.strip()]
    if exact:
        return exact
    words = q.split()
    return [i for i, s in enumerate(stops) if words and all(w in norm(s["name"]).split() or w in norm(s["name"]) for w in words)]


def _journey(dirs: dict[str, Any], board: str, target: str) -> tuple[str, dict[str, Any], dict[str, Any]] | str:
    """(direction, board stop, target stop) with the board stop before the target, or what went wrong."""
    seen: dict[str, list[str]] = {"board": [], "target": []}
    for d, v in dirs.items():
        stops = v["stops"]
        b, t = _stop_matches(board, stops), _stop_matches(target, stops)
        seen["board"] += [stops[i]["name"] for i in b]
        seen["target"] += [stops[i]["name"] for i in t]
        pairs = [(i, j) for i in b for j in t if i < j]
        if pairs:
            i, j = min(pairs, key=lambda p: p[1] - p[0])
            return d, stops[i], stops[j]
    for key, q in (("board", board), ("target", target)):
        if not seen[key]:
            return f"No stop on this line matches {q!r}."
    return f"{board!r} does not come before {target!r} in either direction."


def _stop_list(line: str, dirs: dict[str, Any]) -> str:
    return " ".join(f"Towards {v['towards']}: " + ", ".join(s["name"] for s in v["stops"]) + "." for v in dirs.values()) \
        or f"No stops found for the {line}."  # fmt: skip


async def bus_setup_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from pydantic import ValidationError

    from app.bus import SetupIn, line_stops, setup
    from app.integrations.bus import store

    line = _need(args, "line")
    got = await _wrap(line_stops(request, line, place=args.get("place") or None, lat=None, lon=None))
    dirs = {d: v for d, v in got.items() if not d.startswith("_")}
    board, target = str(args.get("board") or "").strip(), str(args.get("to") or "").strip()
    if not board or not target:
        return {"text": f"The {line}'s stops. {_stop_list(line, dirs)} Say where you get on and where you are going.",
                "data": got}  # fmt: skip
    found = _journey(dirs, board, target)
    if isinstance(found, str):
        return {"text": f"{found} {_stop_list(line, dirs)}", "data": got}
    direction, b, t = found
    deadline = _hhmm(args.get("arrive_by"), "arrive_by")
    if not deadline:
        raise ToolError("Say what time you need to arrive by (arrive_by, e.g. 08:30).")
    earliest = _hhmm(args.get("earliest"), "earliest")
    if not earliest:
        h, m = divmod(max(int(deadline[:2]) * 60 + int(deadline[3:]) - 60, 0), 60)
        earliest = f"{h:02d}:{m:02d}"
    back_e, back_d = _hhmm(args.get("return_earliest"), "return_earliest"), _hhmm(args.get("return_arrive_by"), "return_arrive_by")
    name = str(args.get("name") or re.sub(r"[^a-z0-9]+", "-", f"bus-{line}".lower()).strip("-"))
    async with request.app.state.engine.connect() as conn:
        existing = {r["id"] for r in await store.list_trackers(conn)}
    clash = [n for n in (name, f"{name}-return" if back_e else None) if n and n in existing]
    if clash and not args.get("replace"):
        raise ToolError(f"A tracker called {clash[0]!r} already exists. Give another name, or replace=true to replace it.")
    try:
        body = SetupIn(line=line, board=b["id"], target=t["id"], earliest=earliest, deadline=deadline, name=name,
                       return_earliest=back_e, return_deadline=back_d, source=got.get("_source"),
                       operator=got.get("_operator") if got.get("_source") == "bods" else None)  # fmt: skip
    except ValidationError as exc:
        raise ToolError("; ".join(e["msg"] for e in exc.errors())) from exc
    r = await _wrap(setup(request, body))
    text = (f"Tracking the {line} from {b['name']} to {t['name']} (towards {dirs[direction]['towards']}), "
            f"buses from {earliest}, to arrive by {deadline}, as tracker {r['id']!r}.")  # fmt: skip
    if r.get("return_tracker"):
        rt = r["return_tracker"]
        text += f" The way back, from {rt['board']} to {rt['target']}, {rt['earliest']} to arrive by {rt['deadline']}, is {rt['id']!r}."
    return {"text": text, "data": r}


# --- The wishlist -----------------------------------------------------------------------------------------------
def _seen_text(s: dict[str, Any]) -> str:
    shop = {"aldi": "Aldi", "lidl": "Lidl"}.get(s["retailer"], s["retailer"])
    price = f" at £{s['price']:.2f}" if s.get("price") is not None else ""
    when = {"coming": f"in store from {s.get('on_sale')}", "on sale": "on sale now", "sold out": "sold out"}.get(
        s["status"], f"listed{' for ' + s['on_sale'] if s.get('on_sale') else ''}")  # fmt: skip
    return f"{s['name']} at {shop}{price}, {when}"


def _wish_text(it: dict[str, Any]) -> str:
    if it["now"]:
        return f"{it['name']}: " + "; ".join(_seen_text(s) for s in it["now"][:3]) + "."
    if it["last_seen"]:
        lo = it.get("last_on_sale")
        return (f"{it['name']}: not on sale now. Last seen {it['last_seen']['last_seen'][:10]}"
                + (f"; last on sale {lo['on_sale']}" + (f" at £{lo['price']:.2f}" if lo.get("price") is not None else "") if lo else "")
                + ".")  # fmt: skip
    return f"{it['name']}: not seen yet."


async def wishlist_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.wishlist import wishes

    data = await _wrap(wishes(request))
    if not data["items"]:
        return {"text": f"The {data['list']['name']} list in KitchenOwl is empty. Add something with wish_add.", "data": data}
    want = str(args.get("name") or "").strip().lower()
    items = [i for i in data["items"] if not want or want in i["name"].lower()]
    if not items:
        return {"text": f"Nothing called {args.get('name')!r} on the wishlist.", "data": data}
    return {"text": " ".join(_wish_text(i) for i in items), "data": {**data, "items": items}}


async def wish_add_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.wishlist import WishIn, add_wish, summary

    rules = []
    if args.get("max_price"):
        rules.append(f"under £{float(args['max_price']):g}")
    if args.get("must"):
        rules.append("must: " + ", ".join(str(m) for m in args["must"]))
    r = await _wrap(add_wish(request, WishIn(name=_need(args, "name"), description="; ".join(rules))))
    it = next((i for i in r["items"] if i["key"] == r["item"]["key"]), None)
    errs = f" ({'; '.join(r['errors'].values())})" if r.get("errors") else ""
    head = (f"{r['item']['name']} was already on the {r['list']['name']} list" if r.get("already")
            else f"Added {r['item']['name']} to the {r['list']['name']} list in KitchenOwl")  # fmt: skip
    return {"text": f"{head}, and checked Aldi and Lidl{errs}. {_wish_text(it or summary(r['item'], []))}", "data": r}


async def wish_remove_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.wishlist import remove_wish, wishes

    name = _need(args, "name").lower()
    items = (await _wrap(wishes(request)))["items"]
    hit = [i for i in items if i["name"].lower() == name] or [i for i in items if name in i["name"].lower()]
    if len(hit) != 1:
        raise ToolError(f"Say which: {', '.join(i['name'] for i in hit)}" if hit else f"Nothing called {name!r} on the wishlist.")
    await _wrap(remove_wish(request, hit[0]["key"]))
    return {"text": f"Ticked {hit[0]['name']} off the wishlist in KitchenOwl.", "data": {"removed": hit[0]["name"]}}


async def wishlist_check_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.wishlist import check_now

    data = await _wrap(check_now(request, None))
    errs = f" Could not read: {'; '.join(data['errors'].values())}." if data.get("errors") else ""
    found = [i for i in data["items"] if i["now"]]
    return {"text": (" ".join(_wish_text(i) for i in found) if found else
                     f"Checked {data['wishes']} wishes: none is coming or on sale now.") + errs, "data": data}  # fmt: skip


# --- Bank holidays ---------------------------------------------------------------------------------------------------
async def bank_holidays_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from datetime import date as _date

    from app.bankholidays import upcoming

    r = await _wrap(upcoming(request))
    nxt = r["next"][: int(args.get("count") or 3)]
    say = "; ".join(f"{_date.fromisoformat(x['date']).strftime('%A %-d %B %Y')}: {x['title']}" for x in nxt)
    head = f"Today is {r['today']}. " if r.get("today") else ""
    return {"text": head + (f"The next bank holidays: {say}." if say else "No bank holidays are listed ahead."), "data": r}


# --- Here --------------------------------------------------------------------------------------------------------
async def here_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.here import gather
    from app.postcodes import local_postcode

    lat, lon = args.get("lat"), args.get("lon")
    if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))):
        pc = str(args.get("postcode") or "").strip()
        if not pc:
            raise ToolError("Give lat and lon (a Siri shortcut can send the phone's location), or a postcode.")
        got = await local_postcode(request.app.state.engine, pc)
        if got is None:
            raise ToolError(f"Could not find the postcode {pc!r}.")
        lat, lon = got.lat, got.lon
    if not (49.5 <= float(lat) <= 61.0 and -8.7 <= float(lon) <= 2.0):
        raise ToolError("That point is outside Great Britain.")
    d = await gather(request, float(lat), float(lon))
    a = d.get("area") or {}
    head = f"Around {a['postcode']}" + (f", {a['district']}" if a.get("district") else "") if a.get("postcode") else f"Around {d['lat']}, {d['lon']}"
    want = {w.strip() for w in str(args.get("about") or "").split(",") if w.strip()}
    parts = [p for p in d["parts"] if p["ok"] and not p.get("blank") and (not want or p["part"] in want)]
    return {"text": head + ". " + " ".join(f"{p['title']}: {p['text']}" for p in parts),
            "data": {k: v for k, v in d.items() if k != "parts"} | {"parts": [{k: p[k] for k in ("part", "title", "ok", "text")} for p in d["parts"]]}}  # fmt: skip


# --- Outdoors ----------------------------------------------------------------------------------------------------
def _outdoor_text(it: dict[str, Any]) -> str:
    ps = it.get("products") or []
    if not ps:
        return f"{it['name']}: not priced yet."
    shop = {"gooutdoors": "GO Outdoors", "decathlon": "Decathlon", "bikesy": "Bikesy"}

    def one(p: dict[str, Any]) -> str:
        price = f"£{p['price']:.2f}" if p.get("price") is not None else "no price"
        extra = []
        if p.get("dropped") and p.get("previous_price") is not None:
            extra.append(f"down from £{p['previous_price']:.2f}")
        if p.get("was_price"):
            extra.append(f"was £{p['was_price']:.2f}")
        if p.get("lowest_price") is not None and p.get("price") is not None and p["lowest_price"] < p["price"]:
            extra.append(f"lowest seen £{p['lowest_price']:.2f}")
        if p.get("available") is False:
            extra.append("out of stock")
        return f"{p['name']} at {shop.get(p['retailer'], p['retailer'])}, {price}" + (f" ({', '.join(extra)})" if extra else "")

    return f"{it['name']}: " + "; ".join(one(p) for p in ps[:3]) + "."


async def outdoors_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.outdoors import overview

    data = await _wrap(overview(request))
    if not data["items"]:
        return {"text": f"The {data['list']['name']} list in KitchenOwl is empty.", "data": data}
    want = str(args.get("name") or "").strip().lower()
    items = [i for i in data["items"] if not want or want in i["name"].lower()]
    if not items:
        return {"text": f"Nothing called {args.get('name')!r} on the Outdoors list.", "data": data}
    drops = sum(len(i["dropped"]) for i in items)
    head = f"{drops} price drop{'s' if drops != 1 else ''} this week. " if drops else ""
    return {"text": head + " ".join(_outdoor_text(i) for i in items), "data": {**data, "items": items}}


async def outdoors_add_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.outdoors import ItemIn, add_outdoors

    bits = [str(args["link"]).strip()] if args.get("link") else []
    if args.get("max_price"):
        bits.append(f"under £{float(args['max_price']):g}")
    r = await _wrap(add_outdoors(request, ItemIn(name=_need(args, "name"), description=" ".join(bits))))
    it = next((i for i in r["items"] if i["key"] == r["item"]["key"]), None)
    head = (f"{r['item']['name']} was already on the {r['list']['name']} list" if r.get("already")
            else f"Added {r['item']['name']} to the {r['list']['name']} list in KitchenOwl")  # fmt: skip
    errs = " Could not read: " + "; ".join(e for v in (r.get("errors") or {}).values() for e in v) + "." if r.get("errors") else ""
    return {"text": f"{head}. {_outdoor_text(it) if it else ''}{errs}".strip(), "data": r}


async def outdoors_check_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.outdoors import check_now

    data = await _wrap(check_now(request, None))
    fell = data.get("fell") or []
    errs = " Could not read: " + "; ".join(e for v in (data.get("errors") or {}).values() for e in v) + "." if data.get("errors") else ""
    if not fell:
        return {"text": f"Checked {data['items']} items: no prices have fallen since the last check.{errs}", "data": data}
    return {"text": "Cheaper now: " + "; ".join(f"{p['name']} £{p['price']:.2f} (was £{p['from']:.2f})" for p in fell) + "." + errs,
            "data": data}  # fmt: skip

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
    Tool("outdoors", "The family's Outdoors list in KitchenOwl, priced every day at GO Outdoors and Decathlon: each "
         "thing's price now, the lowest seen, sale prices, stock, and price drops this week.",
         {"type": "object", "properties": _props(name=("string", "just this item (any part of its name)"))}, outdoors_tool),
    Tool("outdoors_add", "Add something to the Outdoors list in KitchenOwl (acts: adds it there) and price it now. Give "
         "a GO Outdoors or Decathlon product link to follow that product exactly; otherwise GO Outdoors is searched.",
         {"type": "object", "properties": _props(name=("string", "what you want, e.g. Two-man tent"),
                                                 link=("string", "a GO Outdoors or Decathlon product link"),
                                                 max_price=("number", "the most you would pay, £")),
          "required": ["name"]}, outdoors_add_tool),
    Tool("outdoors_check", "Price everything on the Outdoors list now and say what has got cheaper (it is also "
         "checked every morning).", {"type": "object", "properties": {}}, outdoors_check_tool),
    Tool("bank_holidays", "The next UK bank holidays (from GOV.UK), and whether today is one.",
         {"type": "object", "properties": _props(count=("integer", "how many, default 3"))}, bank_holidays_tool),
    Tool("here", "Everything about a point that is not a saved place, e.g. where the phone is now: the area, bus stops "
         "and buses, stations, fuel and EV chargers, flood warnings, air quality, rivers, rain, tides, storm overflows, "
         "pollen, events, roads and power cuts. Give lat and lon, or a postcode.",
         {"type": "object", "properties": _props(lat=("number", "latitude"), lon=("number", "longitude"),
                                                 postcode=("string", "a UK postcode instead of lat and lon"),
                                                 about=("string", ("only these parts, comma-separated: stops, buses, "
                                                        "stations, floods, air, rivers, rain, tides, overflows, pollen, "
                                                        "fuel, chargers, events, roads, power")))}, here_tool),
    Tool("wishlist", "The family's Wishlist list in KitchenOwl: things to look out for in Aldi's Specialbuys and the "
         "Middle of Lidl. Says which "
         "are coming or on sale now (price, shop, date) and, for the rest, when each was last seen and last on sale.",
         {"type": "object", "properties": _props(name=("string", "just this wish (any part of its name)"))}, wishlist_tool),
    Tool("wish_add", "Add something to the family's Wishlist list in KitchenOwl (acts: adds it there) and check Aldi and "
         "Lidl for it straight away, e.g. Kamado BBQ. A product counts when its name has every word (bbq also finds "
         "barbecue and grill).",
         {"type": "object", "properties": {**_props(name=("string", "what you want, e.g. Kamado BBQ"),
                                                    max_price=("number", "only count it at or under this price, £")),
                                           "must": {"type": "array", "items": {"type": "string"},
                                                    "description": "words the product's name must have"}},
          "required": ["name"]}, wish_add_tool),
    Tool("wish_remove", "Tick something off the Wishlist list in KitchenOwl (acts: ticks it; its history is kept).",
         {"type": "object", "properties": _props(name=("string", "the wish, or part of its name")), "required": ["name"]},
         wish_remove_tool),
    Tool("wishlist_check", "Search Aldi and Lidl now for everything on the wishlist (it is also checked on Wednesday "
         "and Saturday evenings).", {"type": "object", "properties": {}}, wishlist_check_tool),
    Tool("bus_setup", "Set up a bus tracker for a journey (acts: saves it). With just a line, lists its stops each way. "
         "With where you get on, where you are going and when you must arrive, it works out the direction and starts "
         "tracking: whether a bus gets you there in time, and the next buses. Stops can be named loosely, e.g. "
         "'Epsom station'. Will not replace an existing tracker unless replace is true.",
         {"type": "object", "properties": _props(
             line=("string", "the bus route, e.g. 406"), board=("string", "the stop you get on at"),
             to=("string", "the stop you are going to"), arrive_by=("string", "arrive by, e.g. 08:30"),
             earliest=("string", "the earliest bus to count; default an hour before arrive_by"),
             return_earliest=("string", "also track the way back: earliest bus"),
             return_arrive_by=("string", "the way back: arrive by"),
             name=("string", "tracker id, lower-case letters, digits, hyphens; default bus-<line>"),
             replace=("boolean", "replace a tracker of that name"),
             place=("string", "outside London: a saved place near the line, to find it; default home")),
          "required": ["line"]}, bus_setup_tool),
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
    Tool("air_quality", "Air quality near a saved place: Defra's Daily Air Quality Index (1-10) and the NO2, ozone, PM2.5 "
         "and PM10 readings it comes from, from the nearest monitoring stations.",
         {"type": "object", "properties": _props(place=PLACE, km=("number", "how far to look for stations, default 20"))},
         air_quality_tool),
    Tool("river_levels", "River levels near a saved place (the level, whether it is low, normal or high for there, and rising "
         "or falling), and the rain at the nearest gauge. England only.",
         {"type": "object", "properties": _props(place=PLACE, km=("number", "how far to look for river gauges, default 5"))},
         river_levels_tool),
    Tool("tides", "The nearest tide gauge to a saved place: the measured level and whether it is rising or falling (not a "
         "forecast of high water). England only.",
         {"type": "object", "properties": _props(place=PLACE, km=("number", "how far to look, default 20"))}, tides_tool),
    Tool("storm_overflows", "Storm overflows (sewage spills) near a saved place and on the watched rivers: which are "
         "discharging now, which did in the last 48 hours, which monitors are offline. Thames Water's, from the National "
         "Storm Overflow Hub.",
         {"type": "object", "properties": {**_props(place=PLACE, km=("number", "how far to look, default 3")),
                                           "rivers": RIVER_LIST}},
         storm_overflows_tool),
    Tool("watch_rivers", "Set the rivers whose storm overflows to watch (within 15 km of home), e.g. Hogsmill and Thames. "
         "An empty list stops watching rivers.",
         {"type": "object", "properties": {"rivers": RIVER_LIST}, "required": ["rivers"]},
         watch_rivers_tool),
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
