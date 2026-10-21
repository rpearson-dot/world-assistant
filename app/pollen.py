"""Pollen and allergy risk near a saved place: Polleninformation forecast, cached for the day."""

from __future__ import annotations

import contextlib
import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.auth import require_token
from app.config import get_settings
from app.integrations.pollen.client import ATTRIBUTION, DEFAULT_BASE, PollenClient, PollenError
from app.pagestyle import themed
from app.places import place_or_default
from app.pollen_budget import PollenLimiter
from app.pollen_grid import current, nearest
from app.pollen_grid import load as load_grid
from app.pollen_history import CACHE_SECONDS, KEEP_DAYS, cache_key, history, record

router = APIRouter(prefix="/v1/pollen", tags=["pollen"], dependencies=[Depends(require_token)])


def _client(request: Request) -> PollenClient:
    s = get_settings()
    limiter = PollenLimiter(request.app.state.redis, s.pollen_daily_limit)
    return PollenClient(s.pollen_api_key, s.pollen_base_url or DEFAULT_BASE, limiter=limiter)


async def _place(request: Request, place: str) -> tuple[str, float, float]:
    async with request.app.state.engine.connect() as conn:
        row = (
            await conn.execute(text("SELECT name, lat, lon FROM place WHERE id = :p"), {"p": place})
        ).first()
    if row is None:
        raise HTTPException(404, f"no place {place!r}")
    return row[0], row[1], row[2]


@router.get("/grid")
async def grid(request: Request) -> dict[str, Any]:
    """The stored forecast for every point of the nationwide lattice (what Home Assistant filters)."""
    points = current(await load_grid(request.app.state.engine))  # each cut to start today
    out = []
    for p in points:
        f = p["forecast"]
        out.append({
            "lat": p["lat"], "lon": p["lon"], "fetched_at": p["fetched_at"], "changed_at": p["changed_at"],
            "dates": f.get("dates"), "allergy_risk": f.get("allergy_risk"),
            "allergy_risk_label": f.get("allergy_risk_label"),
            "allergens": [{"name": a.get("name"), "levels": a.get("levels"), "labels": a.get("labels")}
                          for a in f.get("allergens") or []],
        })  # fmt: skip
    return {"source": ATTRIBUTION, "count": len(out), "points": out}


@router.get("/near")
async def near(
    request: Request,
    place: str | None = Query(None, description="id of a saved place; default: home"),
    lat: float | None = Query(None, ge=49.5, le=61.0, description="with lon: use this point, not a place"),
    lon: float | None = Query(None, ge=-8.7, le=2.0),
    hourly: bool = Query(False, description="include the hourly allergy risk for each day"),
) -> dict[str, Any]:
    """Pollen levels by allergen for today and the next three days, plus the allergy risk."""
    s = get_settings()
    if not s.pollen_api_key:
        raise HTTPException(503, "pollen is not set up: add the pollen_api_key secret")
    if (lat is None) != (lon is None):
        raise HTTPException(422, "give both lat and lon, or neither")
    if lat is not None and lon is not None:
        place, name = None, "your location"  # a point from the caller (Home Assistant): no place needed
        with contextlib.suppress(Exception):  # the stored lattice is an optimisation; fall back to a live fetch
            hit = nearest(await load_grid(request.app.state.engine), lat, lon)
            if hit:
                point, dist = hit
                fc = point["forecast"]
                out = {"source": ATTRIBUTION, **fc, "cached": True,
                       "grid": {"distance_km": round(dist), "fetched_at": point["fetched_at"],
                                "changed_at": point["changed_at"]}}  # fmt: skip
                allergens = sorted(fc.get("allergens") or [], key=lambda a: -((a.get("levels") or [0])[0] or 0))
                return {"place": name, **out, "allergens": allergens}
    else:
        place = await place_or_default(request, place)
        name, lat, lon = await _place(request, place)
    today = datetime.now(ZoneInfo(s.local_tz)).date()
    key = cache_key(lat, lon, s.pollen_country, today)
    redis = request.app.state.redis
    try:
        cached = await redis.get(key)
    except Exception:  # noqa: BLE001 - the cache is optional
        cached = None
    if cached:
        out = {**json.loads(cached), "cached": True}
    else:
        try:
            async with _client(request) as c:
                fc = await c.forecast(lat, lon, today, country=s.pollen_country)
        except PollenError as exc:
            raise HTTPException(502, str(exc)) from exc
        out = {"source": ATTRIBUTION, **fc}
        with contextlib.suppress(Exception):  # the cache is optional
            await redis.set(key, json.dumps(out), ex=CACHE_SECONDS)
        with contextlib.suppress(Exception):  # history is a bonus; never fail the request for it
            if place:
                await record(request.app.state.engine, place, today, fc)
        out["cached"] = False
    if not hourly:
        out = {k: v for k, v in out.items() if k != "allergy_risk_hourly"}
    allergens = sorted(out["allergens"], key=lambda a: -(a["levels"][0] or 0))
    return {"place": name, **out, "allergens": allergens}


