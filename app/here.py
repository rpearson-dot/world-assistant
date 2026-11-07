"""Here: what World Assistant knows about wherever you are, without saving it as a place first.

    GET  /v1/here?lat=&lon=          every source at once for a point: the area, buses and stations, fuel and chargers,
                                     floods, air, rivers and rain, tides, pollen, events, roads and power cuts
    POST /v1/here/save               keep the point as a saved place: {"lat", "lon", "name"}

Each part is World Assistant's own endpoint, asked for that point inside the server, so every source works exactly as
it does for a saved place. A source that is switched off, outside its area or failing says so in that part; the rest
still come back. The page's "Use my location" asks the browser, which only allows it over https (the Tailscale
address), not the plain address on the home network.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.auth import require_token
from app.config import get_settings

router = APIRouter(prefix="/v1/here", tags=["here"], dependencies=[Depends(require_token)])
MILE = 1.609344


def _mi(km: Any) -> str:
    try:
        m = float(km) / MILE
    except (TypeError, ValueError):
        return ""
    return f"{m:.1f} mile{'s' if round(m, 1) != 1 else ''}"


# (part, title, path, extra query) in the order shown. {pc} is the point's postcode, found first.
PARTS: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
    ("buses", "Next buses", "/v1/live-buses/departures", {"radius_m": 400, "stops": 3}),
    ("bus_disruptions", "Bus disruptions", "/v1/bus-disruptions", {"within_km": 2, "limit": 5}),
    ("stations", "Stations (London)", "/v1/stations/near", {"km": 2, "limit": 4}),
    ("floods", "Flood warnings", "/v1/floods/at", {"within_km": 3}),
    ("air", "Air quality", "/v1/environment/air", {"km": 20}),
    ("rivers", "Rivers", "/v1/environment/rivers", {"km": 5}),
    ("rain", "Rain", "/v1/environment/rain", {"km": 10}),
    ("tides", "Tide", "/v1/environment/tides", {"km": 20}),
    ("overflows", "Storm overflows", "/v1/environment/overflows", {"km": 3}),
    ("pollen", "Pollen", "/v1/pollen/near", {}),
    ("fuel", "Fuel (E10)", "/v1/fuel/near", {"radius_km": 8, "limit": 3, "fuel": "E10"}),
    ("chargers", "EV chargers", "/v1/ev/sites", {"km": 15, "limit": 3}),
    ("events", "Events, next 3 days", "/v1/events/near", {"radius_km": 8, "days": 3, "limit": 5}),
    ("roads", "Roads (London)", "/v1/roads/tfl/at", {"radius_m": 2000}),
    ("power", "Power cuts", "/v1/power/cuts", {"km": 5, "postcode": "{pc}"}),
)


def words(part: str, d: dict[str, Any]) -> str:
    """One or two sentences for a part's answer."""
    if part == "buses":
        stops = d.get("stops") or []
        if not stops:
            return d.get("note") or "No bus stops within 400 m."

        def bus(b: dict[str, Any]) -> str:
            to = f" to {b['towards']}" if b.get("towards") else ""
            if b.get("live"):
                return f"{b['line']}{to} {'due' if b['minutes'] <= 1 else 'in ' + str(b['minutes']) + ' min'}"
            return f"{b['line']}{to} at {', '.join(b['at'])} (timetable)"

        parts = []
        for s in stops:
            name = s["name"] + (f" ({s['indicator']})" if s.get("indicator") else "")
            buses = s.get("buses") or []
            parts.append(f"{name}, {round(s['km'] * 1000)} m: " + ("; ".join(bus(b) for b in buses[:4]) if buses else "nothing due"))
        tail = f" Still looking up the timetable for {', '.join(d['loading'])}; ask again in a minute." if d.get("loading") else ""
        return ". ".join(parts) + "." + tail
    if part == "bus_disruptions":
        x = d.get("disruptions") or []
        return " ".join(f"{i.get('summary') or 'Disruption'}" + (f" ({', '.join(i['lines'][:4])})" if i.get("lines") else "") + "."
                        for i in x[:3]) if x else "No bus disruptions published within 2 km."  # fmt: skip
    if part == "stations":
        s = d.get("stations") or []
        return "; ".join(f"{x['name']} ({_mi(x.get('km'))})" + (", closures reported" if x.get("closures") else "") for x in s) + "." if s else "No London stations within 2 km."
    if part == "floods":
        w = d.get("warnings") or []
        return " ".join(f"{x['severity']}: {x['area']}." for x in w[:3]) if w else "No flood warnings here."
    if part == "air":
        if d.get("index") is None:
            return "No air quality station near enough."
        return f"Index {d['index']} ({d.get('band', '').lower()})" + (f", worst {d['worst']}" if d.get("worst") else "") + "."
    if part == "rivers":
        r = d.get("rivers") or []
        return "; ".join(f"{x.get('river') or x.get('station')}: {x.get('state') or 'level ' + str(x.get('level'))}"
                         + (f", {x['trend']}" if x.get("trend") else "") for x in r[:3]) + "." if r else "No river gauge within 5 km."  # fmt: skip
    if part == "rain":
        g = (d.get("gauges") or [None])[0]
        return (f"{g['last_hour_mm']} mm in the last hour, {g['last_24h_mm']} mm in 24 hours ({g['gauge']}, {_mi(g['km'])})."
                if g else "No rain gauge within 10 km.")  # fmt: skip
    if part == "tides":
        if not d.get("gauge"):
            return "No tide gauge within 20 km."
        return f"{d['gauge']} ({_mi(d.get('km'))}): {d.get('level_m')} m, {d.get('trend') or 'steady'}."
    if part == "overflows":
        n = len(d.get("discharging") or [])
        recent = len(d.get("recent") or [])
        return (f"{n} discharging now" if n else "None discharging now") + (f", {recent} in the last 48 hours" if recent else "") + f" (of {d.get('count', 0)} within 3 km)."
    if part == "pollen":
        risk = (d.get("allergy_risk_label") or [None])[0]
        high = [f"{a['name']} {a['labels'][0]}" for a in d.get("allergens") or [] if (a["levels"][0] or 0) >= 1 and a.get("name")][:4]
        return (f"Allergy risk {risk}. " if risk else "") + ("Today: " + ", ".join(high) + "." if high else "No pollen reported today.")
    if part == "fuel":
        s = d.get("stations") or []
        return "Cheapest: " + "; ".join(f"{x.get('brand') or x.get('name')}, {x['pence']:.1f}p, {_mi(x.get('distance_km'))}" for x in s) + "." if s else "No E10 prices within 5 miles."
    if part == "chargers":
        s = d.get("sites") or []
        return " ".join(x.get("summary") or x.get("name", "") for x in s) if s else "No GRIDSERVE chargers within 10 miles."
    if part == "events":
        e = d.get("events") or []
        return ("; ".join(f"{x.get('name')}" + (f" at {x['venue']}" if x.get("venue") else "") + (f", {x['date']}" if x.get("date") else "") for x in e[:4])
                + (f", and {d['total'] - 4} more" if (d.get("total") or 0) > 4 else "") + ".") if e else "Nothing listed nearby in the next 3 days."  # fmt: skip
    if part == "roads":
        r = d.get("disruptions") or []
        return "; ".join(f"{x.get('severity')}: {x.get('location') or x.get('category')}" for x in r[:3]) + "." if r else "No TfL road disruption within 2 km."
    if part == "power":
        c = d.get("cuts") or []
        return (f"{len(c)} power cut{'s' if len(c) != 1 else ''} within 3 miles" + (f", {d['at_place']} here" if d.get("at_place") else "") + "."
                if c else "No power cuts reported nearby.")  # fmt: skip
    return ""


