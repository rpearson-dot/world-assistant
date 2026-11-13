"""Here: what World Assistant knows about wherever you are, without saving it as a place first.

    GET  /v1/here?lat=&lon=          every source at once for a point: the area, buses and stations, fuel and chargers,
                                     floods, air, rivers and rain, tides, pollen, events, roads, power cuts, crime and food hygiene ratings
    POST /v1/here/save               keep the point as a saved place: {"lat", "lon", "name"}

Each part is World Assistant's own endpoint, asked for that point inside the server, so every source works exactly as
it does for a saved place. A source that is switched off, outside its area or failing says so in that part; the rest
still come back. The page's "Use my location" asks the browser, which only allows it over https (the Tailscale
address), not the plain address on the home network.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
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
    ("flows", "River flow", "/v1/environment/flows", {"km": 10}),
    ("rain", "Rain", "/v1/environment/rain", {"km": 10}),
    ("tides", "Tide", "/v1/environment/tides", {"km": 20}),
    ("overflows", "Storm overflows", "/v1/environment/overflows", {"km": 3}),
    ("pollen", "Pollen", "/v1/pollen/near", {}),
    ("fuel", "Fuel (E10)", "/v1/fuel/near", {"radius_km": 8, "limit": 3, "fuel": "E10"}),
    ("chargers", "EV chargers", "/v1/ev/sites", {"km": 15, "limit": 3}),
    ("events", "Events, next 3 days", "/v1/events/near", {"radius_km": 8, "days": 3, "limit": 5}),
    ("roads", "Roads (London)", "/v1/roads/tfl/at", {"radius_m": 2000}),
    ("power", "Power cuts", "/v1/power/cuts", {"km": 5, "postcode": "{pc}"}),
    ("crime", "Crime and police", "/v1/crime", {}),
    ("hygiene", "Food hygiene", "/v1/food/hygiene", {"miles": 0.5, "limit": 6}),
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
            parts.append(f"{name}, {round(s['km'] * 1000)} m: " + (", ".join(bus(b) for b in buses[:4]) if buses else "nothing due"))
        tail = f"\nStill looking up the timetable for {', '.join(d['loading'])}: ask again in a minute." if d.get("loading") else ""
        return "\n".join(parts) + tail
    if part == "bus_disruptions":
        x = d.get("disruptions") or []
        return "\n".join(f"{i.get('summary') or 'Disruption'}" + (f" ({', '.join(i['lines'][:4])})" if i.get("lines") else "")
                         for i in x[:3]) if x else "No bus disruptions published within 2 km."  # fmt: skip
    if part == "stations":
        s = d.get("stations") or []
        return "\n".join(f"{x['name']} ({_mi(x.get('km'))})" + (", closures reported" if x.get("closures") else "") for x in s) if s else "No London stations within 2 km."
    if part == "floods":
        w = d.get("warnings") or []
        return "\n".join(f"{x['severity']}: {x['area']}" for x in w[:3]) if w else "No flood warnings here."
    if part == "air":
        if d.get("index") is None:
            return "No air quality station near enough."
        return f"Index {d['index']} ({d.get('band', '').lower()})" + (f", worst {d['worst']}" if d.get("worst") else "") + "."
    if part == "rivers":
        r = d.get("rivers") or []
        return "\n".join(f"{x.get('river') or x.get('station')}: {x.get('state') or 'level ' + str(x.get('level_m'))}"
                          + (f", {x['trend']}" if x.get("trend") else "") for x in r[:3]) if r else "No river gauge within 5 km."  # fmt: skip
    if part == "flows":
        f = d.get("flows") or []
        return "\n".join(f"{x.get('river') or x.get('station')}: {x['flow_m3s']:g} m³/s" + (f", {x['state']} for there" if x.get("state") else "")
                          + (f" ({x['pct_of_mean']}% of its mean)" if x.get("pct_of_mean") is not None else "")
                          + (f", {x['trend']}" if x.get("trend") else "") for x in f[:3] if x.get("flow_m3s") is not None) or "No flow gauge within 10 km."  # fmt: skip
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
        return "Cheapest:\n" + "\n".join(f"{x.get('brand') or x.get('name')}, {x['pence']:.1f}p, {_mi(x.get('distance_km'))}" for x in s) if s else "No E10 prices within 5 miles."
    if part == "chargers":
        s = d.get("sites") or []
        return "\n".join(x.get("summary") or x.get("name", "") for x in s) if s else "No GRIDSERVE chargers within 10 miles."
    if part == "events":
        e = d.get("events") or []
        return ("\n".join(f"{x.get('name')}" + (f" at {x['venue']}" if x.get("venue") else "") + (f", {x['date']}" if x.get("date") else "") for x in e[:5])
                + (f"\nand {d['total'] - 5} more" if (d.get("total") or 0) > 5 else "")
                + ("\nListings include Data Thistle's." if d.get("attribution") else "")) if e else "Nothing listed nearby in the next 3 days."  # fmt: skip
    if part == "roads":
        r = d.get("disruptions") or []
        return "\n".join(f"{x.get('severity')}: {x.get('location') or x.get('category')}" for x in r[:3]) if r else "No TfL road disruption within 2 km."
    if part == "power":
        c = d.get("cuts") or []
        return (f"{len(c)} power cut{'s' if len(c) != 1 else ''} within 3 miles" + (f", {d['at_place']} here" if d.get("at_place") else "") + "."
                if c else "No power cuts reported nearby.")  # fmt: skip
    if part in ("crime", "hygiene"):
        return str(d.get("text") or "")
    return ""


def _blank(part: str, d: dict[str, Any]) -> bool:
    """Nothing worth showing: a part outside its area (London-only sources away from London)."""
    return part in ("stations", "roads") and not (d.get("stations") or d.get("disruptions"))


REFUSED = re.compile(r"HTTP (401|403|429)\b|refused|forbidden|too many requests|not allowed|denied|unauthori", re.IGNORECASE)
NO_ANSWER = re.compile(r"did not answer|timed? ?out|could not be reached|HTTP (502|503|504)\b|no answer|unavailable", re.IGNORECASE)


def status_of(code: int, text: str) -> str:
    """What a failed part's answer means: not_set_up (it needs a key), refused (the source said no: a key, a limit),
    no_answer (it did not answer in time or at all), or error (anything else)."""
    if "not set up" in text.lower() or "is set up" in text.lower():
        return "not_set_up"
    if code in (401, 403, 429) or REFUSED.search(text):
        return "refused"
    if code in (502, 503, 504) and NO_ANSWER.search(text) or code == 504:
        return "no_answer"
    return "error"


AT_PLACE_KM = 0.15  # within this of a saved place, Here is "at" it


def saved_place(places: list[dict[str, Any]], lat: float, lon: float) -> dict[str, Any] | None:
    """The nearest saved place within AT_PLACE_KM of the point, if any."""
    from app.geoquery import haversine_km

    best = None
    for p in places:
        try:
            km = haversine_km((lat, lon), (float(p["lat"]), float(p["lon"])))
        except (KeyError, TypeError, ValueError):
            continue
        if km <= AT_PLACE_KM and (best is None or km < best["km"]):
            best = {"id": p.get("id"), "name": p.get("name") or p.get("id"), "kind": p.get("kind"), "km": round(km, 3)}
    return best


# In-process calls ignore httpx's timeout, so each part has its own deadline: one slow outside source must not hold up
# the rest (or the page). Kept well inside the 30 seconds callers such as the smoke check wait.
AREA_S = 5.0
PART_S = 15.0


STREAM_PART_S = 45.0  # the page shows each part as it comes, so it can wait longer for a slow one


async def _head(c: httpx.AsyncClient, lat: float, lon: float) -> dict[str, Any]:
    """The point's area (its postcode), the saved place it is (if any) and its three words."""
    out: dict[str, Any] = {"lat": round(lat, 5), "lon": round(lon, 5), "area": None, "place": None}
    try:
        pc = await asyncio.wait_for(c.get("/v1/postcodes/near", params={"lat": lat, "lon": lon, "radius_km": 1, "limit": 1}),
                                    AREA_S)  # fmt: skip
        out["area"] = (pc.json().get("postcodes") or [None])[0]
    except (httpx.HTTPError, ValueError, TimeoutError):
        pass
    try:  # one of your saved places? Then Here says so (everything is still asked for the point)
        places = (await asyncio.wait_for(c.get("/v1/places"), AREA_S)).json()
        out["place"] = saved_place(places if isinstance(places, list) else [], lat, lon)
    except (httpx.HTTPError, ValueError, TimeoutError):
        out["place"] = None
    out["what3words"] = await _three_words(lat, lon)
    return out