@router.get("/history")
async def pollen_history(
    request: Request,
    place: str | None = Query(None, description="id of a saved place; default: home"),
    days: int = Query(KEEP_DAYS, ge=1, le=KEEP_DAYS),
) -> dict[str, Any]:
    """Recorded pollen levels per day (up to 90 days): each allergen, their total, and the allergy risk."""
    place = await place_or_default(request, place)
    name, _lat, _lon = await _place(request, place)
    today = datetime.now(ZoneInfo(get_settings().local_tz)).date()
    h = await history(request.app.state.engine, place, today, days)
    return {"place": name, "source": ATTRIBUTION, **h}


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant pollen</title>
<style>
/*BASE_CSS*/
  .lv0 { background: var(--chip); } .lv1 { background: var(--good-bg); } .lv2 { background: #f6e7b4; color: #4a3b00; }
  .lv3 { background: #f4c28f; color: #4a2300; } .lv4 { background: #eba0a0; color: #4a0000; }
  .days { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; }
  .days div { border-radius: 8px; padding: 8px; text-align: center; font-size: 14px; }
  .days small { display: block; opacity: .75; }
  svg.chart { width: 100%; height: auto; display: block; }
  svg.chart text { fill: var(--dim); font-size: 11px; } svg.chart .grid { stroke: var(--line); stroke-width: 1; }
  .legend { margin: 8px 0 0; } .legend label { display: inline-flex; flex-direction: row; align-items: center; gap: 5px;
    margin: 3px 14px 3px 0; font-weight: 400; font-size: 13px; color: var(--ink); }
  .legend input { padding: 0; } .legend i { width: 14px; height: 4px; border-radius: 2px; display: inline-block; }
</style></head><body><main>
<!--NAV-->
<h1>Pollen</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary" type="button">Load</button></div>
<div class="card"><div class="row">
  <label>Near<select id="place"></select></label>
  <button id="search" class="primary" type="button">Show</button></div>
  <p id="note" class="dim"></p></div>
<p id="msg" class="err"></p>
<div id="list"></div>
<div id="hist" hidden>
  <div class="card"><h2>History: total pollen</h2>
    <p class="dim">All allergens added together, one value a day (0 to 4 for each, so at most 4 times the number of allergens).</p>
    <div id="c_total"></div></div>
  <div class="card"><h2>History: each pollen</h2>
    <p class="dim">0 none, 1 low, 2 moderate, 3 high, 4 very high. Allergy risk is shown on the same scale.</p>
    <div id="c_each"></div><div id="legend" class="legend"></div></div>
  <p id="hnote" class="dim"></p>
</div>
<p class="dim">Source: Austrian Pollen Information Service, www.polleninformation.at</p>
<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
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
const DAY = d => new Date(d + 'T12:00:00').toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' });
function row(title, dates, levels, labels, extra) {
  const c = el('div', 'card'); c.append(el('h2', null, title));
  const g = el('div', 'days');
  levels.forEach((lv, i) => {
    const d = el('div', 'lv' + (lv == null ? 0 : lv), labels[i] || 'no data');
    d.prepend(el('small', null, DAY(dates[i]) + (extra ? ' · ' + extra[i] : '')));
    g.append(d);
  });
  c.append(g); return c;
}

const COLORS = ['#1f6feb', '#d9480f', '#2f9e44', '#9c36b5', '#e8a200', '#0c8599', '#c2255c', '#6b7280'];
const NS = 'http:' + '//www.w3.org/2000/svg';  // an XML namespace name, not a request
function sv(tag, attrs, text) { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (text != null) e.textContent = text; return e; }
function chart(box, days, lines, ymax) {
  const W = 720, H = 230, L = 34, R = 10, T = 10, B = 24, n = days.length;
  const svg = sv('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'chart', role: 'img' });
  const x = i => L + (n < 2 ? (W - L - R) / 2 : i * (W - L - R) / (n - 1));
  const y = v => T + (H - T - B) * (1 - v / ymax);
  const step = ymax <= 5 ? 1 : Math.ceil(ymax / 6);
  for (let v = 0; v <= ymax; v += step) {
    svg.append(sv('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), class: 'grid' }));
    svg.append(sv('text', { x: L - 6, y: y(v) + 4, 'text-anchor': 'end' }, String(v)));
  }
  const ticks = Math.min(6, n);
  for (let k = 0; k < ticks; k++) {
    const i = ticks === 1 ? 0 : Math.round(k * (n - 1) / (ticks - 1));
    const d = new Date(days[i] + 'T12:00:00');
    svg.append(sv('text', { x: x(i), y: H - 6, 'text-anchor': k === 0 ? 'start' : (k === ticks - 1 ? 'end' : 'middle') }, d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })));
  }
  for (const ln of lines) {
    let seg = [];
    const flush = () => { if (seg.length > 1) svg.append(sv('polyline', { points: seg.join(' '), fill: 'none', stroke: ln.color, 'stroke-width': ln.width || 2, 'stroke-linejoin': 'round' })); seg = []; };
    ln.values.forEach((v, i) => {
      if (v == null) { flush(); return; }
      seg.push(x(i).toFixed(1) + ',' + y(Math.min(v, ymax)).toFixed(1));
    });
    flush();
    ln.values.forEach((v, i) => {
      if (v == null) return;
      const c = sv('circle', { cx: x(i), cy: y(Math.min(v, ymax)), r: n > 45 ? 2 : 3, fill: ln.color });
      c.append(sv('title', {}, ln.name + ', ' + days[i] + ': ' + (Math.round(v * 10) / 10)));
      svg.append(c);
    });
  }
  box.replaceChildren(svg);
}
let HIST = null, OFF = new Set();
function drawEach() {
  const lines = HIST.allergens.map((a, i) => ({ name: a.name || a.id, color: COLORS[i % COLORS.length], values: a.values, key: a.id }))
    .filter(l => !OFF.has(l.key));
  if (!OFF.has('_risk')) lines.push({ name: 'Allergy risk (0-10 scaled to 0-4)', color: 'var(--ink)', width: 1.5, key: '_risk',
    values: HIST.allergy_risk.map(v => v == null ? null : v / 2.5) });
  chart($('c_each'), HIST.days, lines, 4);
}
async function loadHistory() {
  try {
    HIST = await get('/v1/pollen/history?place=' + encodeURIComponent($('place').value));
  } catch (e) { $('hist').hidden = true; return; }
  $('hist').hidden = false; OFF = new Set();
  chart($('c_total'), HIST.days, [{ name: 'Total', color: 'var(--accent)', values: HIST.total }],
    Math.max(4, Math.ceil(Math.max(0, ...HIST.total.filter(v => v != null)))));
  const lg = $('legend'); lg.replaceChildren();
  const items = HIST.allergens.map((a, i) => ({ key: a.id, name: a.name || a.id, color: COLORS[i % COLORS.length] }))
    .concat([{ key: '_risk', name: 'Allergy risk', color: 'var(--ink)' }]);
  for (const it of items) {
    const l = el('label'); const cb = el('input'); cb.type = 'checkbox'; cb.checked = true;
    cb.onchange = () => { if (cb.checked) OFF.delete(it.key); else OFF.add(it.key); drawEach(); };
    const sw = el('i'); sw.style.background = it.color;
    l.append(cb, sw, document.createTextNode(it.name)); lg.append(l);
  }
  drawEach();
  $('hnote').textContent = HIST.days_recorded + ' of the last ' + HIST.days.length + ' days recorded so far. History builds up as the app checks once a day, and is kept for 90 days.';
}
async function show() {
  msg.textContent = ''; $('list').replaceChildren();
  if (!$('place').value) { msg.textContent = 'Add a place on /places first.'; return; }
  $('note').textContent = 'Loading...';
  try {
    const d = await get('/v1/pollen/near?place=' + encodeURIComponent($('place').value));
    $('note').textContent = 'Pollen near ' + d.place + (d.cached ? ' (saved from earlier; refreshed daily)' : '') + '.';
    $('list').append(row('Allergy risk', d.dates, d.allergy_risk_level, d.allergy_risk_label,
      d.allergy_risk.map(r => r == null ? '' : r + '/10')));
    for (const a of d.allergens) $('list').append(row(a.name || a.id || 'Pollen', d.dates, a.levels, a.labels));
    if (!d.allergens.length) msg.textContent = 'No pollen data for this place right now.';
    loadHistory();
  } catch (e) { $('note').textContent = ''; msg.textContent = e.message; }
}
$('go').onclick = load; $('search').onclick = show;
if (tok.value) load();
</script></main></body></html>"""

page_router = APIRouter()


@page_router.get("/pollen", response_class=HTMLResponse, include_in_schema=False)
async def pollen_page() -> str:
    """The page holds no data; it asks for the token and calls /v1/pollen."""
    return themed(PAGE, "pollen")
