"""The Nest page (/nest, In the home → Heating): each thermostat's room temperature, target, humidity, mode and whether
it is calling for heat, with a history chart. Read-only; holds no data, calls /v1/nest."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

page_router = APIRouter()

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant heating</title>
<style>
/*BASE_CSS*/
.t { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 18px; }
.t .big { font-size: 2.2em; font-weight: 650; font-variant-numeric: tabular-nums; }
.t .set { color: var(--dim); } .heat { color: var(--bad); font-weight: 650; }
.tiles { display: grid; gap: 10px; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); margin-top: 8px; }
.tile { border: 1px solid var(--line); border-radius: 12px; padding: 8px 10px; } .tile .k { font-size: 12px; color: var(--dim); }
.tile .v { font-size: 1.2em; font-weight: 650; font-variant-numeric: tabular-nums; }
.chart { width: 100%; height: 200px; } .chart path.ln { fill: none; stroke-width: 2; }
.chart path.a { stroke: var(--series-1); } .chart path.s { stroke: var(--series-2); stroke-dasharray: 4 3; }
.chart line.ax { stroke: var(--line); } .chart rect.h { fill: var(--bad); opacity: .12; } .chart text { fill: var(--dim); font-size: 11px; }
.seg select { min-width: 0; } .leg span { margin-right: 14px; } .leg i { display: inline-block; width: 14px; height: 3px; vertical-align: middle; margin-right: 4px; }
</style></head><body><main>
<!--NAV-->
<h1>Heating</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary" type="button">Load</button></div>
<p id="msg" class="err"></p>

<div class="card" id="none" hidden><div class="say">The Nest isn't connected yet.</div>
  <p class="dim">Add the Google Smart Device Management keys on the <a href="/keys">Keys page</a>:
  <code>nest_project_id</code>, <code>nest_client_id</code>, <code>nest_client_secret</code>, <code>nest_refresh_token</code>.
  They come from the one-time Google Device Access setup.</p></div>

<section id="view" hidden>
<div id="cards"></div>
<div class="card"><h2>History</h2>
  <div class="row seg"><label>Thermostat <select id="dev"></select></label>
    <label>Over <select id="days"><option value="1">a day</option><option value="7" selected>a week</option>
      <option value="30">a month</option></select></label></div>
  <p class="leg dim small"><span><i style="background:var(--series-1)"></i>room</span><span><i style="background:var(--series-2)"></i>target</span><span><i class="h" style="background:var(--bad);opacity:.3"></i> heating</span></p>
  <svg class="chart" id="chart" viewBox="0 0 600 200" role="img" aria-label="Room temperature and target over time"></svg>
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
const mode = m => ({ HEAT: 'Heat', COOL: 'Cool', HEATCOOL: 'Heat/Cool', OFF: 'Off' }[m] || m || '–');

let data = null;
const cur = () => data && data.thermostats.find(t => t.device === $('dev').value);
function cards() {
  const box = $('cards'); box.replaceChildren();
  for (const t of data.thermostats) {
    const c = el('div', null, 'card');
    const head = el('div', null, 't');
    head.append(el('b', t.name));
    if (t.ambient_c != null) head.append(el('span', t.ambient_c.toFixed(1) + '°C', 'big'));
    if (t.hvac === 'HEATING') head.append(el('span', 'heating now', 'heat'));
    if (t.setpoint_c != null) head.append(el('span', 'target ' + t.setpoint_c.toFixed(1) + '°C', 'set'));
    c.append(head);
    const tiles = [['Mode', mode(t.mode)], ['Humidity', t.humidity != null ? Math.round(t.humidity) + '%' : null],
      ['Eco', t.eco && t.eco !== 'OFF' ? 'On' : 'Off'], ['Status', t.online ? 'Online' : 'Offline']];
    const tb = el('div', null, 'tiles');
    tiles.filter(([, v]) => v != null).forEach(([k, v]) => { const d = el('div', null, 'tile'); d.append(el('div', k, 'k'), el('div', v, 'v')); tb.append(d); });
    c.append(tb);
    c.append(el('p', 'Reported ' + ago(t.at), 'dim small'));
    box.append(c);
  }
}
function chart(points) {
  const s = $('chart'); s.replaceChildren();
  const temps = points.filter(p => p.ambient_c != null);
  if (!temps.length) { $('chartnote').textContent = 'No history yet.'; return; }
  const xs = points.map(p => new Date(p.at).getTime());
  const ys = points.flatMap(p => [p.ambient_c, p.setpoint_c].filter(v => v != null));
  const x0 = Math.min(...xs), x1 = Math.max(...xs), lo = Math.min(...ys), hi = Math.max(...ys);
  const pad = (hi - lo) * 0.1 || 1, YLO = lo - pad, YHI = hi + pad;
  const px = x => 40 + (x1 === x0 ? 0.5 : (x - x0) / (x1 - x0)) * 549, py = y => 188 - (y - YLO) / (YHI - YLO) * 176;
  // heating shading
  for (let i = 0; i < points.length - 1; i++) {
    if (points[i].hvac === 'HEATING') {
      const a = px(new Date(points[i].at).getTime()), b = px(new Date(points[i + 1].at).getTime());
      s.append(svg('rect', { x: a, y: 8, width: Math.max(1, b - a), height: 180, class: 'h' }));
    }
  }
  s.append(svg('line', { x1: 40, y1: 188, x2: 589, y2: 188, class: 'ax' }));
  const line = (field, cls) => {
    const pts = points.filter(p => p[field] != null);
    if (pts.length < 2) return;
    s.append(svg('path', { d: pts.map((p, i) => (i ? 'L' : 'M') + px(new Date(p.at).getTime()).toFixed(1) + ' ' + py(p[field]).toFixed(1)).join(' '), class: 'ln ' + cls }));
  };
  line('ambient_c', 'a'); line('setpoint_c', 's');
  const tx = (x, y, t, a) => { const e = svg('text', { x, y, 'text-anchor': a || 'start' }); e.textContent = t; s.append(e); };
  tx(2, 12, Math.round(hi) + '°'); tx(2, 186, Math.round(lo) + '°');
  const fmt = t => new Date(t).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  tx(40, 199, fmt(x0)); tx(589, 199, fmt(x1), 'end');
  $('chartnote').textContent = points.length + ' readings.';
}
async function drawChart() {
  const c = cur(); if (!c) return;
  try { const r = await api('/v1/nest/history?device=' + encodeURIComponent(c.device) + '&days=' + $('days').value); chart(r.points); }
  catch (e) { $('chartnote').textContent = e.message; }
}
async function load() {
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  msg.textContent = '';
  try {
    data = await api('/v1/nest');
    const have = data.thermostats.length > 0;
    $('none').hidden = have || data.configured; $('view').hidden = !have;
    if (!have) { if (data.configured) msg.textContent = 'Connected, but no thermostat has reported yet.'; return; }
    const keep = $('dev').value;
    $('dev').replaceChildren(...data.thermostats.map(t => new Option(t.name, t.device)));
    if (keep && data.thermostats.some(t => t.device === keep)) $('dev').value = keep;
    cards(); await drawChart();
  } catch (e) { msg.textContent = e.message; }
}
$('dev').onchange = $('days').onchange = () => drawChart();
$('go').onclick = load;
load();
setInterval(() => { if (!document.hidden && data) load(); }, 60000);
</script></main></body></html>"""


@page_router.get("/nest", response_class=HTMLResponse, include_in_schema=False)
async def nest_page(request: Request) -> str:
    return themed(PAGE, "nest", request)
