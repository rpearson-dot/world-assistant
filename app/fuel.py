"""Fuel price API: cheapest near a place, a point or along a corridor."""

from __future__ import annotations

import statistics
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.auth import require_token
from app.integrations.fuelfinder.normalise import canonical_fuel
from app.integrations.fuelfinder.queries import (
    FuelHit,
    all_stations,
    along_corridor,
    brand_options,
    compare_cheapest,
    near_point,
    select_brands,
)
from app.integrations.nationalhighways.store import load_corridors
from app.pagestyle import themed
from app.places import place_or_default
from app.vehicles import get_vehicle, net_saving_pence

router = APIRouter(prefix="/v1/fuel", tags=["fuel"], dependencies=[Depends(require_token)])

FUEL = Query("E10", description="E10 (standard petrol), E5, B7_STANDARD, B7_PREMIUM, B10, HVO")


BRANDS = Annotated[
    str | None, Query(description="comma-separated brands to compare, e.g. Tesco,Asda")
]
SUPERMARKETS = Annotated[bool, Query(description="also include every supermarket")]
FILL = Annotated[float, Query(gt=0, le=200, description="litres, for the cost difference")]


def _cheapest(hits: list[FuelHit]) -> dict[str, Any] | None:
    best = min(hits, key=lambda h: (h.pence, h.distance_km), default=None)
    return best.as_dict() if best else None


def _out(
    fuel: str, hits: list[FuelHit], *, brands: str | None = None, supermarkets: bool = False,
    fill_litres: float = 40.0, limit: int = 10, **extra: Any,
) -> dict[str, Any]:  # fmt: skip
    """Rank the hits, optionally narrowed to chosen brands, with the comparison and brand list."""
    names = [b for b in (brands or "").split(",") if b.strip()]
    chosen = select_brands(hits, names, supermarkets)
    filtered = bool(names) or supermarkets
    shown = chosen[:limit]
    out: dict[str, Any] = {
        "fuel": fuel, **extra, "count": len(shown), "stations": [h.as_dict() for h in shown],
        "brands_available": brand_options(hits),
        # Always the cheapest in the area whatever brands are chosen, so a filter can never hide it.
        "cheapest_overall": _cheapest(hits),
    }  # fmt: skip
    if filtered:
        out["filter"] = {"brands": names, "supermarkets": supermarkets}
        out["comparison"] = compare_cheapest(hits, chosen, fill_litres)
    return out