def _blank(part: str, d: dict[str, Any]) -> bool:
    """Nothing worth showing: a part outside its area (London-only sources away from London)."""
    return part in ("stations", "roads") and not (d.get("stations") or d.get("disruptions"))


async def gather(request: Request, lat: float, lon: float) -> dict[str, Any]:
    """Every part for a point, asked at once inside the server."""
    token = get_settings().api_token
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    transport = httpx.ASGITransport(app=request.app)
    out: dict[str, Any] = {"lat": round(lat, 5), "lon": round(lon, 5), "area": None, "parts": []}
    async with httpx.AsyncClient(transport=transport, base_url="http://wa.internal", headers=headers, timeout=30) as c:
        try:
            pc = (await c.get("/v1/postcodes/near", params={"lat": lat, "lon": lon, "radius_km": 1, "limit": 1})).json()
            out["area"] = (pc.get("postcodes") or [None])[0]
        except (httpx.HTTPError, ValueError):
            pass
        postcode = (out["area"] or {}).get("postcode") or ""

        async def one(part: str, title: str, path: str, extra: dict[str, Any]) -> dict[str, Any]:
            q = {"lat": lat, "lon": lon, **{k: (postcode if v == "{pc}" else v) for k, v in extra.items()}}
            q = {k: v for k, v in q.items() if v != ""}
            try:
                r = await c.get(path, params=q)
            except httpx.HTTPError as exc:
                return {"part": part, "title": title, "ok": False, "text": f"Could not ask ({type(exc).__name__})."}
            if r.status_code == 404 and "off" in r.text.lower():
                return {"part": part, "title": title, "ok": False, "off": True, "text": "Switched off."}
            try:
                d = r.json()
            except ValueError:
                d = {}
            if r.status_code >= 400:
                detail = d.get("detail") if isinstance(d, dict) else None
                return {"part": part, "title": title, "ok": False, "text": str(detail or f"HTTP {r.status_code}")[:200]}
            return {"part": part, "title": title, "ok": True, "blank": _blank(part, d), "text": words(part, d), "data": d}

        out["parts"] = list(await asyncio.gather(*(one(*p) for p in PARTS)))
    return out


