"""One map with every dataset on it, a layer at a time, over /v1/map/geojson.

The token is typed in the browser, as on the other pages. Leaflet is served from this app
(app/static/vendor/leaflet), so the page loads no third-party script; the map tiles are the one
thing the browser fetches from elsewhere, and WA_MAP_TILE_URL points that wherever you like.
The page never builds HTML from data: popups are assembled as DOM nodes.
"""

from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.pagestyle import themed

page_router = APIRouter()

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant map</title>
<link rel="stylesheet" href="/static/vendor/leaflet/leaflet.css">
<script src="/static/vendor/leaflet/leaflet.js"></script>
<style>
/*BASE_CSS*/
  main { max-width: 1200px; }
  #map { height: 70vh; min-height: 420px; border-radius: 14px; border: 1px solid var(--line); background: var(--chip); }
  @media (prefers-color-scheme: dark) { .leaflet-tile-pane { filter: invert(1) hue-rotate(180deg) brightness(.95) contrast(.9); } }
  .layers { display: flex; gap: 6px 16px; flex-wrap: wrap; margin-bottom: 12px; }
  .layers label { display: flex; align-items: center; gap: 6px; font-size: 14px; }
  .layers .swatch { width: 12px; height: 12px; border-radius: 3px; border: 1px solid rgba(0,0,0,.25); }
  .layers .count { color: var(--dim); font-variant-numeric: tabular-nums; }
  .pop b { display: block; margin-bottom: 2px; }
  .pop .dim { font-size: 12px; }
  .leaflet-popup-content { margin: 10px 12px; }
</style></head><body><main>
<!--NAV-->
<h1>Map</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<select id="place" aria-label="Centre"></select>
<select id="radius" aria-label="Distance">
  <option value="5">5 km</option><option value="10" selected>10 km</option>
  <option value="25">25 km</option><option value="50">50 km</option><option value="0">Everywhere</option>
</select>
<button id="go" class="primary" type="button">Load</button></div>
<p id="msg" class="err"></p>
<div class="layers" id="layers"></div>
<div id="map"></div>
<p class="dim" id="note"></p>

<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
const CFG = /*MAP_CONFIG*/;
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
async function api(method, path) {
  const r = await fetch(path, { method, headers: { Authorization: 'Bearer ' + tok.value } });
  if (!r.ok) { let d = r.status; try { d = (await r.json()).detail; } catch (e) {} throw new Error(typeof d === 'string' ? d : JSON.stringify(d)); }
  return r.json();
}
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };

// How each layer is drawn. Colour carries the meaning; the popup carries the detail.
const STYLE = {
  places:      { colour: '#1f6feb', label: 'Places' },
  locations:   { colour: '#9333ea', label: 'People' },
  roads:       { colour: '#0891b2', label: 'Corridors', weight: 4 },
  road_events: { colour: '#b42318', label: 'Closures', weight: 5 },
  signs:       { colour: '#b54708', label: 'Signs', small: true },
  fuel:        { colour: '#1a7f4b', label: 'Fuel', small: true },
  stops:       { colour: '#0d6e6e', label: 'Stops', small: true },
  floods:      { colour: '#1d4ed8', label: 'Floods' },
  pollen:      { colour: '#7c7c7c', label: 'Pollen', small: true }
};
const FLOOD = { 1: '#7a0c0c', 2: '#d92d20', 3: '#f79009' };  // severe, warning, alert
const POLLEN = ['#ffffff', '#8cc700', '#f5e400', '#f7a600', '#e63a10'];
const ON = new Set(['places', 'roads', 'road_events', 'fuel', 'floods']);

let map, drawn = {};
function ready() {
  if (map) return map;
  // canvas, not one SVG node per feature: 'Everywhere' draws thousands of stations and pollen points
  map = L.map('map', { scrollWheelZoom: true, preferCanvas: true }).setView([51.5074, -0.1278], 12);
  L.tileLayer(CFG.tile_url, { maxZoom: CFG.max_zoom, attribution: CFG.attribution }).addTo(map);
  return map;
}

function popup(key, p) {
  const box = el('div', null, 'pop');
  const title = p.name || p.road || p.label || p.brand || p.id || STYLE[key].label;
  box.appendChild(el('b', String(title)));
  const lines = [];
  if (key === 'places') lines.push([p.kind, p.postcode].filter(Boolean).join(' · '), p.origin ? 'added from Home Assistant' : '');
  if (key === 'locations') lines.push(p.radius_km ? 'within ' + p.radius_km + ' km' : '', p.last_seen ? 'seen ' + new Date(p.last_seen).toLocaleString('en-GB') : '');
  if (key === 'roads') lines.push((p.roads || []).join(', '), p.buffer_m ? p.buffer_m + ' m either side' : '');
  if (key === 'road_events') lines.push(p.kind === 'speed' ? (p.limit_mph ? p.limit_mph + ' mph limit' : 'speed limit') : 'closure',
    p.description || p.cause || '', p.start ? 'from ' + new Date(p.start).toLocaleString('en-GB') : '');
  if (key === 'signs') lines.push((p.lines || []).join(' / '), p.working ? '' : 'not working');
  if (key === 'fuel') lines.push(p.pence ? p.fuel + ' at ' + p.pence.toFixed(1) + 'p' : 'no ' + p.fuel + ' price',
    p.price_at ? 'set ' + new Date(p.price_at).toLocaleDateString('en-GB') : '', p.postcode || '');
  if (key === 'stops') lines.push([p.kind ? p.kind.replace('_', ' ') : '', p.indicator, p.street].filter(Boolean).join(' · '), p.locality || '', p.id);
  if (key === 'floods') lines.push(p.severity || '', [p.river_or_sea, p.county].filter(Boolean).join(' · '), p.message ? String(p.message).slice(0, 220) : '', 'Environment Agency flood data');
  if (key === 'pollen') lines.push(p.label ? 'allergy risk: ' + p.label : 'no reading');
  for (const line of lines) if (line) box.appendChild(el('div', line, 'dim'));
  return box;
}

