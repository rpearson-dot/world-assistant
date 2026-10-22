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
            t += f" About {r['cost_gbp_at_home_rate']:.2f} pounds at your home rate."
    else:
        t = f"{r['trip']} is {miles:.0f} miles, about {r['litres']:.0f} litres in the {r['vehicle']}."
        if "cost_gbp_typical" in r:
            t += (f" Around {r['cost_gbp_typical']:.2f} pounds at typical prices on the route, "
                  f"or {r['cost_gbp_cheapest']:.2f} at the cheapest station.")  # fmt: skip
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
    Tool("trip_cost", "Fuel or energy needed, and cost, for a saved routed trip in a saved vehicle.",
         {"type": "object", "properties": _props(trip=("string", "trip id"), vehicle=("string", "vehicle id")),
          "required": ["trip", "vehicle"]}, trip_cost_tool),
    Tool("trains", "Next trains on a saved departure board.",
         {"type": "object", "properties": _props(board=("string", "board id"),
                                                 count=("integer", "how many, default 3")),
          "required": ["board"]}, trains),
    Tool("bus", "Bus tracker: has a bus made it to the target stop before the deadline, and next buses at your stops.",
         {"type": "object", "properties": _props(tracker=("string", "tracker id; optional if only one"),
                                                 stop=("string", "only this stop key; optional"))}, bus),
]}  # fmt: skip


async def run_tool(request: Request, name: str, args: dict[str, Any]) -> dict[str, Any]:
    tool = TOOLS.get(name)
    if tool is None:
        raise ToolError(f"Unknown tool {name!r}.")
    try:
        return await tool.run(request, args)
    except (ValueError, TypeError) as exc:
        raise ToolError(f"Bad arguments: {exc}") from exc


# ---- plain HTTP, for Siri Shortcuts and scripts --------------------------------------------


@router.get("/v1/ask")
async def ask_index() -> list[dict[str, Any]]:
    return [{"tool": t.name, "description": t.description, "arguments": t.schema.get("properties", {})}
            for t in TOOLS.values()]  # fmt: skip


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
            "serverInfo": {"name": "world-assistant", "version": "0.11.0"},
            "instructions": "Personal assistant for fuel prices, trips, trains and buses. "
                            "Call list_options first to see saved ids.",
        }}  # fmt: skip
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [
            {"name": t.name, "description": t.description, "inputSchema": t.schema} for t in TOOLS.values()]}}  # fmt: skip
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
