"""The Car page (/car): everything the car's OVMS module reports — battery, charging, cabin, tyres, position, motor,
module and the Leaf's own dashboard readings — grouped and labelled, with a chart of any kept metric over time, and
(once the broker is on TLS) the controls. Holds no data; calls /v1/ovms."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

page_router = APIRouter()

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant car</title>
<style>
/*BASE_CSS*/
.top { display: flex; flex-wrap: wrap; align-items: center; gap: 14px 28px; }
.batt { display: flex; align-items: center; gap: 10px; } .batt b { font-size: 1.8em; font-variant-numeric: tabular-nums; }
.now b { display: block; font-size: 1.4em; } .now span { display: block; color: var(--dim); }
@keyframes fill { 0%, 100% { opacity: .25; } 50% { opacity: 1; } } .filling { animation: fill 1.6s ease-in-out infinite; }
.tiles { display: grid; gap: 10px; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); margin-top: 4px; }
.tile { border: 1px solid var(--line); border-radius: 12px; padding: 8px 10px; } .tile .k { font-size: 12px; color: var(--dim); }
.tile .v { font-size: 1.25em; font-weight: 650; font-variant-numeric: tabular-nums; }
.grp { border-bottom: 1px solid var(--line); } .grp:last-child { border-bottom: 0; }
.grp > summary { cursor: pointer; padding: 10px 2px; font-weight: 650; list-style: none; display: flex; justify-content: space-between; gap: 10px; }
.grp > summary::-webkit-details-marker { display: none; } .grp > summary::after { content: '▸'; color: var(--dim); }
.grp[open] > summary::after { content: '▾'; }
.kv { display: grid; grid-template-columns: 1fr auto auto; gap: 2px 14px; padding: 0 2px 10px; font-size: .92em; }
.kv .mk { color: var(--ink); } .kv .mv { font-variant-numeric: tabular-nums; font-weight: 600; text-align: right; }
.kv .mt { color: var(--dim); white-space: nowrap; font-size: .9em; }
.kv .raw { color: var(--dim); font-size: .85em; }
.chart { width: 100%; height: 200px; } .chart path.ln { fill: none; stroke: var(--series-1); stroke-width: 2; }
.chart line.ax { stroke: var(--line); } .chart text { fill: var(--dim); font-size: 11px; }
.warn { color: var(--bad); } .seg select { min-width: 0; }
.ctl { display: grid; gap: 10px; } .cg { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cg .cl { min-width: 5.5em; font-weight: 650; } .cbtn { padding: 8px 18px; }
</style></head><body><main>
<!--NAV-->
<h1>Car</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<select id="car" aria-label="Car" hidden></select><button id="go" class="primary" type="button">Load</button></div>
<p id="msg" class="err"></p>

<div class="card" id="none" hidden><div class="say">No car has reported through OVMS yet.</div>
  <p class="dim">The OVMS module publishes to the broker set in Settings → Home Assistant (Server V3), and World
  Assistant's broker user must be able to read <code>ovms/#</code>. See <a href="/docs/ovms.md">how to set it up</a>.</p></div>

<section id="view" hidden>
<div class="card">
  <div class="top">
    <div class="batt"><svg id="bsvg" viewBox="0 0 132 52" width="110" height="43" role="img" aria-label="Car battery"></svg><b id="pct">–</b></div>
    <div class="now"><b id="state">–</b><span id="sub"></span></div>
    <div class="now"><b id="range">–</b><span>range</span></div>
  </div>
  <p class="dim" id="seen"></p>
  <div id="warns"></div>
  <div class="tiles" id="tiles"></div>
</div>

<div class="card"><h2>Controls</h2>
  <div class="ctl">
    <div class="cg"><span class="cl">Charging</span><button class="cbtn" data-a="charge-on" type="button">Start</button><button class="cbtn" data-a="charge-off" type="button">Stop</button></div>
    <div class="cg"><span class="cl">Climate</span><button class="cbtn" data-a="climate-on" type="button">On</button><button class="cbtn" data-a="climate-off" type="button">Off</button></div>
    <div class="cg"><span class="cl">Doors</span><button class="cbtn" data-a="unlock" type="button">Unlock</button><button class="cbtn" data-a="lock" type="button">Lock</button></div>
  </div>
  <p class="dim small" id="cmsg">Commands go to the car over MQTT, so they need the broker on TLS. The car must be awake — turning on the A/C wakes its CAN bus. Lock and unlock ask for your OVMS module PIN.</p></div>

<div class="card"><h2>History</h2>
  <div class="row seg"><label>Metric <select id="metric"></select></label>
    <label>Over <select id="days"><option value="1">a day</option><option value="7" selected>a week</option>
      <option value="30">a month</option><option value="365">a year</option></select></label></div>
  <svg class="chart" id="chart" viewBox="0 0 600 200" role="img" aria-label="The chosen metric over time"></svg>
  <p class="dim small" id="chartnote"></p></div>

<div class="card"><h2>All readings</h2><div id="groups"></div>
  <p class="dim small">Everything the module has sent, as it stands. Tap a group to open it.</p></div>
</section>

<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
async function api(path) {
  const r = await fetch(path, { headers: { Authorization: 'Bearer ' + tok.value } });
  if (!r.ok) { let d = r.status; try { d = (await r.json()).detail; } catch (e) {} throw new Error(typeof d === 'string' ? d : JSON.stringify(d)); }
  return r.json();
}
async function apiPost(path, body) {
  const r = await fetch(path, { method: 'POST', headers: { Authorization: 'Bearer ' + tok.value, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!r.ok) { let d = r.status; try { d = (await r.json()).detail; } catch (e) {} throw new Error(typeof d === 'string' ? d : JSON.stringify(d)); }
  return r.json();
}
async function control(a) {
  const c = cur(); if (!c) return;
  let path, body;
  if (a === 'lock' || a === 'unlock') {
    const pin = prompt('Your OVMS module PIN, to ' + a + ' the car:'); if (!pin) return;
    path = '/lock'; body = { lock: a === 'lock', pin };
  } else {
    const act = { 'charge-on': 'start charging', 'charge-off': 'stop charging', 'climate-on': 'turn climate on', 'climate-off': 'turn climate off' }[a];
    if (!confirm('Send “' + act + '” to the car?')) return;
    path = a.startsWith('charge') ? '/charge' : '/climate'; body = { on: a.endsWith('-on') };
  }
  $('cmsg').textContent = 'Sending…'; document.querySelectorAll('.cbtn').forEach(b => b.disabled = true);
  try { const r = await apiPost('/v1/ovms/' + encodeURIComponent(c.id) + path, body);
    $('cmsg').textContent = r.ok ? ('Done' + (r.response ? ': ' + r.response : '.')) : ('Didn’t go through: ' + r.error);
  } catch (e) { $('cmsg').textContent = e.message; }
  document.querySelectorAll('.cbtn').forEach(b => b.disabled = false);
  setTimeout(() => { if (!document.hidden) load(); }, 2500);
}
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const mi = km => km == null ? null : km * 0.621371;
const ago = s => { if (!s) return 'never'; const m = Math.round((Date.now() - new Date(s)) / 60000); return m < 1 ? 'just now' : m < 90 ? m + ' min ago' : m < 1440 ? Math.round(m / 60) + ' h ago' : Math.round(m / 1440) + ' d ago'; };
const NS = 'http://www.w3.org/2000/svg', svg = (t, a) => { const e = document.createElementNS(NS, t); for (const k in a) e.setAttribute(k, a[k]); return e; };

// metric -> [label, unit, group]. Anything not here is grouped by its prefix and shown by its raw name.
const L = {
  'v.b.soc': ['State of charge', '%', 'Battery'], 'v.b.soh': ['State of health', '%', 'Battery'],
  'v.b.health': ['Health', '', 'Battery'], 'v.b.cac': ['Usable capacity', 'Ah', 'Battery'],
  'v.b.range.est': ['Range (estimated)', 'km', 'Battery'], 'v.b.range.ideal': ['Range (ideal)', 'km', 'Battery'],
  'v.b.12v.voltage': ['12V battery', 'V', 'Battery'], 'v.b.voltage': ['Pack voltage', 'V', 'Battery'],
  'v.b.current': ['Pack current', 'A', 'Battery'], 'v.b.power': ['Pack power', 'kW', 'Battery'],
  'v.b.temp': ['Battery temperature', '°C', 'Battery'], 'v.b.energy.used': ['Energy used', 'kWh', 'Battery'],
  'v.b.energy.recd': ['Energy recovered', 'kWh', 'Battery'], 'v.b.consumption': ['Consumption', 'Wh/km', 'Battery'],
  'v.c.state': ['Charge state', '', 'Charging'], 'v.c.mode': ['Charge mode', '', 'Charging'],
  'v.c.charging': ['Charging', '', 'Charging'], 'v.c.pilot': ['Pilot present', '', 'Charging'],
  'v.c.power': ['Charge power', 'kW', 'Charging'], 'v.c.current': ['Charge current', 'A', 'Charging'],
  'v.c.voltage': ['Charge voltage', 'V', 'Charging'], 'v.c.efficiency': ['Charge efficiency', '%', 'Charging'],
  'v.c.temp': ['Charger temperature', '°C', 'Charging'], 'v.c.climit': ['Current limit', 'A', 'Charging'],
  'v.c.kwh': ['Added this session', 'kWh', 'Charging'], 'v.c.duration.full': ['Time to full', 'min', 'Charging'],
  'v.c.limit.soc': ['Charge to', '%', 'Charging'], 'v.c.limit.range': ['Charge to range', 'km', 'Charging'],
  'v.e.on': ['Car on', '', 'Cabin & doors'], 'v.e.awake': ['Awake', '', 'Cabin & doors'],
  'v.e.locked': ['Locked', '', 'Cabin & doors'], 'v.e.handbrake': ['Handbrake', '', 'Cabin & doors'],
  'v.e.alarm': ['Alarm', '', 'Cabin & doors'], 'v.e.parktime': ['Parked for', 's', 'Cabin & doors'],
  'v.e.drivetime': ['Driving for', 's', 'Cabin & doors'], 'v.e.cabintemp': ['Cabin temperature', '°C', 'Cabin & doors'],
  'v.e.temp': ['Outside temperature', '°C', 'Cabin & doors'], 'v.e.throttle': ['Throttle', '%', 'Cabin & doors'],
  'v.e.footbrake': ['Brake', '%', 'Cabin & doors'], 'v.e.gear': ['Gear', '', 'Cabin & doors'],
  'v.e.hvac': ['Climate on', '', 'Cabin & doors'], 'v.e.cooling': ['Cooling', '', 'Cabin & doors'],
  'v.e.headlights': ['Headlights', '', 'Cabin & doors'],
  'v.d.fl': ['Front-left door', '', 'Cabin & doors'], 'v.d.fr': ['Front-right door', '', 'Cabin & doors'],
  'v.d.rl': ['Rear-left door', '', 'Cabin & doors'], 'v.d.rr': ['Rear-right door', '', 'Cabin & doors'],
  'v.d.hood': ['Bonnet', '', 'Cabin & doors'], 'v.d.trunk': ['Boot', '', 'Cabin & doors'],
  'v.d.cp': ['Charge port', '', 'Cabin & doors'],
  'v.p.latitude': ['Latitude', '', 'Position'], 'v.p.longitude': ['Longitude', '', 'Position'],
  'v.p.odometer': ['Odometer', 'km', 'Position'], 'v.p.speed': ['Speed', 'km/h', 'Position'],
  'v.p.gpsspeed': ['GPS speed', 'km/h', 'Position'], 'v.p.altitude': ['Altitude', 'm', 'Position'],
  'v.p.direction': ['Heading', '°', 'Position'], 'v.p.satcount': ['Satellites', '', 'Position'],
  'v.p.trip': ['Trip', 'km', 'Position'], 'v.p.gpslock': ['GPS lock', '', 'Position'],
  'v.m.temp': ['Motor temperature', '°C', 'Motor'], 'v.m.rpm': ['Motor', 'rpm', 'Motor'],
  'v.i.temp': ['Inverter temperature', '°C', 'Motor'],
  'v.tp.fl.p': ['Front-left pressure', 'kPa', 'Tyres'], 'v.tp.fr.p': ['Front-right pressure', 'kPa', 'Tyres'],
  'v.tp.rl.p': ['Rear-left pressure', 'kPa', 'Tyres'], 'v.tp.rr.p': ['Rear-right pressure', 'kPa', 'Tyres'],
  'v.tp.fl.t': ['Front-left tyre temp', '°C', 'Tyres'], 'v.tp.fr.t': ['Front-right tyre temp', '°C', 'Tyres'],
  'v.tp.rl.t': ['Rear-left tyre temp', '°C', 'Tyres'], 'v.tp.rr.t': ['Rear-right tyre temp', '°C', 'Tyres'],
  'm.net.sq': ['Signal', 'dBm', 'Module & network'], 'm.net.provider': ['Network', '', 'Module & network'],
  'm.net.type': ['Network type', '', 'Module & network'], 'm.version': ['Firmware', '', 'Module & network'],
  'm.net.good': ['Connected', '', 'Module & network'],
  'xnl.v.b.soc.instrument': ['Dashboard charge', '%', 'Leaf (dashboard)'],
  'xnl.v.b.soc.newcar': ['Charge (new-car basis)', '%', 'Leaf (dashboard)'],
  'xnl.v.b.soc.nominal': ['Charge (nominal)', '%', 'Leaf (dashboard)'],
  'xnl.v.b.range.instrument': ['Dashboard range', 'km', 'Leaf (dashboard)'],
};
const GROUP_ORDER = ['Battery', 'Charging', 'Cabin & doors', 'Tyres', 'Position', 'Motor', 'Leaf (dashboard)', 'Module & network', 'Other'];
// metrics worth charting (numeric), offered in the History picker when present
const CHARTABLE = ['v.b.soc', 'xnl.v.b.soc.instrument', 'v.b.range.est', 'v.b.soh', 'v.c.power', 'v.c.kwh', 'v.b.power',
  'v.b.temp', 'v.e.cabintemp', 'v.e.temp', 'v.b.12v.voltage', 'v.p.odometer', 'v.c.efficiency', 'v.m.temp'];
const BOOL = { yes: 'Yes', no: 'No', on: 'On', off: 'Off', true: 'Yes', false: 'No' };
function prettyName(m) { if (L[m]) return L[m][0]; const t = m.replace(/^xnl\\./, 'Leaf ').replace(/\\./g, ' '); return t.charAt(0).toUpperCase() + t.slice(1); }
function groupOf(m) { if (L[m]) return L[m][2]; if (m.startsWith('v.b')) return 'Battery'; if (m.startsWith('v.c')) return 'Charging';
  if (m.startsWith('v.e') || m.startsWith('v.d')) return 'Cabin & doors'; if (m.startsWith('v.tp') || m.startsWith('v.t')) return 'Tyres';
  if (m.startsWith('v.p')) return 'Position'; if (m.startsWith('v.m') || m.startsWith('v.i')) return 'Motor';
  if (m.startsWith('xnl')) return 'Leaf (dashboard)'; if (m.startsWith('m.')) return 'Module & network'; return 'Other'; }
function fmtVal(m, raw) {
  const unit = L[m] ? L[m][1] : '';
  const low = String(raw).trim().toLowerCase();
  if (raw === '' || raw == null) return '–';
  if (low in BOOL) return BOOL[low];
  const n = Number(raw);
  if (!isNaN(n) && raw !== '') return (Math.abs(n) >= 100 ? Math.round(n) : n.toFixed(Math.abs(n) < 10 ? 2 : 1)).toLocaleString?.() ?? n + (unit ? ' ' + unit : '');
  return raw;
}
function unitOf(m, raw) { const low = String(raw).trim().toLowerCase(); if (low in BOOL || isNaN(Number(raw))) return ''; return L[m] ? L[m][1] : ''; }

let data = null, car = null;
const cur = () => data && data.cars.find(c => c.id === $('car').value);
function battery(soc, charging) {
  const s = $('bsvg'); s.replaceChildren();
  s.append(svg('rect', { x: 2, y: 2, width: 118, height: 48, rx: 8, fill: 'none', stroke: 'var(--ink)', 'stroke-width': 3 }),
           svg('rect', { x: 122, y: 16, width: 8, height: 20, rx: 2, fill: 'var(--ink)' }));
  const full = soc == null ? 0 : Math.ceil(soc / 20), low = soc != null && soc <= 20;
  for (let i = 0; i < 5; i++) {
    const r = svg('rect', { x: 8 + i * 22.4, y: 8, width: 19, height: 36, rx: 3, fill: i < full ? (low ? 'var(--bad)' : 'var(--good)') : 'var(--chip)' });
    if (charging && (i === full || (full === 5 && i === 4))) { r.setAttribute('fill', 'var(--good)'); r.classList.add('filling'); }
    s.append(r);
  }
  $('pct').textContent = soc == null ? '–' : Math.round(soc) + '%';
}
function header(c) {
  battery(c.soc, c.charging);
  const charging = c.charging, state = charging ? 'Charging' : (c.charge_state && c.charge_state !== 'done' ? c.charge_state : (c.on ? 'On' : 'Parked'));
  $('state').textContent = state; $('state').className = charging ? 'ok' : '';
  const bits = [];
  if (charging && c.charge_power_kw != null) bits.push(c.charge_power_kw.toFixed(1) + ' kW');
  if (charging && c.charge_eta_min) bits.push('full in ' + Math.round(c.charge_eta_min) + ' min');
  if (c.charge_limit_soc) bits.push('to ' + Math.round(c.charge_limit_soc) + '%');
  if (c.locked === true) bits.push('locked'); else if (c.locked === false) bits.push('unlocked');
  $('sub').textContent = bits.join(' · ');
  const r = mi(c.range_km); $('range').textContent = r == null ? '–' : Math.round(r) + ' mi';
  $('seen').textContent = 'Reported ' + ago(c.last) + (c.signal != null ? ' · signal ' + c.signal + ' dBm' : '') + (c.network ? ' · ' + c.network : '');
  const w = $('warns'); w.replaceChildren();
  (c.warnings || []).forEach(x => w.append(el('div', x.text, x.level === 'info' ? 'dim' : 'warn')));
  const tiles = [['Battery', c.soh != null ? Math.round(c.soh) + '% health' : null],
    ['12V', c.volts_12v != null ? c.volts_12v.toFixed(1) + ' V' : null],
    ['Cabin', c.cabin_temp != null ? Math.round(c.cabin_temp) + '°C' : null],
    ['Outside', c.ambient_temp != null ? Math.round(c.ambient_temp) + '°C' : null],
    ['Battery temp', c.battery_temp != null ? Math.round(c.battery_temp) + '°C' : null],
    ['Tyres (min)', c.tyres_bar != null ? c.tyres_bar + ' kPa' : null],
    ['Odometer', c.odometer_km != null ? Math.round(mi(c.odometer_km)).toLocaleString('en-GB') + ' mi' : null]];
  const tb = $('tiles'); tb.replaceChildren();
  tiles.filter(([, v]) => v != null).forEach(([k, v]) => { const d = el('div', null, 'tile'); d.append(el('div', k, 'k'), el('div', v, 'v')); tb.append(d); });
}
async function metricsTable(id) {
  const r = await api('/v1/ovms/' + encodeURIComponent(id) + '/metrics');
  const byGroup = {};
  for (const [m, info] of Object.entries(r.metrics)) { const g = groupOf(m); (byGroup[g] = byGroup[g] || []).push([m, info]); }
  const box = $('groups'); box.replaceChildren();
  const groups = Object.keys(byGroup).sort((a, b) => (GROUP_ORDER.indexOf(a) + 1 || 99) - (GROUP_ORDER.indexOf(b) + 1 || 99));
  for (const g of groups) {
    const rows = byGroup[g].sort((a, b) => prettyName(a[0]).localeCompare(prettyName(b[0])));
    const d = el('details', null, 'grp'); if (g === 'Battery' || g === 'Charging') d.open = true;
    const sm = el('summary'); sm.append(el('span', g), el('span', rows.length + '', 'dim')); d.append(sm);
    const kv = el('div', null, 'kv');
    for (const [m, info] of rows) {
      const name = el('div', prettyName(m), 'mk'); const known = !!L[m];
      if (!known) name.append(el('span', '  ' + m, 'raw'));
      const u = unitOf(m, info.value);
      kv.append(name, el('div', fmtVal(m, info.value) + (u ? ' ' + u : ''), 'mv'), el('div', ago(info.at), 'mt'));
    }
    d.append(kv); box.append(d);
  }
}
function lineChart(points, unit) {
  const s = $('chart'); s.replaceChildren();
  if (!points.length) { $('chartnote').textContent = 'No history for this metric yet.'; return; }
  const xs = points.map(p => new Date(p.at).getTime()), ys = points.map(p => Number(p.value)).filter(v => !isNaN(v));
  if (!ys.length) { $('chartnote').textContent = 'This metric is not a number.'; return; }
  const x0 = Math.min(...xs), x1 = Math.max(...xs), lo = Math.min(...ys), hi = Math.max(...ys);
  const pad = (hi - lo) * 0.08 || 1, YLO = lo - pad, YHI = hi + pad;
  const px = x => 44 + (x1 === x0 ? 0.5 : (x - x0) / (x1 - x0)) * 545, py = y => 188 - (y - YLO) / (YHI - YLO) * 176;
  s.append(svg('line', { x1: 44, y1: 188, x2: 589, y2: 188, class: 'ax' }), svg('line', { x1: 44, y1: 8, x2: 44, y2: 188, class: 'ax' }));
  const tx = (x, y, t, a) => { const e = svg('text', { x, y, 'text-anchor': a || 'start' }); e.textContent = t; s.append(e); };
  tx(4, 12, (Math.round(hi * 100) / 100) + (unit ? ' ' + unit : '')); tx(4, 186, (Math.round(lo * 100) / 100) + '');
  const d = points.filter(p => !isNaN(Number(p.value))).map((p, i) => (i ? 'L' : 'M') + px(new Date(p.at).getTime()).toFixed(1) + ' ' + py(Number(p.value)).toFixed(1)).join(' ');
  s.append(svg('path', { d, class: 'ln' }));
  const fmt = t => new Date(t).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  tx(44, 199, fmt(x0)); tx(589, 199, fmt(x1), 'end');
  $('chartnote').textContent = points.length + ' points.';
}
async function drawChart() {
  const c = cur(); if (!c) return;
  const m = $('metric').value; if (!m) return;
  try { const r = await api('/v1/ovms/' + encodeURIComponent(c.id) + '/history?metric=' + encodeURIComponent(m) + '&days=' + $('days').value);
    lineChart(r.points, L[m] ? L[m][1] : ''); } catch (e) { $('chartnote').textContent = e.message; }
}
function fillMetricPicker(id) {
  // offer chartable metrics that the car actually sends; fall back to the full chartable list
  api('/v1/ovms/' + encodeURIComponent(id) + '/metrics').then(r => {
    const have = new Set(Object.keys(r.metrics));
    const opts = CHARTABLE.filter(m => have.has(m));
    const sel = $('metric'), keep = sel.value;
    sel.replaceChildren(...(opts.length ? opts : CHARTABLE).map(m => { const o = new Option(prettyName(m), m); return o; }));
    sel.value = (keep && opts.includes(keep)) ? keep : (opts[0] || 'v.b.soc');
    drawChart();
  }).catch(() => {});
}
async function pick() {
  const c = cur(); if (!c) return;
  header(c); await metricsTable(c.id); fillMetricPicker(c.id);
}
async function load() {
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  msg.textContent = '';
  try {
    data = await api('/v1/ovms');
    const keep = $('car').value; $('car').replaceChildren(...data.cars.map(c => new Option((c.vehicle || c.id), c.id)));
    if (keep && data.cars.some(c => c.id === keep)) $('car').value = keep;
    $('car').hidden = data.cars.length < 2;
    $('none').hidden = data.cars.length > 0; $('view').hidden = !data.cars.length;
    if (data.cars.length) await pick();
  } catch (e) { msg.textContent = e.message; }
}
$('metric').onchange = $('days').onchange = () => drawChart();
document.querySelectorAll('.cbtn').forEach(b => b.onclick = () => control(b.dataset.a));
$('car').onchange = () => pick().catch(e => msg.textContent = e.message);
$('go').onclick = load;
load();
setInterval(() => { if (!document.hidden && data && data.cars.length) load(); }, 30000);
</script></main></body></html>"""


@page_router.get("/car", response_class=HTMLResponse, include_in_schema=False)
async def car_page(request: Request) -> str:
    return themed(PAGE, "car", request)