function draw(key, collection, cheapest) {
  const s = STYLE[key], group = L.layerGroup();
  for (const f of collection.features) {
    const p = f.properties || {}, g = f.geometry || {};
    let shape;
    if (g.type === 'LineString') {
      shape = L.polyline(g.coordinates.map(c => [c[1], c[0]]), { color: s.colour, weight: s.weight || 3, opacity: .85 });
    } else if (g.type === 'Polygon' || g.type === 'MultiPolygon') {
      const c = FLOOD[p.level] || s.colour;
      shape = L.geoJSON(g, { style: { color: c, weight: 2, fillColor: c, fillOpacity: .25 } });
    } else if (g.type === 'Point') {
      let colour = s.colour, r = s.small ? 4 : 7;
      if (key === 'stops' && p.kind && p.kind !== 'bus_stop') { colour = '#0b3d91'; r = 6; }  // stations stand out from stops
      if (key === 'pollen') colour = POLLEN[p.level == null ? 0 : p.level];
      if (key === 'fuel' && p.pence && cheapest) colour = p.pence <= cheapest + 2 ? '#1a7f4b' : p.pence <= cheapest + 6 ? '#b54708' : '#b42318';
      shape = L.circleMarker([g.coordinates[1], g.coordinates[0]],
        { radius: r, color: '#00000055', weight: 1, fillColor: colour, fillOpacity: .9 });
    } else continue;
    shape.bindPopup(popup(key, p));
    group.addLayer(shape);
  }
  return group;
}

function controls(counts) {
  const box = $('layers'); box.replaceChildren();
  for (const key of Object.keys(STYLE)) {
    const wrap = el('label'), cb = el('input');
    cb.type = 'checkbox'; cb.checked = ON.has(key); cb.dataset.key = key;
    cb.addEventListener('change', () => { cb.checked ? ON.add(key) : ON.delete(key); show(key); });
    const sw = el('span', null, 'swatch'); sw.style.background = STYLE[key].colour;
    wrap.append(cb, sw, el('span', STYLE[key].label), el('span', '(' + (counts[key] || 0) + ')', 'count'));
    box.appendChild(wrap);
  }
}

function show(key) {
  const g = drawn[key];
  if (!g) return;
  if (ON.has(key)) g.addTo(map); else map.removeLayer(g);
}

async function load() {
  msg.textContent = '';
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  try {
    const radius = Number($('radius').value), place = $('place').value;
    const q = ['layers=' + Object.keys(STYLE).join(',')];
    if (!radius) q.push('everywhere=true'); else q.push('radius_km=' + radius);
    if (place && radius) q.push('place=' + encodeURIComponent(place));
    const body = await api('GET', '/v1/map/geojson?' + q.join('&'));
    ready();
    for (const g of Object.values(drawn)) map.removeLayer(g);
    drawn = {};
    const counts = {};
    const prices = (body.layers.fuel ? body.layers.fuel.features : []).map(f => f.properties.pence).filter(Boolean);
    const cheapest = prices.length ? Math.min.apply(null, prices) : null;
    for (const [key, collection] of Object.entries(body.layers)) {
      counts[key] = collection.features.length;
      drawn[key] = draw(key, collection, cheapest);
      show(key);
    }
    controls(counts);
    if (body.centre) {
      map.setView([body.centre.lat, body.centre.lon], radius <= 5 ? 13 : radius <= 10 ? 12 : radius <= 25 ? 11 : 10);
      $('note').textContent = 'Centred on ' + body.centre.name + ', within ' + body.centre.radius_km + ' km.';
    } else {
      $('note').textContent = 'Everything, wherever it is.';
      const b = L.latLngBounds([]);  // layer groups have no bounds of their own: ask each shape
      for (const [key, g] of Object.entries(drawn))
        if (ON.has(key)) g.eachLayer(l => b.extend(l.getBounds ? l.getBounds() : l.getLatLng()));
      if (b.isValid()) map.fitBounds(b.pad(.05));
    }
  } catch (e) { msg.textContent = e.message; }
}

async function places() {
  try {
    const list = await api('GET', '/v1/places'), keep = $('place').value;
    $('place').replaceChildren(...list.map(p => new Option(p.name, p.id)));
    if (keep && list.some(p => p.id === keep)) $('place').value = keep;
  } catch (e) { /* the token may be wrong; load() will say so */ }
}

$('go').addEventListener('click', async () => { await places(); await load(); });
$('radius').addEventListener('change', load);
$('place').addEventListener('change', load);
if (tok.value) { places().then(load); }
</script></main></body></html>"""


@page_router.get("/map", response_class=HTMLResponse, include_in_schema=False)
async def map_page() -> str:
    """The page holds no data; it asks for the token and calls /v1/map."""
    s = get_settings()
    cfg = json.dumps(
        {"tile_url": s.map_tile_url, "attribution": s.map_tile_attribution, "max_zoom": s.map_max_zoom},
        ensure_ascii=False,  # the page is UTF-8; "©" should read as itself in the source
    )
    return themed(PAGE.replace("/*MAP_CONFIG*/", cfg), "map")
