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
    <label class="tick"><input id="follow" type="checkbox" checked> Live</label>
    <button id="sched" type="button">Download schedule now</button>
  </div>
  <p class="dim" id="schedmsg"></p>
  <div id="map" role="img" aria-label="Map of the route, its stops and its buses"></div>
  <p class="dim" id="mapnote"></p>
  <p class="dim">Tap a stop for its next hour, or a bus for its times to the end of its route.</p>
</div>

<div class="card" id="detail" hidden><h2 id="dtitle"></h2><p class="dim" id="dsub"></p><div id="dbody"></div></div>

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

async function drawBuses() {
  const at = shownAt();
  $('tlabel').textContent = hms(at);
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
