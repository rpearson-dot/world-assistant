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
    source: str = Query("all", pattern="^(all|skiddle|ticketmaster)$"),
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
) -> dict[str, Any]:
    """Events from every configured source (Skiddle, Ticketmaster), merged and sorted by date, cached for
    cache_seconds. Shared by /near and the Home Assistant locations."""
    s = get_settings()
    wanted = [n for n in ("skiddle", "ticketmaster") if source in ("all", n)]
    have = {"skiddle": bool(s.skiddle_api_key), "ticketmaster": bool(s.ticketmaster_api_key)}
    active = [n for n in wanted if have[n]]
    if not active:
        raise HTTPException(
            503, "no events source is set up: add skiddle_api_key and/or ticketmaster_api_key"
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

    jobs = {"skiddle": from_skiddle, "ticketmaster": from_ticketmaster}
    results = await asyncio.gather(*(jobs[n]() for n in active), return_exceptions=True)
    events: list[dict[str, Any]] = []
    total = 0
    errors: list[str] = []
    for src, res in zip(active, results, strict=True):
        if isinstance(res, (SkiddleError, TicketmasterError)):
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
    for (const e of d.events) {
      const c = el('div', 'card');
      c.append(el('h2', null, [e.date, e.doors_open, e.type].filter(Boolean).join('  ·  ')));
      c.append(el('p', 'big', e.name || '(unnamed)'));
      c.append(el('p', 'dim', [e.venue, e.town, e.distance_km != null ? e.distance_km.toFixed(1) + ' km' : null, e.availability, e.price, e.source === 'ticketmaster' ? 'Ticketmaster' : 'Skiddle'].filter(Boolean).join('  ·  ')));
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
