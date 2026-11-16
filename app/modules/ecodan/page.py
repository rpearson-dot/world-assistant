"""The Ecodan page (/ecodan, Energy → Heat pump): each heat pump's tank temperature and target, the hot-water boost,
flow/return and outdoor temperatures, the heating zone, power and daily energy, with a history chart. Read-only; holds
no data, calls /v1/ecodan."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

page_router = APIRouter()

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant heat pump</title>
<style>
/*BASE_CSS*/
.t { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 18px; }
.t .big { font-size: 2.2em; font-weight: 650; font-variant-numeric: tabular-nums; }
.t .set { color: var(--dim); } .boost { color: var(--series-1); font-weight: 650; }
.tiles { display: grid; gap: 10px; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); margin-top: 8px; }
.tile { border: 1px solid var(--line); border-radius: 12px; padding: 8px 10px; } .tile .k { font-size: 12px; color: var(--dim); }
.tile .v { font-size: 1.2em; font-weight: 650; font-variant-numeric: tabular-nums; }
.chart { width: 100%; height: 200px; } .chart path.ln { fill: none; stroke-width: 2; }
.chart path.tank { stroke: var(--series-1); } .chart path.tgt { stroke: var(--series-1); stroke-dasharray: 4 3; opacity: .6; }
.chart path.flow { stroke: var(--series-2); } .chart path.ret { stroke: var(--series-3); }
.chart line.ax { stroke: var(--line); } .chart rect.b { fill: var(--series-1); opacity: .12; } .chart text { fill: var(--dim); font-size: 11px; }
.seg select { min-width: 0; } .leg span { margin-right: 14px; } .leg i { display: inline-block; width: 14px; height: 3px; vertical-align: middle; margin-right: 4px; }
</style></head><body><main>
<!--NAV-->
<h1>Heat pump</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary" type="button">Load</button></div>
<p id="msg" class="err"></p>

<div class="card" id="none" hidden><div class="say">The heat pump isn't connected yet.</div>
  <p class="dim">Add your MELCloud login on the <a href="/keys">Keys page</a>: <code>melcloud_email</code> and
  <code>melcloud_password</code>. World Assistant reads the Ecodan every ten minutes (MELCloud limits how often it can
  be asked). Tracking only — it does not control the heat pump.</p></div>

<section id="view" hidden>
<div id="cards"></div>
<div class="card"><h2>History</h2>
  <div class="row seg"><label>Heat pump <select id="dev"></select></label>
    <label>Over <select id="days"><option value="1">a day</option><option value="7" selected>a week</option>
      <option value="30">a month</option></select></label></div>
  <p class="leg dim small"><span><i style="background:var(--series-1)"></i>tank</span><span><i style="background:var(--series-2)"></i>flow</span><span><i style="background:var(--series-3)"></i>return</span><span><i class="b" style="background:var(--series-1);opacity:.3"></i> hot-water boost</span></p>
  <svg class="chart" id="chart" viewBox="0 0 600 200" role="img" aria-label="Tank, flow and return temperatures over time"></svg>
  <p class="dim small" id="chartnote"></p></div>
</section>

<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
async function api(path) {
  const r = await fetch(path, { headers: { Authorization: 'Bearer ' + tok.value } });
  if (!r.ok) { let d = r.status; try { d = (await r.json()).detail; } catch (e) {} throw new Error(typeof d === 'string' ? d : JSON.stringify(d)); }
  return r.json();
}
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const ago = s => { if (!s) return 'never'; const m = Math.round((Date.now() - new Date(s)) / 60000); return m < 1 ? 'just now' : m < 90 ? m + ' min ago' : m < 1440 ? Math.round(m / 60) + ' h ago' : Math.round(m / 1440) + ' d ago'; };
const NS = 'http://www.w3.org/2000/svg', svg = (t, a) => { const e = document.createElementNS(NS, t); for (const k in a) e.setAttribute(k, a[k]); return e; };
const c1 = v => v == null ? null : v.toFixed(1) + '°C';

let data = null;
const cur = () => data && data.units.find(u => u.device === $('dev').value);
function cards() {
  const box = $('cards'); box.replaceChildren();
  for (const u of data.units) {
    const c = el('div', null, 'card');
    const head = el('div', null, 't');
    head.append(el('b', u.name));
    if (u.tank_c != null) head.append(el('span', u.tank_c.toFixed(1) + '°C', 'big'), el('span', 'tank', 'set'));
    if (u.forced_hot_water) head.append(el('span', 'heating water now', 'boost'));
    if (u.tank_target_c != null) head.append(el('span', 'target ' + u.tank_target_c.toFixed(0) + '°C', 'set'));
    c.append(head);
    const kwh = v => v != null ? v.toFixed(1) + ' kWh' : null;
    const cop = (out, inp) => (out != null && inp) ? (out / inp).toFixed(1) : null;
    const tiles = [['Flow', c1(u.flow_c)], ['Return', c1(u.return_c)], ['Outside', c1(u.outdoor_c)],
      ['Room (zone 1)', c1(u.zone1_room_c)], ['Power', u.power ? 'On' : 'Off'],
      ['Hot water today', kwh(u.water_in_kwh)], ['Heating today', kwh(u.heating_in_kwh)],
      ['Hot-water CoP', cop(u.water_out_kwh, u.water_in_kwh)], ['Status', u.offline ? 'Offline' : 'Online']];
    const tb = el('div', null, 'tiles');
    tiles.filter(([, v]) => v != null).forEach(([k, v]) => { const d = el('div', null, 'tile'); d.append(el('div', k, 'k'), el('div', v, 'v')); tb.append(d); });
    c.append(tb);
    c.append(el('p', 'Reported ' + ago(u.at), 'dim small'));
    box.append(c);
  }
}
function chart(points) {
  const s = $('chart'); s.replaceChildren();
  const have = points.filter(p => p.tank_c != null);
  if (!have.length) { $('chartnote').textContent = 'No history yet.'; return; }
  const xs = points.map(p => new Date(p.at).getTime());
  const ys = points.flatMap(p => [p.tank_c, p.tank_target_c, p.flow_c, p.return_c].filter(v => v != null));
  const x0 = Math.min(...xs), x1 = Math.max(...xs), lo = Math.min(...ys), hi = Math.max(...ys);
  const pad = (hi - lo) * 0.1 || 1, YLO = lo - pad, YHI = hi + pad;
  const px = x => 40 + (x1 === x0 ? 0.5 : (x - x0) / (x1 - x0)) * 549, py = y => 188 - (y - YLO) / (YHI - YLO) * 176;
  for (let i = 0; i < points.length - 1; i++) {
    if (points[i].forced_hot_water) {
      const a = px(new Date(points[i].at).getTime()), b = px(new Date(points[i + 1].at).getTime());
      s.append(svg('rect', { x: a, y: 8, width: Math.max(1, b - a), height: 180, class: 'b' }));
    }
  }
  s.append(svg('line', { x1: 40, y1: 188, x2: 589, y2: 188, class: 'ax' }));
  const line = (field, cls) => {
    const pts = points.filter(p => p[field] != null);
    if (pts.length < 2) return;
    s.append(svg('path', { d: pts.map((p, i) => (i ? 'L' : 'M') + px(new Date(p.at).getTime()).toFixed(1) + ' ' + py(p[field]).toFixed(1)).join(' '), class: 'ln ' + cls }));
  };
  line('tank_c', 'tank'); line('tank_target_c', 'tgt'); line('flow_c', 'flow'); line('return_c', 'ret');
  const tx = (x, y, t, a) => { const e = svg('text', { x, y, 'text-anchor': a || 'start' }); e.textContent = t; s.append(e); };
  tx(2, 12, Math.round(hi) + '°'); tx(2, 186, Math.round(lo) + '°');
  const fmt = t => new Date(t).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  tx(40, 199, fmt(x0)); tx(589, 199, fmt(x1), 'end');
  $('chartnote').textContent = points.length + ' readings.';
}
async function drawChart() {
  const u = cur(); if (!u) return;
  try { const r = await api('/v1/ecodan/history?device=' + encodeURIComponent(u.device) + '&days=' + $('days').value); chart(r.points); }
  catch (e) { $('chartnote').textContent = e.message; }
}
async function load() {
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  msg.textContent = '';
  try {
    data = await api('/v1/ecodan');
    const have = data.units.length > 0;
    $('none').hidden = have || data.configured; $('view').hidden = !have;
    if (!have) { if (data.configured) msg.textContent = 'Connected, but no heat pump has reported yet.'; return; }
    const keep = $('dev').value;
    $('dev').replaceChildren(...data.units.map(u => new Option(u.name, u.device)));
    if (keep && data.units.some(u => u.device === keep)) $('dev').value = keep;
    cards(); await drawChart();
  } catch (e) { msg.textContent = e.message; }
}
$('dev').onchange = $('days').onchange = () => drawChart();
$('go').onclick = load;
load();
setInterval(() => { if (!document.hidden && data) load(); }, 60000);
</script></main></body></html>"""


@page_router.get("/ecodan", response_class=HTMLResponse, include_in_schema=False)
async def ecodan_page(request: Request) -> str:
    return themed(PAGE, "ecodan", request)
