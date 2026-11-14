"""The solar forecast page (/solar, a tab of Energy): today's and tomorrow's expected output hour by hour, how far
off the forecast has been, and the arrays. The page holds no data and never builds HTML from it."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

page_router = APIRouter()

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant solar</title>
<style>
/*BASE_CSS*/
.tiles { display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
.tile b { display: block; font-size: 1.6em; } .tile span { color: var(--dim); font-size: .9em; }
.bars { width: 100%; height: 180px; } .bars rect.t { fill: var(--series-1); } .bars rect.m { fill: var(--series-2); }
.bars text { fill: var(--dim); font-size: 10px; } .bars line { stroke: var(--line); }
table.t { width: 100%; border-collapse: collapse; font-size: .9em; } table.t td, table.t th { padding: 4px 6px; border-bottom: 1px solid var(--line); text-align: left; }
table.t td.n, table.t th.n { text-align: right; } #planes input { width: 100%; min-width: 0; }
</style></head><body><main>
<!--NAV-->
<h1>Solar</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary" type="button">Load</button> <button id="again" type="button">Fetch now</button></div>
<p id="msg" class="err"></p>
<div class="card"><div class="tiles">
  <div class="tile"><span>Expected today</span><b id="today">–</b></div>
  <div class="tile"><span>Still to come today</span><b id="left">–</b></div>
  <div class="tile"><span>Expected tomorrow</span><b id="tomorrow">–</b></div>
  <div class="tile"><span>Power expected now</span><b id="now">–</b></div></div>
  <p class="dim small" id="when"></p></div>
<div class="card"><h2>Hour by hour</h2>
  <svg class="bars" id="bars" viewBox="0 0 600 180" role="img" aria-label="Expected kWh each hour, today then tomorrow"></svg>
  <p class="dim small">Expected kWh in each hour: today, then tomorrow. From forecast.solar, refreshed every hour.</p></div>
<div class="card"><h2>How far off it has been</h2><p id="accsay" class="dim">Loading…</p><table class="t" id="acc"></table></div>
<details class="card" id="setbox"><summary>Your arrays</summary>
  <p class="dim small">One row for each set of panels facing the same way at the same tilt. Direction is a compass bearing:
  180 south, 90 east, 270 west. Tilt is degrees from flat. Size is the array's peak kW (kWp).</p>
  <table class="t" id="planes"><tr><th>Name</th><th>Tilt</th><th>Direction</th><th>kWp</th><th></th></tr></table>
  <p><button id="add" type="button">Add an array</button> <button id="fromha" type="button">Copy from Home Assistant</button></p>
  <label>Home Assistant statistic for what the panels make <select id="stat"></select></label>
  <p><button id="save" class="primary" type="button">Save</button> <span id="smsg" class="dim"></span></p></details>
<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
async function api(path, opts) {
  const r = await fetch(path, { ...(opts || {}), headers: { Authorization: 'Bearer ' + tok.value, 'Content-Type': 'application/json' } });
  if (!r.ok) { let d = r.status; try { d = (await r.json()).detail; } catch (e) {} const e = new Error(typeof d === 'string' ? d : JSON.stringify(d)); e.status = r.status; throw e; }
  return r.json();
}
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const NS = 'http://www.w3.org/2000/svg', svg = (tag, at) => { const e = document.createElementNS(NS, tag); for (const k in at) e.setAttribute(k, at[k]); return e; };
const kwh = v => v == null ? '–' : v.toFixed(1) + ' kWh';
function bars(a, b) {
  const s = $('bars'); s.replaceChildren(); const all = [...a, ...b], max = Math.max(...all, 0.1), w = 600 / 48;
  s.append(svg('line', { x1: 0, y1: 160, x2: 600, y2: 160 }), svg('line', { x1: 300, y1: 10, x2: 300, y2: 170 }));
  all.forEach((v, i) => { const h = v / max * 145; const r = svg('rect', { x: i * w + 1, y: 160 - h, width: w - 2, height: h, class: i < 24 ? 't' : 'm' });
    const ti = svg('title', {}); ti.textContent = (i < 24 ? 'Today ' : 'Tomorrow ') + String(i % 24).padStart(2, '0') + ':00 ' + v.toFixed(2) + ' kWh'; r.append(ti); s.append(r); });
  for (const [x, t] of [[4, 'Today'], [304, 'Tomorrow']]) { const e = svg('text', { x, y: 176 }); e.textContent = t; s.append(e); }
  const top = svg('text', { x: 4, y: 12 }); top.textContent = max.toFixed(2) + ' kWh'; s.append(top);
}
function planeRow(p) {
  const tr = el('tr');
  for (const [k, ph, type] of [['name', 'e.g. South roof', 'text'], ['declination', '35', 'number'], ['azimuth', '180', 'number'], ['kwp', '4.2', 'number']]) {
    const td = el('td'), i = el('input'); i.type = type; i.step = 'any'; i.value = p[k] ?? ''; i.placeholder = ph; i.dataset.k = k; td.append(i); tr.append(td);
  }
  const td = el('td'), b = el('button', 'Remove'); b.type = 'button'; b.onclick = () => tr.remove(); td.append(b); tr.append(td);
  $('planes').append(tr);
}
function setPlanes(list) { [...$('planes').querySelectorAll('tr')].slice(1).forEach(r => r.remove()); list.forEach(planeRow); }
async function load() {
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  msg.textContent = '';
  try {
    const st = await api('/v1/solar/settings'); setPlanes(st.planes);
    if (!st.planes.length) { $('setbox').open = true; $('when').textContent = 'Add your arrays below (or copy them from Home Assistant) to get a forecast.'; }
    else {
      try {
        const d = await api('/v1/solar');
        $('today').textContent = kwh(d.today_kwh); $('left').textContent = kwh(d.remaining_today_kwh); $('tomorrow').textContent = kwh(d.tomorrow_kwh);
        $('now').textContent = d.power_now_w == null ? '–' : (d.power_now_w / 1000).toFixed(2) + ' kW';
        $('when').textContent = d.fetched_at ? 'Forecast from ' + new Date(d.fetched_at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) + '.' : 'Not fetched yet: within the hour, or press Fetch now.';
        bars(d.today_hours, d.tomorrow_hours);
      } catch (e) { msg.textContent = e.message; }
    }
    const a = await api('/v1/solar/accuracy'), sel = $('stat');
    sel.replaceChildren(el('option', '(choose)')); sel.firstChild.value = '';
    (a.choices || []).forEach(o => { const x = el('option', o.name + ' (' + o.id + ')'); x.value = o.id; sel.append(x); });
    sel.value = a.statistic || st.pv_statistic || '';
    const t = $('acc'); t.replaceChildren();
    if (a.error) $('accsay').textContent = a.error;
    else if (!a.days.length) $('accsay').textContent = 'Nothing to compare yet: each day is forecast the evening before, so the first comparison is the day after tomorrow.';
    else {
      $('accsay').textContent = a.typical_miss_kwh == null ? 'No day with both a forecast and a reading yet.' : 'Typically ' + a.typical_miss_kwh + ' kWh off the evening before.';
      const h = el('tr'); [['Day'], ['Forecast', 'n'], ['Made', 'n'], ['Off by', 'n']].forEach(([x, c]) => h.append(el('th', x, c))); t.append(h);
      a.days.slice().reverse().forEach(d => { const r = el('tr'); r.append(el('td', new Date(d.day).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })),
        el('td', kwh(d.forecast_kwh), 'n'), el('td', kwh(d.made_kwh), 'n'), el('td', d.forecast_kwh != null && d.made_kwh != null ? (d.forecast_kwh - d.made_kwh).toFixed(1) : '–', 'n')); t.append(r); });
    }
  } catch (e) { msg.textContent = e.message; }
}
$('add').onclick = () => planeRow({});
$('fromha').onclick = async () => { $('smsg').textContent = 'Reading Home Assistant…';
  try { const r = await api('/v1/solar/from-home-assistant', { method: 'POST' }); setPlanes(r.planes); $('smsg').textContent = 'Copied ' + r.planes.length + ': check them, then Save.'; }
  catch (e) { $('smsg').textContent = e.message; } };
$('save').onclick = async () => {
  const planes = [...$('planes').querySelectorAll('tr')].slice(1).map(tr => Object.fromEntries([...tr.querySelectorAll('input')].map(i => [i.dataset.k, i.type === 'number' ? Number(i.value) : i.value.trim()]))).filter(p => p.name);
  try { await api('/v1/solar/settings', { method: 'PUT', body: JSON.stringify({ planes, pv_statistic: $('stat').value }) }); $('smsg').textContent = 'Saved. Press Fetch now for a forecast straight away.'; load(); }
  catch (e) { $('smsg').textContent = e.message; }
};
$('again').onclick = async () => { msg.textContent = '';
  try { const r = await api('/v1/solar/refresh', { method: 'POST' }); if (r.not_set_up) msg.textContent = r.not_set_up; else if (r.errors) msg.textContent = r.errors.join('; '); load(); }
  catch (e) { msg.textContent = e.message; } };
$('go').onclick = load;
load();
</script></main></body></html>"""


@page_router.get("/solar", response_class=HTMLResponse, include_in_schema=False)
async def solar_page(request: Request) -> str:
    return themed(PAGE, "solar", request)