async def _three_words(lat: float, lon: float) -> dict[str, Any] | None:
    from app.integrations.what3words import client as W3W

    if not get_settings().what3words_api_key:
        return None
    try:
        return await asyncio.wait_for(W3W.to_words(lat, lon), AREA_S)
    except TimeoutError:
        return {"error": "what3words did not answer in time"}
    except W3W.What3WordsError as exc:
        return {"error": exc.brief, "detail": str(exc)}


async def _one(c: httpx.AsyncClient, lat: float, lon: float, postcode: str, part: str, title: str, path: str,
               extra: dict[str, Any], deadline: float) -> dict[str, Any]:  # fmt: skip
    """One part, asked inside the server, with its status: ok, off, no_answer, refused or error."""
    q = {"lat": lat, "lon": lon, **{k: (postcode if v == "{pc}" else v) for k, v in extra.items()}}
    q = {k: v for k, v in q.items() if v != ""}
    try:
        r = await asyncio.wait_for(c.get(path, params=q), deadline)
    except TimeoutError:
        return {"part": part, "title": title, "ok": False, "slow": True, "status": "no_answer",
                "text": f"No answer within {deadline:.0f} seconds: ask again in a minute."}  # fmt: skip
    except httpx.HTTPError as exc:
        return {"part": part, "title": title, "ok": False, "status": "no_answer", "text": f"Could not ask ({type(exc).__name__})."}
    if r.status_code == 404 and "off" in r.text.lower():
        return {"part": part, "title": title, "ok": False, "off": True, "status": "off", "text": "Switched off."}
    try:
        d = r.json()
    except ValueError:
        d = {}
    if r.status_code >= 400:
        detail = str((d.get("detail") if isinstance(d, dict) else None) or f"HTTP {r.status_code}")[:200]
        st = status_of(r.status_code, detail)
        lead = {"refused": "Refused: ", "no_answer": "No answer: "}.get(st, "")
        return {"part": part, "title": title, "ok": False, "status": st, "text": lead + detail}
    return {"part": part, "title": title, "ok": True, "status": "ok", "blank": _blank(part, d), "text": words(part, d), "data": d}


