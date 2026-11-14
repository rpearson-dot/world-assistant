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
.busicon { cursor: pointer; text-align: center; }
.busicon span { display: block; background: var(--plate); color: var(--plate-ink); font: 700 11px/1.1 ui-monospace, Menlo, Consolas, monospace;
  padding: 2px 4px; border-radius: 2px; white-space: nowrap; letter-spacing: .03em; }
.busicon small { display: block; color: var(--bus-ink); font: 600 10px/1.2 system-ui, sans-serif; padding: 2px 2px 0; white-space: nowrap; }
.busicon.picked { outline: 3px solid var(--accent); outline-offset: 1px; }
#detail table { width: 100%; border-collapse: collapse; font-size: .92em; }
#detail td, #detail th { padding: 5px 6px; border-bottom: 1px solid var(--line); text-align: left; font-variant-numeric: tabular-nums; }
.v-late { color: var(--bad); font-weight: 600; } .v-early { color: var(--warn); font-weight: 600; } .v-on { color: var(--good); font-weight: 600; }
#cal table { width: 100%; border-collapse: collapse; } #cal td, #cal th { padding: 4px; border-bottom: 1px solid var(--line); text-align: left; }
#cal input, #cal select { width: 100%; min-width: 0; } #cal td.x { width: 1%; white-space: nowrap; }
.busicon.old { opacity: .55; }
.strip { display: flex; gap: 1px; height: 14px; margin: 4px 0 2px; overflow: hidden; }
.strip i { flex: 1 1 0; min-width: 0; background: var(--chip); }
.strip i.ok { background: var(--good); } .strip i.empty { background: var(--warn); } .strip i.failed { background: var(--bad); }
.legend span { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin: 0 4px 0 10px; vertical-align: middle; }
.row { display: flex; flex-wrap: wrap; gap: 10px; align-items: end; }
#t2 { width: 100%; }
.marks { position: relative; height: 18px; margin: 2px 0 0; }
.marks button { position: absolute; top: 2px; width: 10px; height: 14px; min-height: 0; padding: 0; border-radius: 3px; border: 1px solid var(--card);
  transform: translateX(-5px); background: var(--dim); cursor: pointer; }
