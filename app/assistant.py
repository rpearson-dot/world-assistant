"""Question answering: one set of tools, served to MCP clients (/mcp) and to plain HTTP callers
such as Siri Shortcuts (/v1/ask). Every tool returns a short sentence that reads well aloud,
plus the structured data behind it.

MCP is spoken here directly (JSON-RPC over HTTP POST, stateless) so no extra dependency is
needed. Authentication is the same bearer token as the rest of the API.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import text

from app.auth import require_token
from app.bus import live as bus_live
from app.bus import target as bus_target
from app.config import get_settings
from app.energy import sessions as energy_sessions
from app.energy import speech as energy_speech
from app.energy import store as energy_store
from app.energy import summary as energy_summary
from app.events import near as events_near
from app.fuel import near as fuel_near
from app.pollen import near as pollen_near
from app.tfl import departures as board_departures
from app.vehicles import trip_cost

log = logging.getLogger("wa.assistant")
router = APIRouter(dependencies=[Depends(require_token)])

KM_PER_MILE = 1.609344


class ToolError(Exception):
    pass


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    run: Callable[[Request, dict[str, Any]], Awaitable[dict[str, Any]]]


def _props(**fields: tuple[str, str]) -> dict[str, Any]:
    return {k: {"type": t, "description": d} for k, (t, d) in fields.items()}


def _need(args: dict[str, Any], key: str) -> str:
    v = args.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ToolError(f"Missing {key!r}. Try list_options to see what is set up.")
    return v.strip()


async def _wrap(coro: Awaitable[Any]) -> Any:
    try:
        return await coro
    except HTTPException as exc:
        raise ToolError(str(exc.detail)) from exc


def _miles(km: float) -> str:
    m = km / KM_PER_MILE
    return f"{m:.1f} mile{'s' if round(m, 1) != 1 else ''}"


def _ids(rows: Any) -> list[str]:
    return [r[0] for r in rows]


async def list_options(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    queries = {
        "places": "SELECT id FROM place ORDER BY id", "vehicles": "SELECT id FROM vehicle ORDER BY id",
        "trips": "SELECT id FROM trip ORDER BY id", "train_boards": "SELECT id FROM tfl_board ORDER BY id",
        "bus_trackers": "SELECT id FROM bus_tracker ORDER BY id",
    }  # fmt: skip
    data: dict[str, list[str]] = {}
    async with request.app.state.engine.connect() as conn:
        for name, sql in queries.items():
            data[name] = _ids(await conn.execute(text(sql)))
    parts = [
        f"{len(v)} {k.replace('_', ' ')}" + (f" ({', '.join(v)})" if v else "")
        for k, v in data.items()
    ]
    return {"text": "You have " + "; ".join(parts) + ".", "data": data}


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


async def pollen_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    place = str(args.get("place") or "") or None
    res = await _wrap(pollen_near(request, place=place, hourly=False))
    where = res.get("place") or place
    risk = (res.get("allergy_risk_label") or [None])[0]
    high = [
        f"{a['name']} {a['labels'][0]}"
        for a in res["allergens"]
        if (a["levels"][0] or 0) >= 1 and a.get("name")
    ][:5]
    parts = []
    if risk:
        parts.append(f"allergy risk today is {risk}")
    parts.append("pollen today: " + ", ".join(high) if high else "no pollen is reported today")
    return {"text": f"Near {where}, " + "; ".join(parts) + ".", "data": res}


async def energy_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    hours = float(args.get("hours") or 3)
    tariff, periods = await energy_store.load(request.app.state.engine)
    now = datetime.now(UTC)
    res = energy_summary.summarise(tariff, periods, now, min(max(hours, 0.5), 12))
    if not res["available"]:
        raise ToolError("There are no electricity rates yet. Set up the Octopus account or tariff (docs/energy.md).")
    res["sessions"] = energy_sessions.summarise(await energy_store.load_sessions(request.app.state.engine), now)
    return {"text": energy_speech.sentence(res, ZoneInfo(get_settings().local_tz), now), "data": res}


async def conditions_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.conditions import all_conditions, trip_conditions

    days = int(args.get("days") or 7)
    trip = str(args.get("trip") or "") or None
    if trip:
        r = await _wrap(trip_conditions(request, trip, None, days))
        return {"text": r["summary"], "data": r}
    res = await all_conditions(request, days)
    done = [t for t in res["trips"] if "now" in t]
    if not done:
        raise ToolError("No trip has a route yet. Build one on the Places page, then ask again.")
    return {"text": " ".join(t["summary"] for t in done), "data": res}


async def events_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    place = str(args.get("place") or "") or None
    days = int(args.get("days") or 7)
    kind = str(args.get("type") or "").upper() or None
    res = await _wrap(
        events_near(request, place=place, radius_km=8.0, days=days, type=kind, keyword=None, limit=5)
    )
    ev = res["events"]
    where = res.get("place") or place
    if not ev:
        return {"text": f"I found no events near {where} in the next {days} days.", "data": res}
    parts = []
    for e in ev[:5]:
        when = (e.get("date") or "").strip()
        parts.append(f"{e.get('name') or 'an event'}" + (f" at {e['venue']}" if e.get("venue") else "")
                     + (f" on {when}" if when else ""))  # fmt: skip
    more = f", and {res['total'] - len(parts)} more" if res["total"] > len(parts) else ""
    return {
        "text": f"{res['total']} events near {where} in the next {days} days: " + "; ".join(parts) + more + ".",
        "data": res,
    }


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


async def _tracker(request: Request, args: dict[str, Any]) -> str:
    if args.get("tracker"):
        return str(args["tracker"])
    async with request.app.state.engine.connect() as conn:
        ids = _ids(await conn.execute(text("SELECT id FROM bus_tracker WHERE enabled ORDER BY id")))
    if len(ids) != 1:
        raise ToolError("Say which bus tracker." if ids else "No bus tracker is set up yet.")
    return ids[0]


async def bus(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    tid = await _tracker(request, args)
    tgt = await _wrap(bus_target(request, tid, None))
    parts = [tgt["summary"]]
    data: dict[str, Any] = {"target": tgt}
    try:
        lv = await _wrap(bus_live(request, tid))
        data["live"] = lv
        for key, rows in lv["next"].items():
            if rows and (not args.get("stop") or args["stop"] == key):
                parts.append(f"Next bus at {rows[0]['label']}: {rows[0]['minutes']} minutes.")
    except ToolError as exc:
        parts.append(f"Live times are unavailable ({exc}).")
    if tgt["pending"] and tgt["tracking_now"]:
        parts.append(f"{len(tgt['pending'])} scheduled bus(es) not yet seen.")
    return {"text": " ".join(parts), "data": data}


def _closure_text(items: list[dict[str, Any]]) -> str:
    if not items:
        return "No lifts, escalators or entrances reported closed."
    return "Closed now: " + " ".join(
        f"{i['description'].rstrip('.')}." + (f" (until {i['until'][:10]})" if i.get("until") else "") for i in items[:4]
    ) + (f" And {len(items) - 4} more." if len(items) > 4 else "")


async def station_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.stations import alerts, near, one, search

    name = str(args.get("name") or "").strip()
    if name:
        found = await _wrap(search(request, name, 3))
        if not found["stations"]:
            if not found["loaded"]:
                return {"text": "TfL's station data has not been loaded yet.", "data": found}
            return {"text": f"No TfL station matches {name!r}. TfL covers Underground, Overground, DLR, "
                            "Elizabeth line and tram stations.", "data": found}  # fmt: skip
        st = await _wrap(one(found["stations"][0]["id"], request))
        fac = st["facilities"].get("Facility", {})
        has = ", ".join(f"{k.lower()} {v}" if v not in ("yes", "Yes") else k.lower()
                        for k, v in fac.items() if k in ("Lifts", "Toilets", "Escalators", "Car park") and v)  # fmt: skip
        head = (f"{st['name']}" + (f", zone {st['zone']}" if st.get("zone") else "")
                + (f": {', '.join(st['lines'][:6])}." if st["lines"] else ".")
                + (f" {st['entrances']} entrances, {st['platforms']} platforms." if st["entrances"] else "")
                + (f" Has {has}." if has else "") + (f" Step-free: {st['step_free']}." if st.get("step_free") else ""))  # fmt: skip
        items = st["closures"] if not args.get("lifts_only") else [c for c in st["closures"] if c["kind"] in ("lift", "step_free")]
        return {"text": f"{head} {_closure_text(items)}", "data": st}
    if args.get("place") or not args.get("everywhere"):
        r = await _wrap(near(request, args.get("place"), float(args.get("km") or 2), 8))
        if not r["stations"]:
            return {"text": "No TfL stations within that distance of the place.", "data": r}
        lines = []
        for st in r["stations"]:
            items = [c for c in st["closures"] if not args.get("lifts_only") or c["kind"] in ("lift", "step_free")]
            lines.append(f"{st['name']} ({st['km']} km): " + (_closure_text(items) if items else "nothing closed"))
        return {"text": "Near " + r["place"] + ": " + "; ".join(lines) + ".", "data": r}
    r = await _wrap(alerts(request, None, bool(args.get("lifts_only"))))
    return {"text": f"{r['count']} closures across TfL stations now.", "data": r}
async def power_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.power import cuts, history, supply, works

    place = args.get("place")
    what = str(args.get("about") or "cuts")
    if what == "supply":
        s = await _wrap(supply(request, place))
        if not s["checked"]:
            return {"text": "The network around this place has not been looked up yet (it is, each night).", "data": s}
        if not s["on_network"]:
            other = f" It is supplied by {s['independent_operator']}." if s["independent_operator"] else ""
            return {"text": "This place is not on UK Power Networks' network." + other, "data": s}
        chain = [f"{label} {s[k]['name']}" for k, label in (("grid_supply_point", "grid supply point"),
                 ("primary", "primary substation"), ("secondary", "local substation")) if s.get(k)]  # fmt: skip
        rota = f" Rota block {s['rota_block']['code']}." if s.get("rota_block") else ""
        cut = " There is a power cut here now." if s["power_cut_now"] else " No power cut here now."
        return {"text": "Fed from " + ", ".join(chain) + "." + rota + cut, "data": s}
    if what == "history":
        h = await _wrap(history(request, place, int(args.get("days") or 365), 200))
        if not h["count"]:
            return {"text": "No power cuts recorded there" + (f" since {h['recording_since'][:10]}." if h["recording_since"] else "."), "data": h}
        longest = f" The longest lasted {h['longest_hours']:g} hours." if h["longest_hours"] else ""
        late = f" Power came back later than first promised {h['late']} time(s)." if h["late"] else ""
        return {"text": f"{h['unplanned']} unplanned and {h['planned']} planned power cut(s) recorded there in the last "
                        f"{h['days']} days.{longest}{late}", "data": h}  # fmt: skip
    if what == "works":
        w = await _wrap(works(request, place, float(args.get("km") or 1), None))
        if not w["count"]:
            return {"text": "No UK Power Networks street works near there.", "data": w}
        bits = [f"{x['street'] or 'a street'}" + (f" ({x['traffic']})" if x.get("traffic") else "") for x in w["works"][:4]]
        return {"text": f"{w['open']} under way and {w['proposed']} planned nearby: " + "; ".join(bits) + ".", "data": w}
    r = await _wrap(cuts(request, place, float(args.get("km") or 5)))
    if not r["count"]:
        return {"text": "No power cuts reported near there.", "data": r}
    head = "There is a power cut at this place. " if r["at_place"] else ""
    bits = []
    for x in r["cuts"][:3]:
        kind = "planned" if x["planned"] else "unplanned" if x["planned"] is False else ""
        eta = f", power back by {datetime.fromisoformat(x['restore_by']).astimezone(ZoneInfo(get_settings().local_tz)):%H:%M}" if x["restore_by"] else ""
        bits.append(f"{kind} cut".strip() + (f" {x['km']} km away" if x["km"] is not None else "")
                    + (f" affecting {x['customers']} homes" if x["customers"] else "") + eta)  # fmt: skip
    return {"text": f"{head}{r['count']} power cut(s) nearby: " + "; ".join(bits) + ".", "data": r}


async def ev_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.ev import along_trip, sites

    connector = args.get("connector") or None
    free = bool(args.get("free_only"))
    if args.get("trip"):
        r = await _wrap(along_trip(request, str(args["trip"]), float(args.get("km") or 2), connector, free))
        if not r["count"]:
            return {"text": f"No GRIDSERVE chargers within {r['within_km']:g} km of {r['trip']['name']}'s route.", "data": r}
        return {"text": f"{r['count']} GRIDSERVE site(s) along {r['trip']['name']}: "
                        + " ".join(x["summary"] for x in r["sites"][:6]), "data": r}  # fmt: skip
    q = args.get("name") or None
    r = await _wrap(sites(request, args.get("place"), float(args.get("km") or 25), connector, free, q, 5))
    if not r["count"]:
        where = f"called {q!r}" if q else f"within {r['km']:g} km"
        return {"text": f"No GRIDSERVE chargers {where}" + (f" with {r['connector']}" if r["connector"] else "") + ".",
                "data": r}  # fmt: skip
    return {"text": " ".join(x["summary"] for x in r["sites"][:5]), "data": r}


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("list_options", "List the saved places, vehicles, trips, train boards and bus trackers (their ids).",
         {"type": "object", "properties": {}}, list_options),
    Tool("fuel_cheapest", "Cheapest fuel near a saved place.",
         {"type": "object", "properties": _props(
             place=("string", "id of a saved place; default: home"), fuel=("string", "E10 (default), E5, B7_STANDARD, B7_PREMIUM"),
             radius_km=("number", "search radius in km, default 5"),
             brands=("string", "comma-separated brands to limit to, e.g. Tesco,Asda"),
             supermarkets=("boolean", "limit to supermarkets"))}, fuel_cheapest),
    Tool("events_near", "What's on near a saved place: gigs, club nights, comedy, theatre, sport and more.",
         {"type": "object", "properties": _props(
             place=("string", "id of a saved place; default: home"), days=("number", "how many days ahead, default 7"),
             type=("string", "optional: LIVE, CLUB, FEST, COMEDY, THEATRE, SPORT, KIDS, ARTS"))}, events_tool),
    Tool("pollen", "Pollen levels and allergy risk near a saved place, today and the next three days.",
         {"type": "object", "properties": _props(place=("string", "id of a saved place; default: home"))}, pollen_tool),
    Tool("electricity", "Electricity price now and next, the next cheap and peak times, the cheapest block of hours, and Saving Sessions.",
         {"type": "object", "properties": _props(hours=("number", "length of the cheapest block, default 3"))}, energy_tool),
    Tool("road_conditions", "Road works, closures, incidents and flood warnings on a saved trip's route, now and planned. No trip means all of them.",
         {"type": "object", "properties": _props(trip=("string", "id of a saved routed trip; default: all"),
                                                  days=("number", "how far ahead to look for planned works, default 7"))}, conditions_tool),
    Tool("trip_cost", "Fuel or energy needed, and cost, for a saved routed trip in a saved vehicle.",
         {"type": "object", "properties": _props(trip=("string", "trip id"), vehicle=("string", "vehicle id")),
          "required": ["trip", "vehicle"]}, trip_cost_tool),
    Tool("trains", "Next trains on a saved departure board.",
         {"type": "object", "properties": _props(board=("string", "board id"),
                                                 count=("integer", "how many, default 3")),
          "required": ["board"]}, trains),
    Tool("station", "A TfL station's lines, zone, entrances, platforms, facilities (lifts, toilets, step-free) and what is closed there now, "
         "especially lifts out of service; or the stations near a saved place and their closures.",
         {"type": "object", "properties": _props(
             name=("string", "station name, e.g. Waterloo; omit to list stations near a place"),
             place=("string", "id of a saved place; default: home"), km=("number", "how far from the place, default 2"),
             lifts_only=("boolean", "only lift and step-free closures"))}, station_tool),
    Tool("power", "Electricity from UK Power Networks: power cuts near a saved place (default), their street works, what "
         "supplies the place (substations), or past cuts there.",
         {"type": "object", "properties": _props(
             about=("string", "cuts (default), history, works or supply"), days=("integer", "history: how far back, default 365"),
             place=("string", "id of a saved place; default: home"), km=("number", "how far to look"))}, power_tool),
    Tool("ev_chargers", "GRIDSERVE EV chargers near a saved place, along a trip, or by name: how many work and are free "
         "now, by connector (CCS, CHAdeMO, Type 2), how fast, and the price per kWh.",
         {"type": "object", "properties": _props(
             place=("string", "id of a saved place; default: home"), trip=("string", "id of a trip: chargers along its route"),
             name=("string", "part of a site's name, town or postcode, e.g. Rugby"),
             connector=("string", "ccs, chademo or type2"), free_only=("boolean", "only sites with a free charger now"),
             km=("number", "near a place: how far, default 25; along a trip: how far off the route, default 2"))}, ev_tool),
    Tool("bus", "Bus tracker: has a bus made it to the target stop before the deadline, and next buses at your stops.",
         {"type": "object", "properties": _props(tracker=("string", "tracker id; optional if only one"),
                                                 stop=("string", "only this stop key; optional"))}, bus),
]}  # fmt: skip


async def _available(request: Request) -> set[str]:
    """Tool names whose module is on. A tool no module claims is always available."""
    from app.modules import registry
    from app.modules.gate import enabled

    try:
        on = await enabled(request.app.state.engine)
    except Exception:  # noqa: BLE001 - the choice must not take the assistant down
        return set(TOOLS)
    return {n for n in TOOLS if (registry.module_of_tool(n) or "core") in on}


async def run_tool(request: Request, name: str, args: dict[str, Any]) -> dict[str, Any]:
    tool = TOOLS.get(name)
    if tool is None:
        raise ToolError(f"Unknown tool {name!r}.")
    if name not in await _available(request):
        raise ToolError(f"The module behind {name!r} is switched off. Turn it on at /modules.")
    try:
        return await tool.run(request, args)
    except (ValueError, TypeError) as exc:
        raise ToolError(f"Bad arguments: {exc}") from exc


# ---- plain HTTP, for Siri Shortcuts and scripts --------------------------------------------


@router.get("/v1/ask")
async def ask_index(request: Request) -> list[dict[str, Any]]:
    on = await _available(request)
    return [{"tool": t.name, "description": t.description, "arguments": t.schema.get("properties", {})}
            for t in TOOLS.values() if t.name in on]  # fmt: skip


@router.post("/v1/ask/{name}", response_model=None)
async def ask(
    request: Request,
    name: str,
    args: Annotated[dict[str, Any] | None, Body()] = None,
    format: str = "json",
) -> Any:
    """POST the arguments as JSON. `?format=text` returns just the sentence, ready to speak."""
    try:
        result = await run_tool(request, name, args or {})
    except ToolError as exc:
        if format == "text":
            return PlainTextResponse(str(exc), status_code=422)
        raise HTTPException(422, str(exc)) from exc
    return PlainTextResponse(result["text"]) if format == "text" else result


# ---- MCP over HTTP (JSON-RPC 2.0, stateless) -------------------------------------------------

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")


def _rpc_error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


async def handle_rpc(request: Request, msg: Any) -> dict[str, Any] | None:
    """One JSON-RPC message in, one response out (None for notifications)."""
    if (
        not isinstance(msg, dict)
        or msg.get("jsonrpc") != "2.0"
        or not isinstance(msg.get("method"), str)
    ):
        return _rpc_error(
            msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid request"
        )
    rid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
    if "id" not in msg:  # notification: nothing to answer
        return None
    if method == "initialize":
        asked = params.get("protocolVersion")
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "world-assistant", "version": "0.23.0"},
            "instructions": "Personal assistant for fuel prices, trips, trains and buses. "
                            "Call list_options first to see saved ids.",
        }}  # fmt: skip
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [
            {"name": t.name, "description": t.description, "inputSchema": t.schema}
            for t in TOOLS.values() if t.name in await _available(request)]}}  # fmt: skip
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if not isinstance(name, str) or name not in TOOLS or not isinstance(args, dict):
            return _rpc_error(rid, -32602, "Unknown tool or bad arguments")
        try:
            out = await run_tool(request, name, args)
            content = [{"type": "text", "text": out["text"]}]
            return {"jsonrpc": "2.0", "id": rid, "result": {"content": content, "structuredContent": out["data"],
                                                           "isError": False}}  # fmt: skip
        except ToolError as exc:
            return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": str(exc)}],
                                                           "isError": True}}  # fmt: skip
        except Exception:
            log.exception("tool %s failed", name)
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "That failed on the server; check the logs."}],
                "isError": True}}  # fmt: skip
    return _rpc_error(rid, -32601, f"Method not found: {method}")


@router.post("/mcp", response_model=None)
async def mcp_endpoint(request: Request) -> Any:
    try:
        body = await request.json()
    except ValueError:
        return _rpc_error(None, -32700, "Parse error")
    if isinstance(body, list):
        replies = [r for m in body[:20] if (r := await handle_rpc(request, m)) is not None]
        return replies if replies else Response(status_code=202)
    reply = await handle_rpc(request, body)
    return Response(status_code=202) if reply is None else reply


@router.get("/mcp")
async def mcp_get() -> Response:
    # No server-initiated streams: tells clients to use POST only.
    return Response(status_code=405, headers={"Allow": "POST"})
