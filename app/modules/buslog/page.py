"""The bus route log page (/buslog): what the recorder is doing, its switches, and a map of the route's stops with
each bus as a red box holding its numberplate, at any moment of a recorded day. Every request's outcome is drawn as
a strip, so a gap in the record is never mistaken for a bus that did not come. The page holds no data and never
builds HTML from it."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.pagestyle import themed

page_router = APIRouter()

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant bus log</title>
<link rel="stylesheet" href="/static/vendor/leaflet/leaflet.css">
<script src="/static/vendor/leaflet/leaflet.js"></script>
<style>
/*BASE_CSS*/
#map { height: 62vh; min-height: 380px; border-radius: 14px; border: 1px solid var(--line); background: var(--chip); }
:root[data-dark] .leaflet-tile-pane { filter: invert(1) hue-rotate(180deg) brightness(.95) contrast(.9); }
@media (prefers-color-scheme: dark) { :root:not([data-mode]) .leaflet-tile-pane { filter: invert(1) hue-rotate(180deg) brightness(.95) contrast(.9); } }
.busicon { background: var(--bus); border-radius: 5px; padding: 3px; box-shadow: 0 1px 3px var(--shadow); display: inline-block; }
.busicon span { display: block; background: var(--plate); color: var(--plate-ink); font: 700 11px/1.1 ui-monospace, Menlo, Consolas, monospace;
  padding: 2px 4px; border-radius: 2px; white-space: nowrap; letter-spacing: .03em; }
.busicon.old { opacity: .55; }
.strip { display: flex; gap: 1px; height: 14px; margin: 4px 0 2px; overflow: hidden; }
.strip i { flex: 1 1 0; min-width: 0; background: var(--chip); }
.strip i.ok { background: var(--good); } .strip i.empty { background: var(--warn); } .strip i.failed { background: var(--bad); }
.legend span { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin: 0 4px 0 10px; vertical-align: middle; }
.row { display: flex; flex-wrap: wrap; gap: 10px; align-items: end; }
#t2 { width: 100%; }
</style></head><body><main>
<!--NAV-->
<h1>Bus log</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary" type="button">Load</button></div>
<p id="msg" class="err"></p>

<div class="card hero"><div class="say" id="say">Loading…</div><div><span id="state" class="pill"></span><div class="dim" id="why"></div></div></div>

<div class="card">
  <div class="row">
    <label>Day <input id="day" type="date"></label>
    <label style="flex:1 1 260px">Time <span id="tlabel" class="dim"></span><input id="t2" type="range" min="0" max="7200" step="10"></label>
    <label style="flex-direction:row;align-items:center;gap:6px"><input id="follow" type="checkbox" checked style="width:auto"> Live</label>
  </div>
  <div id="map" role="img" aria-label="Map of the route, its stops and its buses"></div>
  <p class="dim" id="mapnote"></p>
</div>

<div class="card"><h2>Every request that day</h2>
  <p class="dim legend">One mark a minute.<span style="background:var(--good)"></span>answered with data<span style="background:var(--warn)"></span>answered empty<span style="background:var(--bad)"></span>failed<span style="background:var(--chip)"></span>not asked</p>
  <div id="strips"></div><p id="errs" class="dim"></p></div>

<div class="card"><h2>Recording</h2>
  <div class="form">
    <div class="row">
      <label>Route <input id="line" maxlength="8" style="width:6em"></label>
      <label>From <input id="start" type="time"></label>
      <label>To <input id="end" type="time"></label>
      <label style="flex-direction:row;align-items:center;gap:6px"><input id="every" type="checkbox" style="width:auto">
        Every day, weekends and holidays too <span class="hint">(a test)</span></label>
      <button id="save" type="button">Save</button>
    </div>
    <div class="row">
      <label>Record now for <select id="mins"><option>15</option><option selected>30</option><option>60</option><option>120</option></select> minutes</label>
      <button id="now" type="button">Record now</button><button id="stop" type="button">Stop</button>
    </div>
    <p id="setmsg" class="dim"></p>
    <p class="dim" id="next"></p>
  </div>
</div>

<script>
const CFG = /*MAP_CONFIG*/;
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
async function api(method, path, body) {
  const r = await fetch(path, { method, headers: { Authorization: 'Bearer ' + tok.value, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) { let d = r.status; try { d = (await r.json()).detail; } catch (e) {} throw new Error(typeof d === 'string' ? d : JSON.stringify(d)); }
  return r.status === 204 ? null : r.json();
}
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const hms = d => d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
const tok_ = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const today = () => new Date().toLocaleDateString('en-CA');

const map = L.map('map', { zoomControl: true }).setView([51.385, -0.26], 12);
L.tileLayer(CFG.tile_url, { maxZoom: CFG.max_zoom, attribution: CFG.attribution }).addTo(map);
const routeLayer = L.layerGroup().addTo(map), busLayer = L.layerGroup().addTo(map);
let st = null, fitted = false, routeDay = null;

function dayStart() {  // the window's start on the chosen day, local time
  const [h, m] = (st ? st.settings.start : '07:00').split(':').map(Number);
  const d = new Date($('day').value + 'T00:00:00'); d.setHours(h, m, 0, 0); return d;
}
function moment() { return new Date(dayStart().getTime() + Number($('t2').value) * 1000); }

async function drawRoute() {
  if (routeDay === $('day').value) return;
  routeDay = $('day').value;
  const r = await api('GET', '/v1/buslog/route?day=' + routeDay);
  routeLayer.clearLayers();
  const pts = [];
  r.directions.forEach((d, i) => {
    if (d.shape) L.geoJSON(d.shape, { style: { color: tok_(i ? '--series-3' : '--series-1'), weight: 4, opacity: .7 } }).addTo(routeLayer);
    d.stops.forEach(s => {
      if (s.lat == null) return;
      pts.push([s.lat, s.lon]);
      const c = L.circleMarker([s.lat, s.lon], { radius: 4, weight: 1, color: tok_('--ink'), fillColor: tok_('--card'), fillOpacity: 1 });
      c.bindTooltip(document.createTextNode((s.name || s.stop) + (s.letter ? ' (' + s.letter + ')' : '') + ' · ' + d.direction));
      c.addTo(routeLayer);
    });
  });
  if (pts.length && !fitted) { map.fitBounds(pts, { padding: [20, 20] }); fitted = true; }
  $('mapnote').textContent = r.directions.length ? 'Route as read on ' + r.directions.map(d => d.day).filter((v, i, a) => a.indexOf(v) === i).join(', ') + ' (TfL’s stop order, NaPTAN’s positions).'
    : 'No route recorded yet: it is read at the start of the first recording minute.';
}

async function drawBuses() {
  const at = $('follow').checked && $('day').value === today() ? new Date() : moment();
  $('tlabel').textContent = hms(at);
  const p = await api('GET', '/v1/buslog/positions?day=' + $('day').value + '&at=' + encodeURIComponent(at.toISOString()));
  busLayer.clearLayers();
  p.buses.forEach(b => {
    if (b.lat == null) return;
    const box = el('div', null, 'busicon' + (b.age_s > 90 ? ' old' : '')), plate = el('span', b.vehicle); box.appendChild(plate);
    const m = L.marker([b.lat, b.lon], { icon: L.divIcon({ html: box, className: '', iconSize: null, iconAnchor: [30, 12] }) });
    m.bindTooltip(document.createTextNode(b.vehicle + ' to ' + (b.destination || '?') + ' · position ' + (b.age_s != null ? b.age_s + ' s old' : 'of unknown age') +
      (b.off_route_m != null ? ' · ' + b.off_route_m + ' m from the route' : '') + (b.occupancy ? ' · ' + b.occupancy : '')));
    m.addTo(busLayer);
  });
  const last = p.last_ok_poll ? new Date(p.last_ok_poll) : null;
  $('mapnote').dataset.buses = p.buses.length;
  if (!p.last_poll) $('mapnote').textContent = 'No BODS request recorded by ' + hms(at) + ' on this day: nothing is known, which is not the same as no buses.';
  else if (!last || at - last > 60000) $('mapnote').textContent = 'BODS last answered at ' + (last ? hms(last) : 'no time this day') + ': the buses shown may be out of date.';
  else $('mapnote').textContent = p.buses.length + ' bus' + (p.buses.length === 1 ? '' : 'es') + ' in BODS’s answers in the 3 minutes to ' + hms(at) + '.';
}

async function drawStrips() {
  const p = await api('GET', '/v1/buslog/polls?day=' + $('day').value);
  const box = $('strips'); box.replaceChildren();
  const start = dayStart(), [eh, em] = (st ? st.settings.end : '09:00').split(':').map(Number);
  const end = new Date(start); end.setHours(eh, em);
  const first = p.minutes.length ? new Date(Math.min(start, ...p.minutes.map(m => new Date(m.minute)))) : start;
  const last = p.minutes.length ? new Date(Math.max(end - 60000, ...p.minutes.map(m => new Date(m.minute)))) : end;
  const errs = new Set();
  ['bods', 'tfl-arrivals', 'tfl-status', 'tfl-route', 'tfl-timetable'].forEach(src => {
    const rows = new Map(p.minutes.filter(m => m.source === src).map(m => [new Date(m.minute).getTime(), m]));
    if (!rows.size && src.startsWith('tfl-r') || !rows.size && src === 'tfl-timetable') return;
    box.appendChild(el('div', src, 'dim'));
    const s = el('div', null, 'strip');
    for (let t = first.getTime(); t <= last.getTime(); t += 60000) {
      const m = rows.get(t), i = el('i');
      if (m) { i.className = m.failed ? 'failed' : m.ok ? 'ok' : 'empty'; i.title = hms(new Date(t)) + ': ' + m.ok + ' with data, ' + m.empty + ' empty, ' + m.failed + ' failed' + (m.errors ? ' (' + m.errors + ')' : ''); if (m.errors) errs.add(m.errors); }
      else i.title = hms(new Date(t)) + ': not asked';
      s.appendChild(i);
    }
    box.appendChild(s);
  });
  if (!p.minutes.length) box.appendChild(el('p', 'Nothing was asked on this day.', 'dim'));
  $('errs').textContent = errs.size ? 'Failures: ' + [...errs].slice(0, 5).join('; ') : '';
}

async function load() {
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  msg.textContent = '';
  try {
    st = await api('GET', '/v1/buslog/status');
    const s = st.settings;
    if (document.activeElement.tagName !== 'INPUT') { $('line').value = s.line; $('start').value = s.start; $('end').value = s.end; $('every').checked = s.every_day; }
    $('state').textContent = st.recording ? 'Recording' : 'Not recording'; $('state').className = 'pill ' + (st.recording ? 'good' : '');
    $('why').textContent = st.why + (st.record_until ? ' · until ' + hms(new Date(st.record_until)) : '');
    const t = st.today.sources.reduce((a, x) => [a[0] + x.ok, a[1] + x.empty, a[2] + x.failed], [0, 0, 0]);
    $('say').textContent = 'The ' + s.line + ', ' + s.start + ' to ' + s.end + (s.every_day ? ' every day (test).' : ' on school days.') +
      ' Today: ' + t[0] + ' requests with data, ' + t[1] + ' empty, ' + t[2] + ' failed.' + (!st.keys.bods ? ' No BODS key: positions cannot be recorded.' : '');
    $('next').textContent = 'Next: ' + st.next_days.map(d => d.day + ' (' + d.why + ')').join(', ') +
      (st.last_error ? '. Last failure ' + new Date(st.last_error.at).toLocaleString('en-GB') + ' (' + st.last_error.source + ': ' + st.last_error.error + ')' : '');
    const [sh, sm] = s.start.split(':').map(Number), [eh, em] = s.end.split(':').map(Number);
    $('t2').max = Math.max(60, (eh * 60 + em - sh * 60 - sm) * 60);
    await drawRoute(); await drawBuses(); await drawStrips();
  } catch (e) { msg.textContent = e.message; }
}
$('day').value = today();
$('t2').oninput = () => { $('follow').checked = false; drawBuses().catch(e => msg.textContent = e.message); };
$('day').onchange = () => { $('follow').checked = $('day').value === today(); load(); };
$('save').onclick = async () => {
  try { await api('PUT', '/v1/buslog/settings', { line: $('line').value.trim(), start: $('start').value, end: $('end').value, every_day: $('every').checked });
    $('setmsg').textContent = 'Saved.'; routeDay = null; load(); } catch (e) { $('setmsg').textContent = e.message; }
};
$('now').onclick = async () => { try { const r = await api('POST', '/v1/buslog/record-now', { minutes: Number($('mins').value) }); $('setmsg').textContent = 'Recording from the next minute until ' + hms(new Date(r.record_until)) + '.'; load(); } catch (e) { $('setmsg').textContent = e.message; } };
$('stop').onclick = async () => { try { await api('DELETE', '/v1/buslog/record-now'); $('setmsg').textContent = 'Stopped (the window and test switch still apply).'; load(); } catch (e) { $('setmsg').textContent = e.message; } };
$('go').onclick = load;
load();
setInterval(() => { if (!document.hidden && $('follow').checked) drawBuses().catch(() => {}); }, 10000);
setInterval(() => { if (!document.hidden) load(); }, 60000);
</script></main></body></html>"""


@page_router.get("/buslog", response_class=HTMLResponse, include_in_schema=False)
async def buslog_page(request: Request) -> str:
    s = get_settings()
    cfg = json.dumps({"tile_url": s.map_tile_url, "attribution": s.map_tile_attribution, "max_zoom": s.map_max_zoom}, ensure_ascii=False)
    return themed(PAGE.replace("/*MAP_CONFIG*/", cfg), "buslog", request)