@router.get("")
async def here(request: Request, lat: float = Query(..., ge=49.5, le=61.0), lon: float = Query(..., ge=-8.7, le=2.0),
               parts: str | None = Query(None, description="only these parts, comma-separated")) -> dict[str, Any]:  # fmt: skip
    """Everything World Assistant knows about a point in Great Britain, each part with a sentence and its data."""
    out = await gather(request, lat, lon)
    if parts:
        want = {p.strip() for p in parts.split(",")}
        out["parts"] = [p for p in out["parts"] if p["part"] in want]
    return out


class SaveIn(BaseModel):
    lat: float = Field(ge=49.5, le=61.0)
    lon: float = Field(ge=-8.7, le=2.0)
    name: str = Field(min_length=1, max_length=80)
    kind: str = Field("other", max_length=20)


@router.post("/save", status_code=201)
async def save(request: Request, body: SaveIn) -> dict[str, Any]:
    """Keep the point as a saved place, so the rest of World Assistant (sensors, trips, the map) uses it too."""
    token = get_settings().api_token
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=request.app), base_url="http://wa.internal",
                                 headers=headers, timeout=20) as c:  # fmt: skip
        pid = re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-")[:40] or "here"
        have = {p["id"] for p in (await c.get("/v1/places")).json()}
        if pid in have:
            raise HTTPException(409, f"there is already a place called {pid!r}: choose another name")
        r = await c.put(f"/v1/places/{pid}", json={"name": body.name, "kind": body.kind, "lat": body.lat, "lon": body.lon})
    try:
        d = r.json()
    except ValueError:
        d = {}
    if r.status_code >= 400:
        raise HTTPException(r.status_code, d.get("detail") if isinstance(d, dict) else f"HTTP {r.status_code}")
    return d  # type: ignore[no-any-return]