def _client(request: Request) -> httpx.AsyncClient:
    token = get_settings().api_token
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=request.app), base_url="http://wa.internal", headers=headers, timeout=60)


async def gather(request: Request, lat: float, lon: float, want: set[str] | None = None, deadline: float | None = None) -> dict[str, Any]:
    """Every part for a point (or those in `want`), asked at once inside the server."""
    deadline = deadline or PART_S
    async with _client(request) as c:
        out = await _head(c, lat, lon)
        postcode = (out["area"] or {}).get("postcode") or ""
        todo = [p for p in PARTS if want is None or p[0] in want]
        out["parts"] = list(await asyncio.gather(*(_one(c, lat, lon, postcode, *p, deadline) for p in todo)))
    return out


async def stream(request: Request, lat: float, lon: float) -> AsyncIterator[bytes]:
    """The head first, then each part as soon as it answers (newline-delimited JSON), then "done"."""
    async with _client(request) as c:
        head = await _head(c, lat, lon)
        yield (json.dumps({"type": "head", **head, "parts": [{"part": p[0], "title": p[1]} for p in PARTS],
                           "wait_s": STREAM_PART_S}) + "\n").encode()  # fmt: skip
        postcode = (head["area"] or {}).get("postcode") or ""
        tasks = [asyncio.create_task(_one(c, lat, lon, postcode, *p, STREAM_PART_S)) for p in PARTS]
        try:
            for t in asyncio.as_completed(tasks):
                yield (json.dumps({"type": "part", **await t}, default=str) + "\n").encode()
        finally:
            for t in tasks:
                t.cancel()
    yield b'{"type": "done"}\n'


