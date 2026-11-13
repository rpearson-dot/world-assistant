"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import PLACE, Tool, _props, _wrap


async def air_quality_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.environment.environment import air

    r = await _wrap(air(request, place=args.get("place") or None, km=float(args.get("km") or 20)))
    if r.get("index") is None:
        return {"text": f"No air quality reading near {r['name']}: {r.get('note', 'none found')}.", "data": r}
    bits = [f"{x['pollutant']} {x['mean']:g} {x['unit']}" + (f" ({x['mean_hours']}-hour mean)" if x["mean_hours"] > 1 else "")
            + f", index {x['index']}" for x in r["readings"].values()]  # fmt: skip
    nearest = min(r["readings"].values(), key=lambda x: x["station_km"])
    return {"text": f"Air quality near {r['name']}: {r['band'].lower()}, index {r['index']} of 10 (worst: {r['worst']}). "
                    + "; ".join(bits) + f". Nearest station: {nearest['station']}, {nearest['station_km']:g} km.", "data": r}  # fmt: skip


async def river_levels_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.environment.environment import rain, rivers

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
    from app.modules.environment.environment import flows

    try:
        fl = await _wrap(flows(request, place=place, km=10))
    except Exception:  # noqa: BLE001 - flow is extra: levels and rain still answer
        fl = {"flows": []}
    for x in fl["flows"][:2]:
        if x["flow_m3s"] is None:
            continue
        text_ += f" Flow on the {x['river'] or x['station']} at {x['station']}: {x['flow_m3s']:g} m³/s"
        if x.get("state"):
            text_ += f", {x['state']} for there" + (f" ({x['pct_of_mean']}% of its mean)" if x.get("pct_of_mean") is not None else "")
        text_ += f"{', ' + x['trend'] if x.get('trend') else ''}."
    return {"text": text_, "data": {"rivers": rv, "rain": rn, "flows": fl}}


async def tides_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.environment.environment import tides

    r = await _wrap(tides(request, place=args.get("place") or None, km=float(args.get("km") or 20)))
    if not r.get("gauge"):
        return {"text": f"No tide gauge near {r['name']}.", "data": r}
    tr = f", {r['trend']}" if r.get("trend") else ""
    return {"text": f"The tide gauge at {r['gauge']} ({r['km']:g} km from {r['name']}) reads {r['level_m']:.2f} {r['unit']}{tr}. "
                    "That is the measured level, not a forecast.", "data": r}  # fmt: skip


RIVER_LIST = {"type": "array", "items": {"type": "string"},
              "description": "river names, e.g. [\"Hogsmill\", \"Thames\"]; any part of the name matches"}


async def storm_overflows_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.environment.environment import overflows

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
    from app.modules.environment.environment import Rivers, put_rivers

    rv = args.get("rivers") or []
    r = await _wrap(put_rivers(request, Rivers(rivers=[str(x) for x in (rv if isinstance(rv, list) else [rv])])))
    if not r["rivers"]:
        return {"text": "Not watching any rivers; storm overflows near home are still reported.", "data": r}
    return {"text": f"Watching storm overflows on the {', '.join(r['rivers'])} (within 15 km of home).", "data": r}


TOOLS: list[Tool] = [
    Tool("air_quality", "Air quality near a saved place: Defra's Daily Air Quality Index (1-10) and the NO2, ozone, PM2.5 "
         "and PM10 readings it comes from, from the nearest monitoring stations.",
         {"type": "object", "properties": _props(place=PLACE, km=("number", "how far to look for stations, default 20"))},
         air_quality_tool),
    Tool("river_levels", "River levels near a saved place (the level, whether it is low, normal or high for there, and rising "
         "or falling), river flow where measured (m³/s, compared with its usual flows), and the rain at the nearest gauge. "
         "England only.",
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
]
