"""What's on near a saved place: live Skiddle search, cached briefly. Nothing is stored."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.auth import require_token
from app.config import get_settings
from app.integrations.datathistle.client import ATTRIBUTION as DT_ATTRIBUTION
from app.integrations.datathistle.client import DataThistleClient, DataThistleError
from app.integrations.datathistle.client import _state as dt_state
from app.integrations.datathistle.client import keep_alive as dt_keep_alive
from app.integrations.skiddle.client import DEFAULT_BASE, EVENT_CODES, SkiddleClient, SkiddleError
from app.integrations.ticketmaster.client import (
    DEFAULT_BASE as TM_BASE,
)
from app.integrations.ticketmaster.client import TicketmasterClient, TicketmasterError
from app.pagestyle import themed
from app.places import place_or_default
from app.ratelimit import SlidingWindowLimiter

router = APIRouter(prefix="/v1/events", tags=["events"], dependencies=[Depends(require_token)])
CACHE_SECONDS = 600


def _client(request: Request) -> SkiddleClient:
    s = get_settings()
    limiter = SlidingWindowLimiter(request.app.state.redis, "skiddle", 30)
    return SkiddleClient(s.skiddle_api_key, s.skiddle_base_url or DEFAULT_BASE, limiter=limiter)


def _tm_client(request: Request) -> TicketmasterClient:
    s = get_settings()
    limiter = SlidingWindowLimiter(request.app.state.redis, "ticketmaster", 240)
    return TicketmasterClient(s.ticketmaster_api_key, s.ticketmaster_base_url or TM_BASE, limiter=limiter)


DT_DAYS = 14  # one Data Thistle search covers the next two weeks; narrower asks are filtered from it
DT_KEEP_S = 12 * 3600  # its terms allow up to 24 hours


async def datathistle_window(redis: Any, lat: float, lon: float, radius_km: float, today: Any,
                             engine: Any = None) -> list[dict[str, Any]]:  # fmt: skip
    """Data Thistle's events for the next two weeks near a point, shared by every search there for 12 hours, and kept
    within the monthly allowance (`datathistle_monthly_limit`)."""
    s = get_settings()
    rk = 5 if radius_km <= 5 else 10 if radius_km <= 10 else 25
    key = f"wa:datathistle:{lat:.2f}:{lon:.2f}:{rk}:{today.isoformat()}"
    month = f"wa:datathistle:used:{today.strftime('%Y-%m')}"
    try:
        hit = await redis.get(key)
        if hit:
            return list(json.loads(hit))
        used = int(await redis.get(month) or 0)
    except Exception:  # noqa: BLE001 - without the cache there is no count either: ask, but only once a call
        used = 0
    if used >= s.datathistle_monthly_limit:
        raise DataThistleError(f"this month's Data Thistle allowance is used ({used} of {s.datathistle_monthly_limit}); "
                               "it comes back on the 1st")  # fmt: skip
    got: dict[str, Any] = {}
    for attempt in (1, 2):  # a refused token is renewed once, then asked again
        async with DataThistleClient(get_settings().datathistle_token) as c:
            try:
                got = await c.search(lat, lon, radius_km=rk, first=today, last=today + timedelta(days=DT_DAYS), limit=200)
                break
            except DataThistleError as exc:
                if attempt == 2 or engine is None or "refused the token" not in str(exc):
                    raise
                if not (await dt_keep_alive(engine, refused=True)).get("renewed"):
                    raise
            finally:
                with contextlib.suppress(Exception):
                    await redis.incrby(month, c.requests)
                    await redis.expire(month, 40 * 86400)
    with contextlib.suppress(Exception):
        await redis.set(key, json.dumps(got["events"]), ex=DT_KEEP_S)
    return list(got["events"])


def _sort_key(e: dict[str, Any]) -> tuple[str, str]:
    return (e.get("date") or "9999", e.get("doors_open") or "")


async def _place(request: Request, place: str) -> tuple[str, float, float]:
    async with request.app.state.engine.connect() as conn:
        row = (
            await conn.execute(text("SELECT name, lat, lon FROM place WHERE id = :p"), {"p": place})
        ).first()
    if row is None:
        raise HTTPException(404, f"no place {place!r}")
    return row[0], row[1], row[2]


@router.get("/near")
async def near(
    request: Request,
    place: str | None = Query(None, description="id of a saved place; default: home"),
    lat: float | None = Query(None, ge=49.5, le=61.0, description="with lon: use this point, not a place"),
    lon: float | None = Query(None, ge=-8.7, le=2.0),
    radius_km: float = Query(8.0, gt=0, le=50),
    days: int = Query(7, ge=1, le=60, description="how many days ahead, starting today"),
    type: str | None = Query(None, description=f"one of {', '.join(EVENT_CODES)}"),
    keyword: str | None = Query(None, max_length=80),
    limit: int = Query(20, ge=1, le=100),
    source: str = Query("all", pattern="^(all|skiddle|ticketmaster|datathistle)$"),
) -> dict[str, Any]:
    """Events from every configured source (Skiddle, Ticketmaster), merged and sorted by date."""
    if (lat is None) != (lon is None):
        raise HTTPException(422, "give both lat and lon, or neither")
    if lat is not None and lon is not None:
        name = "your location"  # a point from the caller (Home Assistant): no place needed
    else:
        place = await place_or_default(request, place)
        name, lat, lon = await _place(request, place)
    return await search(
        request, name, lat, lon, radius_km=radius_km, days=days, type=type, keyword=keyword, limit=limit, source=source
    )


async def search(
    request: Request, name: str, lat: float, lon: float, *, radius_km: float, days: int, type: str | None,
    keyword: str | None, limit: int, source: str = "all", cache_seconds: int = CACHE_SECONDS,
    datathistle: bool = True,
) -> dict[str, Any]:
    """Events from every configured source (Skiddle, Ticketmaster, Data Thistle), merged and sorted by date, cached
    for cache_seconds (Data Thistle's terms allow up to a day). Shared by /near and the Home Assistant locations."""
    s = get_settings()
    wanted = [n for n in ("skiddle", "ticketmaster", "datathistle") if source in ("all", n)
              and (datathistle or n != "datathistle")]  # Home Assistant's repeating refreshes leave Data Thistle out
    have = {"skiddle": bool(s.skiddle_api_key), "ticketmaster": bool(s.ticketmaster_api_key),
            "datathistle": bool(s.datathistle_token)}  # fmt: skip
    active = [n for n in wanted if have[n]]
    if not active:
        raise HTTPException(
            503, "no events source is set up: add skiddle_api_key, ticketmaster_api_key or datathistle_token"
        )
    zone = ZoneInfo(s.local_tz)
    today = datetime.now(zone).date()
    last = today + timedelta(days=days)
    key = "wa:events:" + hashlib.sha256(
        json.dumps([lat, lon, radius_km, days, type, keyword, limit, today.isoformat(), active]).encode()
    ).hexdigest()[:24]
    redis = request.app.state.redis
    try:
        cached = await redis.get(key)
    except Exception:  # noqa: BLE001 - the cache is optional
        cached = None
    if cached:
        return {**json.loads(cached), "cached": True}

    async def from_skiddle() -> dict[str, Any]:
        async with _client(request) as c:
            return await c.search(
                lat, lon, radius_km=radius_km, min_date=today, max_date=last,
                event_code=type, keyword=keyword, limit=limit,
            )  # fmt: skip

    async def from_ticketmaster() -> dict[str, Any]:
        start = datetime.combine(today, time.min, zone).astimezone(UTC)
        end = datetime.combine(last, time.max, zone).astimezone(UTC)
        async with _tm_client(request) as c:
            return await c.search(
                lat, lon, radius_km=radius_km, start=start, end=end, event_type=type,
                keyword=keyword, limit=limit,
            )  # fmt: skip

    async def from_datathistle() -> dict[str, Any]:
        found = await datathistle_window(redis, lat, lon, radius_km, today, getattr(request.app.state, "engine", None))
        found = [e for e in found if e["date"] <= last.isoformat() and (not type or e["type"] == type)
                 and (e["distance_km"] is None or e["distance_km"] <= radius_km)
                 and (not keyword or keyword.lower() in f"{e['name']} {e['description'] or ''}".lower())]  # fmt: skip
        return {"total": len(found), "events": found[:limit]}

    jobs = {"skiddle": from_skiddle, "ticketmaster": from_ticketmaster, "datathistle": from_datathistle}
    results = await asyncio.gather(*(jobs[n]() for n in active), return_exceptions=True)
    events: list[dict[str, Any]] = []
    total = 0
    errors: list[str] = []
    for src, res in zip(active, results, strict=True):
        if isinstance(res, (SkiddleError, TicketmasterError, DataThistleError)):
            errors.append(f"{src}: {res}")
        elif isinstance(res, BaseException):
            raise res
        else:
            total += res["total"]
            events += [{**e, "source": src} for e in res["events"]]
    if errors and len(errors) == len(active):
        raise HTTPException(502, "; ".join(errors))
    events.sort(key=_sort_key)
    out = {
        "place": name, "radius_km": radius_km, "days": days, "type": type, "count": len(events[:limit]),
        "total": total, "events": events[:limit], "sources": active, "errors": errors,
    }  # fmt: skip
    if "datathistle" in active:
        out["attribution"] = [DT_ATTRIBUTION]
        with contextlib.suppress(Exception):
            used = int(await redis.get(f"wa:datathistle:used:{today.strftime('%Y-%m')}") or 0)
            out["datathistle"] = {"used_this_month": used, "limit": s.datathistle_monthly_limit}
            auth = await dt_state(request.app.state.engine)
            if auth.get("error"):
                out["datathistle"]["warning"] = f"Token renewal failed: {auth['error']}"
    if not errors:
        with contextlib.suppress(Exception):  # the cache is optional
            await redis.set(key, json.dumps(out), ex=cache_seconds)
    return {**out, "cached": False}


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant events</title>
<style>
/*BASE_CSS*/</style></head><body><main>
<!--NAV-->
<h1>What's on</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary" type="button">Load</button></div>
<div class="card"><div class="row">
  <label>Near<select id="place"></select></label>
  <label>Radius km<input id="radius" type="number" value="8" min="1" max="50"></label>
  <label>Next days<input id="days" type="number" value="7" min="1" max="60"></label>
  <label>Type<select id="type"><option value="">Anything</option></select></label>
  <label>Word<input id="kw" placeholder="optional" maxlength="80"></label>
  <button id="search" class="primary" type="button">Search</button></div>
  <p id="note" class="dim"></p></div>
<p id="msg" class="err"></p>
<div id="list"></div>
<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
const TYPES = {LIVE:'Live music', CLUB:'Club nights', FEST:'Festivals', COMEDY:'Comedy', THEATRE:'Theatre', SPORT:'Sport',
  KIDS:'Kids', ARTS:'Arts', EXPER:'Days out', EXHIB:'Exhibitions', BARPUB:'Bar and pub', DATE:'Dating', LGB:'LGBT'};
for (const [k, v] of Object.entries(TYPES)) $('type').append(new Option(v, k));
async function get(url) {
  const r = await fetch(url, { headers: { Authorization: 'Bearer ' + tok.value } });
  if (!r.ok) { let d = r.status; try { d = (await r.json()).detail; } catch (e) {} throw new Error(typeof d === 'string' ? d : url + ' returned ' + r.status); }
  return r.json();
}
async function load() {
  msg.textContent = '';
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  try { const places = await get('/v1/places'); $('place').replaceChildren(...places.map(p => new Option(p.name, p.id))); $('place').value = homeId(places) || ''; }
  catch (e) { msg.textContent = e.message; }
}
const homeId = ps => (ps.find(p => p.kind === 'home') || ps.find(p => p.id === 'home') || ps[0] || {}).id;
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
async function search() {
  msg.textContent = ''; $('list').replaceChildren();
  if (!$('place').value) { msg.textContent = 'Add a place on /places first.'; return; }
  const p = new URLSearchParams({ place: $('place').value, radius_km: $('radius').value, days: $('days').value });
  if ($('type').value) p.set('type', $('type').value);
  if ($('kw').value.trim()) p.set('keyword', $('kw').value.trim());
  $('note').textContent = 'Searching...';
  try {
    const d = await get('/v1/events/near?' + p);
    $('note').textContent = d.count + ' of ' + d.total + ' events near ' + d.place + ' in the next ' + d.days + ' days. Sources: ' + d.sources.join(', ') + '.' + (d.errors.length ? ' Problem: ' + d.errors.join('; ') : '');
    (d.attribution || []).forEach(a => { const p = el('p', 'dim'), l = el('a', null, 'Listings from ' + a.name);
      l.href = a.url; l.target = '_blank'; l.rel = 'noopener noreferrer';
      const add = el('a', null, 'add or update an event'); add.href = a.add_event; add.target = '_blank'; add.rel = 'noopener noreferrer';
      p.append(l, ' · ', add); $('note').append(p); });
    if (d.datathistle && d.datathistle.warning) $('note').append(el('p', 'err', d.datathistle.warning));
    for (const e of d.events) {
      const c = el('div', 'card');
      c.append(el('h2', null, [e.date, e.doors_open, e.type].filter(Boolean).join('  ·  ')));
      c.append(el('p', 'big', e.name || '(unnamed)'));
      c.append(el('p', 'dim', [e.venue, e.town, e.distance_km != null ? e.distance_km.toFixed(1) + ' km' : null, e.availability, e.price, ({ ticketmaster: 'Ticketmaster', datathistle: 'Data Thistle' }[e.source] || 'Skiddle')].filter(Boolean).join('  ·  ')));
      if (e.url) { const a = el('a', null, 'Details and tickets'); a.href = e.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; c.append(a); }
      $('list').append(c);
    }
    if (!d.count) msg.textContent = 'Nothing found. Try more days or a wider radius.';
  } catch (e) { $('note').textContent = ''; msg.textContent = e.message; }
}
$('go').onclick = load; $('search').onclick = search;
if (tok.value) load();
</script></main></body></html>"""

page_router = APIRouter()


@page_router.get("/events", response_class=HTMLResponse, include_in_schema=False)
async def events_page(request: Request) -> str:
    """The page holds no data; it asks for the token and calls /v1/events."""
    return themed(PAGE, "events", request)