@router.get("")
async def here(request: Request, lat: float | None = Query(None, ge=49.5, le=61.0),
               lon: float | None = Query(None, ge=-8.7, le=2.0),
               postcode: str | None = Query(None, max_length=10, description="instead of lat and lon"),
               words: str | None = Query(None, max_length=80, description="what3words, instead of lat and lon"),
               parts: str | None = Query(None, description="only these parts, comma-separated")) -> dict[str, Any]:  # fmt: skip
    """Everything World Assistant knows about a point in Great Britain (or a postcode's centre), each part with a
    sentence, its status (ok, off, no_answer, refused, error) and its data."""
    lat, lon = await resolve(request, lat, lon, postcode, words)
    want = {p.strip() for p in parts.split(",")} if parts else None
    return await gather(request, lat, lon, want)


@router.get("/stream")
async def here_stream(request: Request, lat: float | None = Query(None, ge=49.5, le=61.0),
                      lon: float | None = Query(None, ge=-8.7, le=2.0), postcode: str | None = Query(None, max_length=10),
                      words: str | None = Query(None, max_length=80)) -> StreamingResponse:  # fmt: skip
    """As /v1/here, but each part as soon as it answers (newline-delimited JSON: a "head", each "part", then "done"),
    each waiting up to 45 seconds. The Here page uses this."""
    lat, lon = await resolve(request, lat, lon, postcode, words)
    return StreamingResponse(stream(request, lat, lon), media_type="application/x-ndjson", headers={"Cache-Control": "no-store",
                                                                                                    "X-Accel-Buffering": "no"})


async def resolve(request: Request, lat: float | None, lon: float | None, postcode: str | None,
                  words: str | None) -> tuple[float, float]:  # fmt: skip
    """The point asked about: lat and lon, a postcode's centre, or what3words' square."""
    from app.integrations.what3words import client as W3W

    if (lat is None or lon is None) and (words or W3W.words_in(postcode or "")):
        try:
            got3 = await W3W.to_place(words or postcode or "", getattr(request.app.state, "redis", None))
        except W3W.What3WordsError as exc:
            raise HTTPException(404 if "does not know" in str(exc) else 422, str(exc)) from exc
        lat, lon = got3["lat"], got3["lon"]
        if lat is None or lon is None:
            raise HTTPException(404, "what3words gave no place for those words.")
        if not (49.5 <= lat <= 61.0 and -8.7 <= lon <= 2.0):
            near = f" (near {got3['nearest']})" if got3.get("nearest") else ""
            raise HTTPException(422, f"///{got3['words']} is outside Great Britain{near}.")
    if lat is None or lon is None:
        if not (postcode or "").strip():
            raise HTTPException(422, "Give lat and lon, a postcode, or three words (///filled.count.soap).")
        from app.geo import PostcodeError, lookup_postcode
        from app.modules.codepoint.postcodes import local_postcode

        got = await local_postcode(request.app.state.engine, postcode.strip())
        if got is None:  # not in the local table (new, or the table not built): postcodes.io, which says why not
            try:
                got = await lookup_postcode(postcode.strip())
            except PostcodeError as exc:
                raise HTTPException(404, f"Could not find the postcode {postcode.strip().upper()!r} ({exc}).") from exc
        lat, lon = got.lat, got.lon
        if not (49.5 <= lat <= 61.0 and -8.7 <= lon <= 2.0):
            raise HTTPException(422, "That postcode is outside Great Britain.")
    return lat, lon


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

