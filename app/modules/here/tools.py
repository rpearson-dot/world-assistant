"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import Tool, ToolError, _props


# --- Here --------------------------------------------------------------------------------------------------------
async def here_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.integrations.what3words import client as W3W
    from app.modules.codepoint.postcodes import local_postcode
    from app.modules.here.here import gather

    lat, lon = args.get("lat"), args.get("lon")
    w = str(args.get("words") or "").strip() or (str(args.get("postcode") or "") if W3W.words_in(str(args.get("postcode") or "")) else "")
    if w and not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))):
        try:
            got3 = await W3W.to_place(w, getattr(request.app.state, "redis", None))
        except W3W.What3WordsError as exc:
            raise ToolError(str(exc)) from exc
        lat, lon = got3["lat"], got3["lon"]
    if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))):
        pc = str(args.get("postcode") or "").strip()
        if not pc:
            raise ToolError("Give lat and lon (a Siri shortcut can send the phone's location), a postcode, or three words.")
        got = await local_postcode(request.app.state.engine, pc)
        if got is None:
            raise ToolError(f"Could not find the postcode {pc!r}.")
        lat, lon = got.lat, got.lon
    if not (49.5 <= float(lat) <= 61.0 and -8.7 <= float(lon) <= 2.0):
        raise ToolError("That point is outside Great Britain.")
    d = await gather(request, float(lat), float(lon))
    a = d.get("area") or {}
    head = f"Around {a['postcode']}" + (f", {a['district']}" if a.get("district") else "") if a.get("postcode") else f"Around {d['lat']}, {d['lon']}"
    if d.get("place"):
        head = f"At {d['place']['name']} (a saved place). " + head
    w3 = d.get("what3words") or {}
    if w3.get("words"):
        head += f" (what3words ///{w3['words']})"
    want = {w.strip() for w in str(args.get("about") or "").split(",") if w.strip()}
    parts = [p for p in d["parts"] if p["ok"] and not p.get("blank") and (not want or p["part"] in want)]
    return {"text": head + ". " + " ".join(f"{p['title']}: {p['text']}" for p in parts),
            "data": {k: v for k, v in d.items() if k != "parts"} | {"parts": [{k: p[k] for k in ("part", "title", "ok", "text")} for p in d["parts"]]}}  # fmt: skip


TOOLS: list[Tool] = [
    Tool("here", "Everything about a point that is not a saved place, e.g. where the phone is now: the area, bus stops "
         "and buses, stations, fuel and EV chargers, flood warnings, air quality, rivers, rain, tides, storm overflows, "
         "pollen, events, roads, power cuts and crime, and its what3words address. Give lat and lon, a postcode, or what3words "
         "three words.",
         {"type": "object", "properties": _props(lat=("number", "latitude"), lon=("number", "longitude"),
                                                 postcode=("string", "a UK postcode instead of lat and lon"),
                                                 words=("string", "what3words three words (///filled.count.soap) instead"),
                                                 about=("string", ("only these parts, comma-separated: stops, buses, "
                                                        "stations, floods, air, rivers, rain, tides, overflows, pollen, "
                                                        "fuel, chargers, events, roads, power, crime")))}, here_tool),
]