.marks button.turned, .marks button.off_route { background: var(--bad); } .marks button.school { background: var(--good); }
.marks button.delay, .marks button.status { background: var(--warn); } .marks button.rejoined { background: var(--accent); }
.marks button.left { background: var(--line); height: 8px; margin-top: 3px; width: 6px; transform: translateX(-3px); }
.play { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.wrap { overflow-x: auto; }
table.sr { width: 100%; border-collapse: collapse; font-size: .92em; }
table.sr td, table.sr th { padding: 6px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; font-variant-numeric: tabular-nums; }
table.sr th { font-weight: 600; } table.sr tr.bus { cursor: pointer; } table.sr tr.bus:hover td { background: var(--chip); }
table.sr td.n { white-space: nowrap; } .sub { color: var(--dim); font-size: .85em; display: block; }
.prob { font-style: italic; } .bar { display: inline-block; height: 8px; border-radius: 4px; background: var(--good); vertical-align: middle; margin-left: 6px; }
tr.more td { background: var(--chip); }
.kpis { display: flex; gap: 18px; flex-wrap: wrap; margin: 6px 0 10px; } .kpis div { min-width: 7em; } .kpis b { display: block; font-size: 1.6em; }
</style></head><body><main>
<!--NAV-->
<h1>Bus log</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary" type="button">Load</button></div>
<p id="msg" class="err"></p>

<div class="card hero"><div class="say" id="say">Loading…</div><div><span id="state" class="pill"></span><div class="dim" id="why"></div></div></div>

<div class="card" id="sr"><h2>School run: <span id="srtitle">Worcester Park to Coombe Girls School</span></h2>
  <div class="kpis" id="srkpi"></div>
  <p id="srsay"></p>
  <div class="wrap"><table class="sr" id="srtable"></table></div>
  <p class="dim small">Times in <i>italics with ≈</i> are probable, from the bus's GPS (about one position a minute); others are
  when TfL's prediction for the stop ended as the bus got there. <b>Expected</b> is TfL's last prediction before then
  (its first, at the start, is underneath). Tap a bus for every stop, and to watch it on the map.</p>
  <div class="row"><button id="srfresh" type="button">Work it out again</button><button id="srdl" type="button">Download this day</button>
    <span id="srmsg" class="dim"></span></div>
</div>

<div class="card">
  <div class="row">
    <label>Day <input id="day" type="date"></label>
    <label style="flex:1 1 260px">Time <span id="tlabel" class="dim"></span><input id="t2" type="range" min="0" max="7200" step="10"></label>
    <label class="tick"><input id="follow" type="checkbox" checked> Live</label>
  </div>
  <div class="marks" id="marks" aria-label="Moments to watch: tap one to jump to it"></div>
  <div class="play"><button id="play" type="button">▶ Play</button>
    <label>Speed <select id="speed"><option value="1">1×</option><option value="10" selected>10×</option><option value="30">30×</option><option value="60">60×</option></select></label>
    <span class="dim small" id="marknote">Marks: <span style="color:var(--good)">■</span> at the school <span style="color:var(--bad)">■</span> turned or left the route
      <span style="color:var(--warn)">■</span> delay shown <span style="color:var(--accent)">■</span> back on the route</span></div>
  <div class="row">
    <button id="sched" type="button">Download schedule now</button>
  </div>
  <p class="dim" id="schedmsg"></p>
  <div id="map" role="img" aria-label="Map of the route, its stops and its buses"></div>
  <p class="dim" id="mapnote"></p>
  <p class="dim">Tap a stop for its next hour, or a bus for its times to the end of its route.</p>
</div>

<div class="card" id="detail" hidden><h2 id="dtitle"></h2><p class="dim" id="dsub"></p><div id="dbody"></div></div>

<div class="card"><h2>Reliability over time</h2>
  <p class="dim">Each recorded school morning: the buses seen leaving Worcester Park towards Kingston, how many reached
  Coombe Girls School (from either side of the road), and how many by the time set below. Timetabled journeys with
  no bus seen are counted apart, as are minutes with no positions received.</p>
  <div class="kpis" id="relkpi"></div>
  <div class="wrap"><table class="sr" id="reltable"></table></div>
  <details><summary>Stops and time</summary><div class="row" style="margin-top:8px">
    <label>From <select id="srfirst"></select></label><label>School <select id="srschool"></select></label>
    <label>School, other side <select id="srother"></select></label><label>In time by <input id="srby" type="time"></label>
    <button id="srsave" type="button">Save</button></div><p id="srsetmsg" class="dim"></p></details>
</div>

<div class="card"><h2>Every request that day</h2>
  <p class="dim legend">One mark a minute.<span style="background:var(--good)"></span>answered with data<span style="background:var(--warn)"></span>answered empty<span style="background:var(--bad)"></span>failed<span style="background:var(--chip)"></span>not asked</p>
  <div id="strips"></div><p id="errs" class="dim"></p></div>

<div class="card" id="cal"><h2>School days</h2>
  <p class="dim">Recording follows these on weekdays (and never on bank holidays). Terms are school days; holidays and INSET days inside them are not.</p>
  <label>School <input id="school" maxlength="80"></label>
  <table><thead><tr><th>What</th><th>Name</th><th>From</th><th>To</th><th></th></tr></thead><tbody id="calrows"></tbody></table>
  <div class="row" style="margin-top:8px"><button id="caladd" type="button">Add a row</button><button id="calsave" type="button" class="primary">Save the dates</button></div>
  <p id="calmsg" class="dim"></p>
</div>

<div class="card"><h2>Recording</h2>
  <div class="form">
    <div class="row">
      <label>Route <input id="line" maxlength="8" style="width:6em"></label>
      <label>From <input id="start" type="time"></label>
      <label>To <input id="end" type="time"></label>
      <label class="tick"><input id="every" type="checkbox">
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
// the route under the stops, the stops under the buses
map.createPane('route').style.zIndex = 410; map.createPane('stops').style.zIndex = 620; map.createPane('buses').style.zIndex = 640;
const routeLayer = L.layerGroup().addTo(map), stopLayer = L.layerGroup().addTo(map), busLayer = L.layerGroup().addTo(map);
let st = null, fitted = false, routeDay = null, picked = null;  // picked: {kind: 'stop'|'bus', id}

function dayStart() {  // the window's start on the chosen day, local time
  const [h, m] = (st ? st.settings.start : '07:00').split(':').map(Number);
  const d = new Date($('day').value + 'T00:00:00'); d.setHours(h, m, 0, 0); return d;
}
function moment() { return new Date(dayStart().getTime() + Number($('t2').value) * 1000); }
function shownAt() { return $('follow').checked && $('day').value === today() ? new Date() : moment(); }
const stopRadius = () => { const z = map.getZoom(); return z >= 16 ? 8 : z >= 14 ? 6 : z >= 12 ? 4.5 : 3; };
map.on('zoomend', () => stopLayer.eachLayer(l => l.setRadius && l.setRadius(stopRadius())));

async function drawRoute() {
  if (routeDay === $('day').value) return;
  routeDay = $('day').value;
  const r = await api('GET', '/v1/buslog/route?day=' + routeDay);
  routeLayer.clearLayers(); stopLayer.clearLayers();
  const pts = [];
  r.directions.forEach((d, i) => {
    if (d.shape) {
      L.geoJSON(d.shape, { pane: 'route', style: { color: tok_('--ink'), weight: 10, opacity: .35 } }).addTo(routeLayer);
      L.geoJSON(d.shape, { pane: 'route', style: { color: tok_('--bus'), weight: 5, opacity: .95 } }).addTo(routeLayer);
    }
    const end = d.stops.length ? (d.stops[d.stops.length - 1].name || '') : '';
    d.stops.forEach(s => {
      if (s.lat == null) return;
      pts.push([s.lat, s.lon]);
      const c = L.circleMarker([s.lat, s.lon], { pane: 'stops', radius: stopRadius(), weight: 2.5, color: tok_('--ink'), fillColor: tok_('--card'), fillOpacity: 1 });
      c.bindTooltip(document.createTextNode((s.name || s.stop) + (s.letter ? ' (' + s.letter + ')' : '') + ' · towards ' + end));
      c.on('click', () => showStop(s.stop, s.name));
      c.addTo(stopLayer);
    });
  });
  if (pts.length && !fitted) { map.fitBounds(pts, { padding: [20, 20] }); fitted = true; }
  $('mapnote').textContent = r.directions.length ? 'Route as read on ' + r.directions.map(d => d.day).filter((v, i, a) => a.indexOf(v) === i).join(', ') + ' (TfL’s stop order, NaPTAN’s positions).'
    : 'No route recorded yet: press Download schedule now, or it is read at the start of the first recording minute.';
}

// a day's GPS tracks, for playing a recorded day back without asking the server each step
let tracks = null, tracksDay = null, playing = null, srDay = null, sr = null;
async function loadTracks() {
  if (tracksDay === $('day').value) return;
  tracksDay = $('day').value; tracks = null;
  const r = await api('GET', '/v1/buslog/tracks?day=' + tracksDay);
  tracks = r.buses;
}
function trackAt(pts, t) {  // [epoch, lat, lon, dest]: where a bus was at t (between two fixes a minute apart), or null
  let lo = 0, hi = pts.length - 1;
  if (!pts.length || pts[0][0] > t) return null;
  while (lo < hi) { const m = (lo + hi + 1) >> 1; if (pts[m][0] <= t) lo = m; else hi = m - 1; }
  const a = pts[lo], b = pts[lo + 1];
  if (t - a[0] > 180) return null;  // nothing heard for three minutes: not shown
  if (b && b[0] - a[0] <= 150) { const f = (t - a[0]) / (b[0] - a[0]); return [a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f, a[3], t - a[0]]; }
  return [a[1], a[2], a[3], t - a[0]];
}
const trailLayer = L.layerGroup().addTo(map);
function drawFromTracks(at) {
  const t = at.getTime() / 1000;
  busLayer.clearLayers(); trailLayer.clearLayers();
  let n = 0;
  for (const [v, pts] of Object.entries(tracks || {})) {
    const p = trackAt(pts, t); if (!p) continue; n++;
    const box = el('div', null, 'busicon' + (p[3] > 90 ? ' old' : '') + (picked && picked.kind === 'bus' && picked.id === v ? ' picked' : ''));
    box.append(el('span', v), el('small', 'to ' + (p[2] || '?')));
    const m = L.marker([p[0], p[1]], { pane: 'buses', icon: L.divIcon({ html: box, className: '', iconSize: null, iconAnchor: [34, 18] }) });
    m.bindTooltip(document.createTextNode(v + ' to ' + (p[2] || '?')));
    m.on('click', () => { picked = { kind: 'bus', id: v, name: p[2] }; if (!playing) showBus(v, p[2]).catch(() => {}); });
    m.addTo(busLayer);
    if (picked && picked.kind === 'bus' && picked.id === v) {  // its last ten minutes
      const trail = pts.filter(q => q[0] <= t && q[0] > t - 600).map(q => [q[1], q[2]]); trail.push([p[0], p[1]]);
      if (trail.length > 1) L.polyline(trail, { pane: 'buses', color: tok_('--accent'), weight: 4, opacity: .8 }).addTo(trailLayer);
    }
  }
  $('mapnote').textContent = n + ' bus' + (n === 1 ? '' : 'es') + ' at ' + hms(at) + ' (from their GPS positions, about one a minute).';
}

async function drawBuses() {
  const at = shownAt();
  $('tlabel').textContent = hms(at);
  const live = $('follow').checked && $('day').value === today();
  if (!live) {
    try { await loadTracks(); } catch (e) { tracks = null; }
    if (tracks) {
      drawFromTracks(at);
      if (picked && !playing) (picked.kind === 'stop' ? showStop(picked.id, picked.name, true) : showBus(picked.id, picked.name, true)).catch(() => {});
      return;
    }
  }
  trailLayer.clearLayers();
  const p = await api('GET', '/v1/buslog/positions?day=' + $('day').value + '&at=' + encodeURIComponent(at.toISOString()));
  busLayer.clearLayers();
  p.buses.forEach(b => {
    if (b.lat == null) return;
    const box = el('div', null, 'busicon' + (b.age_s > 90 ? ' old' : '') + (picked && picked.kind === 'bus' && picked.id === b.vehicle ? ' picked' : ''));
    box.append(el('span', b.vehicle), el('small', 'to ' + (b.destination || '?')));
    const m = L.marker([b.lat, b.lon], { pane: 'buses', icon: L.divIcon({ html: box, className: '', iconSize: null, iconAnchor: [34, 18] }) });
    m.bindTooltip(document.createTextNode(b.vehicle + ' to ' + (b.destination || '?') + ' · position ' + (b.age_s != null ? b.age_s + ' s old' : 'of unknown age') +
      (b.off_route_m != null ? ' · ' + b.off_route_m + ' m from the route' : '') + (b.occupancy ? ' · ' + b.occupancy : '')));
    m.on('click', () => showBus(b.vehicle, b.destination));
    m.addTo(busLayer);
  });
  const last = p.last_ok_poll ? new Date(p.last_ok_poll) : null;
  if (!p.last_poll) $('mapnote').textContent = 'No BODS request recorded by ' + hms(at) + ' on this day: nothing is known, which is not the same as no buses.';
  else if (!last || at - last > 60000) $('mapnote').textContent = 'BODS last answered at ' + (last ? hms(last) : 'no time this day') + ': the buses shown may be out of date.';
  else $('mapnote').textContent = p.buses.length + ' bus' + (p.buses.length === 1 ? '' : 'es') + ' in BODS’s answers in the 3 minutes to ' + hms(at) + '.';
  if (picked) (picked.kind === 'stop' ? showStop(picked.id, picked.name, true) : showBus(picked.id, picked.name, true)).catch(() => {});
}

const hm = v => v ? new Date(v).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : '–';
function table(head, rows) {
  const t = el('table'), th = el('thead'), tr = el('tr'); head.forEach(h => tr.append(el('th', h))); th.append(tr); t.append(th);
  const tb = el('tbody'); rows.forEach(r => { const row = el('tr'); r.forEach(c => row.append(c instanceof Node ? (() => { const td = el('td'); td.append(c); return td; })() : el('td', c))); tb.append(row); });
  t.append(tb); return t;
}
async function showStop(stop, name, quiet) {
  picked = { kind: 'stop', id: stop, name };
  const at = shownAt();
  const r = await api('GET', '/v1/buslog/stop/' + encodeURIComponent(stop) + '?day=' + $('day').value + '&at=' + encodeURIComponent(at.toISOString()));
  $('detail').hidden = false; $('dtitle').textContent = (r.name || name || stop) + ': the next hour from ' + hm(at);
  $('dsub').textContent = r.timetable_taken_at ? 'Timetable as taken at ' + hm(r.timetable_taken_at) + '; expected times are TfL’s as they stood then.' : 'No timetable taken for this day: press Download schedule now.';
  const box = $('dbody'); box.replaceChildren();
  box.append(el('h3', 'Expected (TfL)'));
  box.append(r.expected.length ? table(['Bus', 'To', 'Expected'], r.expected.map(e => [e.vehicle, e.to || '', hm(e.expected_at)])) : el('p', 'No bus predicted here in this hour.', 'dim'));
  box.append(el('h3', 'Timetable'));
  box.append(r.scheduled.length ? table(['Due', 'To'], r.scheduled.map(e => [hm(e.scheduled_at), e.to || e.direction])) : el('p', r.timetable_taken_at ? 'Nothing timetabled here in this hour.' : 'No timetable.', 'dim'));
  if (!quiet) $('detail').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
async function showBus(vehicle, dest, quiet) {
  picked = { kind: 'bus', id: vehicle, name: dest };
  const at = shownAt();
  const r = await api('GET', '/v1/buslog/vehicle/' + encodeURIComponent(vehicle) + '?day=' + $('day').value + '&at=' + encodeURIComponent(at.toISOString()));
  $('detail').hidden = false; $('dtitle').textContent = vehicle + ' to ' + (r.destination || dest || '?') + (r.stops.length ? ': ' + r.now : '');
  $('dsub').textContent = r.note || ((r.departed ? 'Running the ' + hm(r.departed) + ' journey' : 'No timetabled journey matches it') + ' · as TfL expected at ' + hm(at) + '. On time is within a minute either way.');
  const box = $('dbody'); box.replaceChildren();
  if (r.stops.length) box.append(table(['Stop', 'Due', 'Expected', ''], r.stops.map(s => {
    const v = el('span', s.words, s.verdict === 'late' ? 'v-late' : s.verdict === 'early' ? 'v-early' : s.verdict === 'on time' ? 'v-on' : 'dim');
    return [s.name || s.stop, hm(s.scheduled_at), hm(s.expected_at), v];
  })));
  if (!quiet) $('detail').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
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

const hmv = (v, probable) => v ? (probable ? '≈' : '') + hm(v) : '–';
function timeCell(actual, probable, extra) {
  const td = el('td', null, 'n');
  if (actual) td.append(el('span', hm(actual)));
  else if (probable) { const sp = el('span', '≈' + hm(probable), 'prob'); sp.title = 'probable, from its GPS'; td.append(sp); }
  else td.append(el('span', '–', 'dim'));
  if (extra) td.append(el('span', extra, 'sub'));
  return td;
}
function badge(b) {
  const cls = b.outcome === 'made_it' ? (b.in_time ? 'good' : 'warn') : b.outcome === 'other_side' ? 'warn'
    : ['turned', 'skipped', 'left_route'].includes(b.outcome) ? 'bad' : '';
  const label = { made_it: b.in_time ? 'Made it' : 'Made it, late', other_side: 'Other side', turned: 'Turned', skipped: 'Skipped the school',
    left_route: 'Left the route', lost: 'Not known', not_seen: 'Not seen', no_data: 'No data' }[b.outcome] || b.outcome;
  return el('span', label, 'badge ' + cls);
}
const mins = s => Math.round(s / 60);
function drawMarks() {
  const box = $('marks'); box.replaceChildren();
  if (!sr || !sr.events) return;
  const start = dayStart().getTime(), len = Number($('t2').max) * 1000, w = box.clientWidth || 600;
  const lanes = [];  // marks close together go in rows, so each can be tapped
  for (const e of sr.events) {
    const x = (new Date(e.at).getTime() - start) / len; if (x < 0 || x > 1) continue;
    const px = x * w; let lane = lanes.findIndex(r => px - r >= 12); if (lane < 0) { lane = lanes.length; lanes.push(-1e9); } lanes[lane] = px;
    const b = el('button', null, e.kind); b.type = 'button'; b.style.left = (x * 100) + '%'; b.style.top = (2 + lane * 16) + 'px';
    b.title = hm(e.at) + ' ' + e.text; b.setAttribute('aria-label', b.title);
    b.onclick = () => jump(e);
    box.append(b);
  }
  box.style.height = (4 + Math.max(1, lanes.length) * 16) + 'px';
}
window.addEventListener('resize', () => drawMarks());
function jump(e) {
  stopPlay(); $('follow').checked = false;
  $('t2').value = Math.max(0, Math.round((new Date(e.at).getTime() - dayStart().getTime()) / 1000) - 45);
  if (e.vehicle) picked = { kind: 'bus', id: e.vehicle, name: null };
  drawBuses().catch(err => msg.textContent = err.message);
  $('map').scrollIntoView({ behavior: 'smooth', block: 'center' });
}
function stopRows(b) {
  const t = el('table', null, 'sr'), h = el('tr');
  ['Stop', 'Timetable', 'TfL expected', 'Got there', ''].forEach(x => h.append(el('th', x))); t.append(h);
  for (const r of b.stops) {
    const tr = el('tr');
    tr.append(el('td', r.name || r.stop));
    tr.append(el('td', hm(r.scheduled), 'n'));
    const ex = el('td', null, 'n'); ex.append(el('span', hm(r.last_expected)));
    if (r.first_expected && r.first_expected !== r.last_expected) ex.append(el('span', 'first ' + hm(r.first_expected) + ' (at ' + hm(r.first_seen) + ')', 'sub'));
    if (r.late) ex.append(el('span', 'showed ' + mins(r.late.late_s) + ' min late at ' + hm(r.late.seen_at), 'sub'));
    tr.append(ex);
    tr.append(timeCell(r.actual, r.probable));
    const late = (r.actual || r.probable) && r.scheduled ? mins((new Date(r.actual || r.probable) - new Date(r.scheduled)) / 1000) : null;
    tr.append(el('td', r.note || (late == null ? '' : late === 0 ? 'on time' : Math.abs(late) + ' min ' + (late > 0 ? 'late' : 'early')), r.note ? 'v-late' : 'dim'));
    t.append(tr);
  }
  return t;
}
async function drawSchoolRun(fresh) {
  const day = $('day').value;
  if (srDay === day && !fresh) return;
  srDay = day;
  $('srmsg').textContent = fresh ? 'Working it out…' : '';
  try { sr = await api('GET', '/v1/buslog/schoolrun?day=' + day + (fresh ? '&fresh=true' : '')); }
  catch (e) { sr = null; $('srsay').textContent = e.message; $('srtable').replaceChildren(); $('srkpi').replaceChildren(); return; }
  $('srmsg').textContent = '';
  if (sr.first && sr.school) $('srtitle').textContent = sr.first.name + ' to ' + sr.school.name;
  const t = $('srtable'); t.replaceChildren(); $('srkpi').replaceChildren();
  if (!sr.recorded) { $('srsay').textContent = 'Nothing to show for ' + day + ': ' + sr.why + '.'; drawMarks(); return; }
  const s = sr.summary, by = sr.by ? hm(sr.by) : null;
  [[s.recorded, 'buses recorded'], [s.made_it + (s.percent != null ? ' (' + s.percent + '%)' : ''), 'reached the school'],
   [s.in_time, by ? 'by ' + by : 'in time'], [s.turned, 'turned early'], [s.skipped, 'skipped or left the route'], [s.not_seen + s.no_data, 'timetabled, not seen']]
    .forEach(([v, l]) => { const d = el('div'); d.append(el('b', String(v)), el('span', l, 'dim')); $('srkpi').append(d); });
  $('srsay').textContent = (sr.timetable ? '' : 'No timetable was taken this day, so times are against nothing. ') +
    (s.other_side ? s.other_side + ' reached the school from the other side of the road. ' : '') +
    (sr.gap_minutes ? sr.gap_minutes + ' minutes of the window had no positions received. ' : '') +
    (sr.status && sr.status.length ? 'TfL: ' + sr.status.map(x => x.severity + (x.reason ? ' (' + x.reason + ')' : '')).join('; ') : '');
  const h = el('tr');
  ['Bus', sr.first.name + ': due', 'left', sr.school.name + ': due', 'expected', 'got there', 'What happened'].forEach(x => h.append(el('th', x))); t.append(h);
  for (const b of sr.buses) {
    const tr = el('tr', null, 'bus');
    tr.append(el('td', b.vehicle || '–'));
    tr.append(el('td', hm(b.due_first), 'n'));
    tr.append(timeCell(b.left_first_actual ? b.left_first : null, b.left_first_actual ? null : b.left_first));
    tr.append(el('td', hm(b.due_school), 'n'));
    const ex = el('td', null, 'n'); ex.append(el('span', hm(b.expected_school)));
    if (b.first_expected_school && b.first_expected_school !== b.expected_school) ex.append(el('span', 'first ' + hm(b.first_expected_school), 'sub'));
    tr.append(ex);
    tr.append(timeCell(b.arrived_school_actual ? b.arrived_school : null, b.arrived_school_actual ? null : b.arrived_school));
    const w = el('td'); w.append(badge(b), el('span', b.words, 'sub'));
    if (b.delay) w.append(el('span', 'TfL showed it ' + mins(b.delay.late_s) + ' min late at ' + hm(b.delay.seen_at), 'sub'));
    tr.append(w);
    t.append(tr);
    if (!b.vehicle) { tr.classList.remove('bus'); continue; }
    tr.onclick = () => {
      const open = tr.nextSibling && tr.nextSibling.classList && tr.nextSibling.classList.contains('more');
      t.querySelectorAll('tr.more').forEach(x => x.remove()); if (open) return;
      const more = el('tr', null, 'more'), td = el('td'); td.colSpan = 7;
      const watch = el('button', 'Watch it on the map'); watch.type = 'button';
      watch.onclick = ev => { ev.stopPropagation(); jump({ at: b.left_first || b.due_first, vehicle: b.vehicle }); startPlay(); };
      td.append(watch, stopRows(b)); more.append(td); tr.after(more);
    };
  }
  drawMarks();
}
async function drawReliability() {
  const r = await api('GET', '/v1/buslog/schoolrun/reliability');
  const t = $('reltable'); t.replaceChildren(); $('relkpi').replaceChildren();
  const s = r.total;
  if (!r.days.length) { t.append(el('tr')); $('relkpi').append(el('p', 'No recorded mornings yet.', 'dim')); return; }
  [[r.days.length, 'mornings'], [s.recorded, 'buses recorded'], [s.made_it + ' (' + s.percent + '%)', 'reached the school'],
   [s.in_time + ' (' + s.in_time_percent + '%)', 'in time'], [s.turned + s.skipped, 'turned early or skipped it']]
    .forEach(([v, l]) => { const d = el('div'); d.append(el('b', String(v)), el('span', l, 'dim')); $('relkpi').append(d); });
  const h = el('tr'); ['Day', 'Recorded', 'Made it', '%', 'In time', 'Other side', 'Turned', 'Skipped', 'Not seen'].forEach(x => h.append(el('th', x))); t.append(h);
  for (const d of r.days) {
    const x = d.summary, tr = el('tr', null, 'bus');
    const day = new Date(d.day + 'T12:00:00');
    tr.append(el('td', day.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' }) + (d.gap_minutes ? ' *' : ''), 'n'));
    tr.append(el('td', String(x.recorded)), el('td', String(x.made_it)));
    const p = el('td', null, 'n'); p.append(el('span', (x.percent ?? '–') + '%')); const bar = el('span', null, 'bar'); bar.style.width = (x.percent || 0) * .6 + 'px'; p.append(bar); tr.append(p);
    tr.append(el('td', String(x.in_time)), el('td', String(x.other_side)), el('td', String(x.turned)), el('td', String(x.skipped)), el('td', String(x.not_seen + x.no_data)));
    if (d.gap_minutes) tr.title = d.gap_minutes + ' minutes with no positions received';
    tr.onclick = () => { $('day').value = d.day; $('follow').checked = false; load(); $('sr').scrollIntoView({ behavior: 'smooth' }); };
    t.append(tr);
  }
}
async function loadSrSettings() {
  const [cfg, rt] = await Promise.all([api('GET', '/v1/buslog/schoolrun/settings'), api('GET', '/v1/buslog/route?day=' + $('day').value)]);
  const fill = (sel, stops, v, none) => { sel.replaceChildren(); if (none) { const o = el('option', '(none)'); o.value = ''; sel.append(o); }
    stops.forEach(s => { const o = el('option', (s.name || s.stop) + (s.letter ? ' (' + s.letter + ')' : '') + ' · ' + s.dir); o.value = s.stop; sel.append(o); }); sel.value = v || ''; };
  const all = rt.directions.flatMap(d => d.stops.map(s => ({ ...s, dir: 'towards ' + (d.stops.length ? d.stops[d.stops.length - 1].name : '') })));
  fill($('srfirst'), all, cfg.first); fill($('srschool'), all, cfg.school); fill($('srother'), all, cfg.school_other, true);
  $('srby').value = cfg.by || '';
}
$('srsave').onclick = async () => {
  try { await api('PUT', '/v1/buslog/schoolrun/settings', { first: $('srfirst').value, school: $('srschool').value, school_other: $('srother').value || null, by: $('srby').value || null });
    $('srsetmsg').textContent = 'Saved.'; srDay = null; await drawSchoolRun(); await drawReliability(); } catch (e) { $('srsetmsg').textContent = e.message; }
};
$('srfresh').onclick = () => drawSchoolRun(true).then(drawReliability).catch(e => $('srmsg').textContent = e.message);
$('srdl').onclick = async () => {
  $('srmsg').textContent = 'Preparing the file…';
  try {
    const r = await fetch('/v1/buslog/export?day=' + $('day').value, { headers: { Authorization: 'Bearer ' + tok.value } });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const a = document.createElement('a'); a.href = URL.createObjectURL(await r.blob()); a.download = 'buslog-' + $('day').value + '.json.gz'; a.click();
    $('srmsg').textContent = 'Saved as buslog-' + $('day').value + '.json.gz.';
  } catch (e) { $('srmsg').textContent = e.message; }
};
function startPlay() {
  if (playing) return;
  $('follow').checked = false; $('play').textContent = '❚❚ Pause';
  let last = performance.now();
  playing = setInterval(() => {
    const now = performance.now(), step = (now - last) / 1000 * Number($('speed').value); last = now;
    const v = Math.min(Number($('t2').max), Number($('t2').value) + step);
    $('t2').value = v;
    if (v >= Number($('t2').max)) stopPlay();
    drawBuses().catch(() => {});
  }, 250);
}
function stopPlay() { if (playing) clearInterval(playing); playing = null; $('play').textContent = '▶ Play'; }
$('play').onclick = () => playing ? stopPlay() : startPlay();

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
    drawSchoolRun().catch(e => $('srsay').textContent = e.message);
  } catch (e) { msg.textContent = e.message; }
}
$('day').value = today();
$('t2').oninput = () => { $('follow').checked = false; drawBuses().catch(e => msg.textContent = e.message); };
$('day').onchange = () => { stopPlay(); $('follow').checked = $('day').value === today(); load(); };
$('follow').onchange = () => { if ($('follow').checked) stopPlay(); drawBuses().catch(() => {}); };
$('save').onclick = async () => {
  try { await api('PUT', '/v1/buslog/settings', { line: $('line').value.trim(), start: $('start').value, end: $('end').value, every_day: $('every').checked });
    $('setmsg').textContent = 'Saved.'; routeDay = null; load(); } catch (e) { $('setmsg').textContent = e.message; }
};
$('now').onclick = async () => { try { const r = await api('POST', '/v1/buslog/record-now', { minutes: Number($('mins').value) }); $('setmsg').textContent = 'Recording from the next minute until ' + hms(new Date(r.record_until)) + '.'; load(); } catch (e) { $('setmsg').textContent = e.message; } };
$('stop').onclick = async () => { try { await api('DELETE', '/v1/buslog/record-now'); $('setmsg').textContent = 'Stopped (the window and test switch still apply).'; load(); } catch (e) { $('setmsg').textContent = e.message; } };

const KINDS = [['terms', 'Term'], ['breaks', 'Holiday'], ['inset', 'INSET day'], ['notes', 'Note']];
let cal = null;
function calRow(kind, name, from, to) {
  const tr = el('tr'), sel = el('select');
  KINDS.forEach(([k, label]) => { const o = el('option', label); o.value = k; sel.append(o); }); sel.value = kind;
  const n = el('input'); n.value = name || ''; n.maxLength = 120;
  const f = el('input'); f.type = 'date'; f.value = from || '';
  const t = el('input'); t.type = 'date'; t.value = to || '';
  const sync = () => { t.disabled = sel.value === 'inset' || sel.value === 'notes'; if (t.disabled) t.value = ''; }; sel.onchange = sync; sync();
  const x = el('td', null, 'x'), b = button('Remove', () => tr.remove()); x.append(b);
  [sel, n, f, t].forEach(i => { const td = el('td'); td.append(i); tr.append(td); }); tr.append(x);
  $('calrows').append(tr);
}
function button(text, fn) { const b = el('button', text); b.type = 'button'; b.onclick = fn; return b; }
async function loadCal() {
  cal = await api('GET', '/v1/buslog/calendar');
  $('school').value = cal.school || ''; $('calrows').replaceChildren();
  const rows = [...(cal.terms || []).map(t => ['terms', t.name, t.from, t.to]), ...(cal.breaks || []).map(t => ['breaks', t.name, t.from, t.to]),
    ...(cal.inset || []).map(d => ['inset', 'INSET day', d, '']), ...(cal.notes || []).map(n => ['notes', n.note, n.date, ''])];
  rows.sort((a, b) => a[2] < b[2] ? -1 : a[2] > b[2] ? 1 : 0).forEach(r => calRow(...r));
}
$('caladd').onclick = () => calRow('breaks', '', '', '');
$('calsave').onclick = async () => {
  const out = { school: $('school').value.trim(), terms: [], breaks: [], inset: [], notes: [] };
  for (const tr of $('calrows').children) {
    const [k, n, f, t] = [...tr.querySelectorAll('select, input')].map(i => i.value);
    if (!f) continue;
    if (k === 'inset') out.inset.push(f);
    else if (k === 'notes') out.notes.push({ date: f, note: n });
    else out[k].push({ name: n, from: f, to: t || f });
  }
  try { await api('PUT', '/v1/buslog/calendar', out); $('calmsg').textContent = 'Saved.'; await loadCal(); load(); } catch (e) { $('calmsg').textContent = e.message; }
};
$('sched').onclick = async () => {
  $('schedmsg').textContent = 'Asking TfL for today’s route and timetable…'; $('sched').disabled = true;
  try {
    const r = await api('POST', '/v1/buslog/schedule-now');
    const t = r.taken || {};
    $('schedmsg').textContent = (r.ok ? 'Taken: ' : 'Only partly taken: ') + ['outbound', 'inbound'].map(d => d + ' ' + (t[d] != null ? t[d] + ' stops' : 'failed') + (t[d + '_schedule'] ? ' (' + t[d + '_schedule'] + ')' : '')).join(', ') + '.';
    routeDay = null; await load();
  } catch (e) { $('schedmsg').textContent = e.message; } finally { $('sched').disabled = false; }
};
loadCal().catch(e => $('calmsg').textContent = e.message);
drawReliability().catch(e => $('relkpi').textContent = e.message);
loadSrSettings().catch(e => $('srsetmsg').textContent = e.message);
$('go').onclick = load;
load();
setInterval(() => { if (!document.hidden && $('follow').checked) drawBuses().catch(() => {}); }, 10000);
setInterval(() => { if (!document.hidden && !playing) load(); }, 60000);
</script></main></body></html>"""


@page_router.get("/buslog", response_class=HTMLResponse, include_in_schema=False)
async def buslog_page(request: Request) -> str:
    s = get_settings()
    cfg = json.dumps({"tile_url": s.map_tile_url, "attribution": s.map_tile_attribution, "max_zoom": s.map_max_zoom}, ensure_ascii=False)
    return themed(PAGE.replace("/*MAP_CONFIG*/", cfg), "buslog", request)