@router.get("/near")
async def near(
    request: Request,
    place: str | None = Query(None, description="id of a saved place; default: home"),
    lat: float | None = Query(None, ge=49.5, le=61.0),
    lon: float | None = Query(None, ge=-8.7, le=2.0),
    radius_km: float = Query(5.0, gt=0, le=50),
    fuel: str = FUEL,
    limit: int = Query(10, ge=1, le=50),
    max_age_hours: float | None = Query(48.0, ge=0, description="ignore prices older than this; 0 = any age"),
    brands: BRANDS = None,
    supermarkets: SUPERMARKETS = False,
    fill_litres: FILL = 40.0,
) -> dict[str, Any]:
    engine = request.app.state.engine
    if place is None and lat is None and lon is None:
        place = await place_or_default(request, None)
    if place is not None:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT name, lat, lon FROM place WHERE id = :p"), {"p": place}
                )
            ).first()
        if row is None:
            raise HTTPException(404, f"no place {place!r}")
        name, lat, lon = row
    elif lat is None or lon is None:
        raise HTTPException(422, "give place, or both lat and lon, or neither to use home")
    else:
        name = None
    try:
        hits = await near_point(
            engine, lat, lon, radius_km=radius_km, fuel=fuel, limit=10_000, max_age_hours=max_age_hours or None
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _out(
        fuel, hits, brands=brands, supermarkets=supermarkets, fill_litres=fill_litres, limit=limit,
        place=name, lat=lat, lon=lon, radius_km=radius_km,
    )


@router.get("/all")
async def everywhere(
    request: Request,
    fuel: str = FUEL,
    max_age_hours: float | None = Query(48.0, ge=0, description="ignore prices older than this; 0 = any age"),
) -> dict[str, Any]:
    """Every station with a current price, nationwide and unranked (for Home Assistant to filter)."""
    code = canonical_fuel(fuel)
    if code is None:
        raise HTTPException(422, f"unknown fuel {fuel!r}")
    stations = await all_stations(request.app.state.engine, code, max_age_hours or None)
    return {"fuel": code, "count": len(stations), "stations": stations}


@router.get("/along/{corridor_id}")
async def along(
    corridor_id: str,
    request: Request,
    within_km: float = Query(2.0, gt=0, le=20),
    fuel: str = FUEL,
    limit: int = Query(10, ge=1, le=50),
    max_age_hours: float | None = Query(48.0, ge=0, description="ignore prices older than this; 0 = any age"),
    vehicle: str | None = Query(None, description="id of a vehicle: rank by net saving after the detour"),
    fill_litres: float = Query(30.0, gt=0, le=200, description="how much you would buy"),
    brands: BRANDS = None,
    supermarkets: SUPERMARKETS = False,
) -> dict[str, Any]:
    engine = request.app.state.engine
    veh = await get_vehicle(request, vehicle) if vehicle else None
    if veh:
        if veh["kind"] == "ev":
            raise HTTPException(422, "electric vehicles don't use fuel prices; see /v1/trips/{id}/cost")
        fuel = veh["fuel"]
    corridor = next(
        (c for c in await load_corridors(engine, only_enabled=False) if c.id == corridor_id), None
    )
    if corridor is None:
        raise HTTPException(404, f"no corridor {corridor_id!r}")
    try:
        hits = await along_corridor(
            engine, corridor, within_km=within_km, fuel=fuel,
            limit=10_000, max_age_hours=max_age_hours or None,
        )  # fmt: skip
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not veh:
        return _out(
            fuel, hits, brands=brands, supermarkets=supermarkets, fill_litres=fill_litres,
            limit=limit, corridor=corridor.name, within_km=within_km,
        )  # fmt: skip
    reference = statistics.median(h.pence for h in hits) if hits else 0.0
    names = [b for b in (brands or "").split(",") if b.strip()]
    chosen = select_brands(hits, names, supermarkets)
    rows = [{**h.as_dict(), **net_saving_pence(h, reference, veh["mpg"], fill_litres)} for h in chosen]
    rows.sort(key=lambda r: -r["net_saving_p"])
    return {"fuel": fuel, "corridor": corridor.name, "within_km": within_km,
            "vehicle": veh["name"], "fill_litres": fill_litres,
            "reference_pence_per_litre": round(reference, 1), "count": len(rows[:limit]),
            "stations": rows[:limit], "brands_available": brand_options(hits),
            "cheapest_overall": _cheapest(hits)}  # fmt: skip


@router.get("/summary")
async def summary(request: Request) -> dict[str, Any]:
    """What is stored, and how fresh it is."""
    async with request.app.state.engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT (SELECT count(*) FROM fuel_station) AS stations,
                           (SELECT count(*) FROM fuel_price) AS prices,
                           (SELECT max(observed_at) FROM fuel_price) AS prices_observed_at
                    """
                )
            )
        ).one()
        by_fuel = (
            await conn.execute(
                text(
                    "SELECT fuel_type, count(*) AS n, min(pence) AS lowest, "
                    "round(avg(pence)::numeric, 1) AS average FROM fuel_price "
                    "GROUP BY 1 ORDER BY 2 DESC"
                )
            )
        ).mappings().all()
    return {**dict(row._mapping), "by_fuel": [dict(r) for r in by_fuel]}


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant fuel</title>
<style>
/*BASE_CSS*/</style></head><body><main>
<!--NAV-->
<h1>Cheapest fuel</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary" type="button">Load</button></div>
<div class="card"><h2>Search</h2><div class="row">
  <label>Search<select id="mode"><option value="place">Near a place</option>
    <option value="corridor">Along a corridor</option></select></label>
  <label>Where<select id="where"></select></label>
  <label>Fuel<select id="fuel"><option>E10</option><option>E5</option>
    <option>B7_STANDARD</option><option>B7_PREMIUM</option><option>B10</option><option>HVO</option></select></label>
  <label id="distl">Radius km<input id="dist" type="number" value="5" min="0.5" max="50" step="0.5"></label>
  <label>Show<input id="limit" type="number" value="10" min="1" max="50"></label>
  <label>Prices no older than<select id="age"><option value="24">1 day</option><option value="48" selected>2 days</option>
    <option value="168">1 week</option><option value="0">any age</option></select></label>
  <button id="search" class="primary" type="button">Search</button>
</div><p id="note" class="dim"></p></div>
<div class="card checks" id="brandbox" hidden><h2>Compare brands <button id="clear" class="link push" type="button">Clear</button></h2>
  <p class="dim">Tick the brands you would actually use, such as a supermarket on your way.</p>
  <div id="brands"></div></div>
<div id="best" class="card best" hidden></div>
<div id="cmp" class="card cmp" hidden></div>
<p id="msg" class="err"></p>
<div class="card scroll"><h2 id="tcap" hidden></h2><table id="tbl" hidden><thead><tr>
  <th>Pence</th><th>Station</th><th>Town</th><th class="n">Km</th><th class="n">Price age</th><th></th>
</tr></thead><tbody></tbody></table></div>
<script>const $ = id => document.getElementById(id);
const tok = $('t'), msg = $('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
let places = [], corridors = [];
const homeId = ps => (ps.find(p => p.kind === 'home') || ps.find(p => p.id === 'home') || ps[0] || {}).id;
let chosen = new Set(), superOn = false;
try { chosen = new Set(JSON.parse(localStorage.getItem('wa_fuel_brands') || '[]')); superOn = localStorage.getItem('wa_fuel_super') === '1'; } catch (e) {}
function saveChoice() { try { localStorage.setItem('wa_fuel_brands', JSON.stringify([...chosen])); localStorage.setItem('wa_fuel_super', superOn ? '1' : '0'); } catch (e) {} }
function box(label, checked, onchange) {
  const l = document.createElement('label'), c = document.createElement('input');
  c.type = 'checkbox'; c.checked = checked; c.onchange = () => { onchange(c.checked); saveChoice(); search(); };
  l.append(c, document.createTextNode(label)); return l;
}
function drawBrands(list) {
  const holder = $('brands'); holder.replaceChildren();
  holder.append(box('All supermarkets', superOn, v => { superOn = v; }));
  for (const b of list) {
    const key = b.brand.toLowerCase();
    holder.append(box(b.brand + ' (' + b.stations + ', from ' + b.cheapest_pence.toFixed(1) + 'p)', chosen.has(key),
      v => { if (v) chosen.add(key); else chosen.delete(key); }));
  }
  $('brandbox').hidden = false;
}
function line(parent, text, strong) {
  const p = document.createElement('p'); if (strong) p.className = 'big';
  p.textContent = text; parent.append(p);
}
function name(s) { return [s.brand, s.name].filter(Boolean).join(' - ') + (s.city ? ', ' + s.city : ''); }
function drawBest(a, filtered) {
  const box = $('best'); box.replaceChildren();
  if (!a) { box.hidden = true; return; }
  const h = document.createElement('h2'); h.textContent = 'Cheapest nearby, any brand'; box.append(h);
  line(box, a.pence.toFixed(1) + 'p at ' + name(a) + ', ' + a.distance_km.toFixed(1) + ' km', true);
  const n = document.createElement('p'); n.className = 'dim';
  n.textContent = filtered ? 'Always shown, whatever brands you tick. Your ticked brands are compared below.'
    : 'Always shown at the top, whatever brands you tick.';
  box.append(n); box.hidden = false;
}
function drawCompare(c) {
  const box = $('cmp'); box.replaceChildren();
  if (!c) { box.hidden = true; return; }
  const b = c.cheapest_selected;
  if (c.same_station) { line(box, 'That is also your cheapest selected brand.', true); }
  else {
    line(box, 'Cheapest selected: ' + b.pence.toFixed(1) + 'p at ' + name(b) + ', ' + b.distance_km.toFixed(1) + ' km');
    line(box, 'Choosing it costs ' + c.extra_pence_per_litre.toFixed(1) + 'p a litre more, about \u00a3'
      + c.extra_cost_pounds.toFixed(2) + ' for ' + c.fill_litres + ' litres'
      + (c.distance_saved_km > 0 ? ', and it is ' + c.distance_saved_km.toFixed(1) + ' km closer.' : '.'), true);
  }
  box.hidden = false;
}
const auth = () => ({ headers: { Authorization: 'Bearer ' + tok.value } });
async function get(url) {
  const r = await fetch(url, auth());
  if (!r.ok) throw new Error(url.split('?')[0] + ' returned ' + r.status);
  return r.json();
}
function fillWhere() {
  const list = $('mode').value === 'place' ? places : corridors;
  $('where').replaceChildren(...list.map(x => new Option(x.name, x.id)));
  if ($('mode').value === 'place') $('where').value = homeId(places) || '';
  $('distl').firstChild.textContent = $('mode').value === 'place' ? 'Radius km' : 'Within km';
  $('dist').value = $('mode').value === 'place' ? 5 : 2;
}
async function load() {
  msg.textContent = '';
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  try {
    places = await get('/v1/places');
    corridors = (await get('/v1/status')).corridors;
    fillWhere();
    const s = await get('/v1/fuel/summary');
    $('note').textContent = s.stations + ' stations, ' + s.prices + ' prices, last update ' + (s.prices_observed_at || 'never');
  } catch (e) { msg.textContent = e.message; }
}
function cell(tr, text, cls) { const td = tr.insertCell(); td.textContent = text; if (cls) td.className = cls; }
async function search() {
  msg.textContent = '';
  if (!$('where').value) { msg.textContent = 'Nothing to search: add a place on /places first.'; return; }
  const p = new URLSearchParams({ fuel: $('fuel').value, limit: $('limit').value, max_age_hours: $('age').value });
  if (chosen.size) p.set('brands', [...chosen].join(','));
  if (superOn) p.set('supermarkets', 'true');
  let url;
  if ($('mode').value === 'place') { p.set('place', $('where').value); p.set('radius_km', $('dist').value); url = '/v1/fuel/near?'; }
  else { p.set('within_km', $('dist').value); url = '/v1/fuel/along/' + encodeURIComponent($('where').value) + '?'; }
  try {
    const d = await get(url + p);
    drawBrands(d.brands_available || []); drawBest(d.cheapest_overall, !!d.filter); drawCompare(d.comparison);
    const cap = $('tcap'); cap.hidden = false;
    cap.textContent = d.filter ? 'Your selection: ' + [...(d.filter.supermarkets ? ['all supermarkets'] : []), ...d.filter.brands].join(', ')
      : 'All brands, cheapest first';
    const body = $('tbl').tBodies[0]; body.replaceChildren();
    for (const s of d.stations) {
      const tr = body.insertRow();
      cell(tr, s.pence.toFixed(1)); cell(tr, [s.brand, s.name].filter(Boolean).join(' - '));
      cell(tr, s.city || s.postcode || ''); cell(tr, s.distance_km.toFixed(1), 'n');
      cell(tr, s.price_age_hours == null ? '?' : s.price_age_hours.toFixed(0) + ' h', 'n');
      cell(tr, s.motorway_services ? 'motorway services' : '');
    }
    $('tbl').hidden = false;
    if (!d.count) msg.textContent = 'No stations found. Try a wider distance.';
  } catch (e) { msg.textContent = e.message; }
}
$('clear').onclick = () => { chosen.clear(); superOn = false; saveChoice(); search(); };
$('go').onclick = load; $('search').onclick = search; $('mode').onchange = fillWhere;
if (tok.value) load();
</script></main></body></html>"""

page_router = APIRouter()


@page_router.get("/fuel", response_class=HTMLResponse, include_in_schema=False)
async def fuel_page(request: Request) -> str:
    """The page holds no data; it asks for the token and calls /v1/fuel."""
    return themed(PAGE, "fuel", request)
