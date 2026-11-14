"""The planner page (/planner, a tab of Energy): what World Assistant would do with the car now and why, how often
that matched Home Assistant's plan, each time they differed; and how predictable evening demand is from the cold.
The page holds no data and never builds HTML from it."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

page_router = APIRouter()

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant planner</title>
<style>
/*BASE_CSS*/
.big { font-size: 1.5em; font-weight: 650; } .big::first-letter { text-transform: uppercase; }
.pair { display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
table.t { width: 100%; border-collapse: collapse; font-size: .9em; } table.t td, table.t th { padding: 4px 6px; border-bottom: 1px solid var(--line); text-align: left; }
.scatter { width: 100%; height: 220px; } .scatter circle { fill: var(--series-1); opacity: .75; } .scatter line.fit { stroke: var(--series-2); stroke-width: 2; }
.scatter line.ax { stroke: var(--line); } .scatter text { fill: var(--dim); font-size: 11px; }
form.grid { display: grid; gap: 8px; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); } form.grid label { display: grid; gap: 3px; }
</style></head><body><main>
<!--NAV-->
<h1>Planner</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary" type="button">Load</button></div>
<p id="msg" class="err"></p>
<div class="card"><h2>Charging, in shadow</h2>
  <div class="pair"><div><div class="dim">World Assistant would</div><div class="big" id="wa">–</div><div id="why"></div></div>
  <div><div class="dim">Home Assistant's plan</div><div class="big" id="ha">–</div><div class="dim" id="agree"></div></div></div>
  <p class="dim small" id="mode"></p></div>
<div class="card"><h2>When they differed</h2><table class="t" id="diff"></table>
  <p class="dim small">Each disagreement once, from when it began. Differences are expected where Home Assistant's own rules
  differ on purpose (charging from the house battery, the rest after full); the rest show where one is wrong.</p></div>
<div class="card"><h2>How predictable are your evenings?</h2><p id="dsay">Loading…</p>
  <svg class="scatter" id="sc" viewBox="0 0 600 220" role="img" aria-label="Each evening's use against how cold it was"></svg>
  <p class="dim small">Each dot an evening: across, how cold (heating degree-hours below 15.5 °C); up, the kWh the house used.
  The line is the best straight fit. R² is how much of the difference between evenings the cold explains.</p>
  <p id="tomorrow"></p></div>
<details class="card"><summary>Settings</summary>
<form class="grid" id="f">
  <label>House use statistic (kWh) <select id="demand_statistic"></select></label>
  <label>Days of history <input id="demand_days" type="number" min="14" max="365"></label>
  <label>Evening from (hour) <input id="evening_from" type="number" min="0" max="23"></label>
  <label>Evening to (hour) <input id="evening_to" type="number" min="1" max="24"></label>
  <label>House battery charge entity <input id="battery_entity" placeholder="sensor.office_inverter_battery_soc"></label>
  <label>Grid power entity (W) <input id="grid_entity" placeholder="sensor.inverter_grid_power"></label>
  <label><span><input id="export_is_negative" type="checkbox"> Export is a negative figure</span></label>
  <label>Solar: start at export of (W) <input id="solar_start_w" type="number" min="500" max="20000"></label>
  <label>Solar: keep going while spare is (W) <input id="solar_stop_w" type="number" min="0" max="20000"></label>
  <label><span><input id="act" type="checkbox"> Let World Assistant start and stop the charger (Auto only; Home Assistant's start and stop then stand aside)</span></label>
</form>
<p><button id="save" type="button">Save</button> <span id="saved" class="dim"></span></p></details>
<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
async function api(path, opts) {
  const r = await fetch(path, { ...(opts || {}), headers: { Authorization: 'Bearer ' + tok.value, 'Content-Type': 'application/json' } });
  if (!r.ok) { let d = r.status; try { d = (await r.json()).detail; } catch (e) {} throw new Error(typeof d === 'string' ? d : JSON.stringify(d)); }
  return r.json();
}
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const when = s => new Date(s).toLocaleString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
const NS = 'http://www.w3.org/2000/svg', svg = (tag, at) => { const e = document.createElementNS(NS, tag); for (const k in at) e.setAttribute(k, at[k]); return e; };
const FIELDS = ['demand_statistic', 'demand_days', 'evening_from', 'evening_to', 'battery_entity', 'grid_entity', 'export_is_negative', 'solar_start_w', 'solar_stop_w', 'act'];
function scatter(days, fit) {
  const s = $('sc'); s.replaceChildren(); if (!days.length) return;
  const xs = days.map(d => d.evening_degree_hours), ys = days.map(d => d.evening_kwh);
  const X = Math.max(...xs, 1), Y = Math.max(...ys, 1), px = x => 40 + x / X * 545, py = y => 200 - y / Y * 185;
  s.append(svg('line', { x1: 40, y1: 200, x2: 585, y2: 200, class: 'ax' }), svg('line', { x1: 40, y1: 15, x2: 40, y2: 200, class: 'ax' }));
  const tx = (x, y, t, a) => { const e = svg('text', { x, y, 'text-anchor': a || 'start' }); e.textContent = t; s.append(e); };
  tx(585, 215, Math.round(X) + ' degree-hours', 'end'); tx(4, 14, Y.toFixed(1) + ' kWh');
  if (fit && fit.enough) s.append(svg('line', { x1: px(0), y1: py(Math.max(0, fit.base_kwh)), x2: px(X), y2: py(Math.max(0, fit.base_kwh + fit.kwh_per_degree_hour * X)), class: 'fit' }));
  days.forEach(d => { const c = svg('circle', { cx: px(d.evening_degree_hours), cy: py(d.evening_kwh), r: 4 });
    const ti = svg('title', {}); ti.textContent = d.day + ': ' + d.evening_kwh + ' kWh, ' + d.evening_degree_hours + ' degree-hours'; c.append(ti); s.append(c); });
}
async function load() {
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  msg.textContent = '';
  try {
    const [c, st] = await Promise.all([api('/v1/planner/charging?days=7'), api('/v1/planner/settings')]);
    const l = c.latest;
    $('wa').textContent = l ? l.plan : 'no decision yet'; $('why').textContent = l ? l.why + ' (' + when(l.at) + ')' : 'The first comes within five minutes of the worker starting.';
    $('ha').textContent = l && l.ha_plan ? l.ha_plan : '–';
    $('agree').textContent = c.agreed_pct == null ? '' : 'Agreed ' + c.agreed_pct + '% of ' + c.checks + ' checks in the last 7 days';
    $('mode').textContent = (c.act ? 'World Assistant is acting on this (in Auto).' : 'Shadow only: World Assistant decides but does not act.') + (l && l.acted ? ' Last: ' + l.acted + '.' : '');
    const t = $('diff'); t.replaceChildren();
    if (!c.differences.length) { const r = el('tr'); r.append(el('td', 'No differences in the last 7 days.')); t.append(r); }
    else { const h = el('tr'); ['From', 'World Assistant', 'Home Assistant', 'Why World Assistant'].forEach(x => h.append(el('th', x))); t.append(h);
      c.differences.slice().reverse().forEach(d => { const r = el('tr'); [when(d.at), d.planner, d.home_assistant, d.why].forEach(x => r.append(el('td', x))); t.append(r); }); }
    FIELDS.forEach(k => { const e = $(k); if (!e || k === 'demand_statistic') return; if (e.type === 'checkbox') e.checked = !!st[k]; else e.value = st[k] ?? ''; });
    const d = await api('/v1/planner/demand'), sel = $('demand_statistic');
    sel.replaceChildren(el('option', '(choose)')); sel.firstChild.value = '';
    (d.choices || []).forEach(o => { const e = el('option', o.name + ' (' + o.id + ')'); e.value = o.id; sel.append(e); });
    sel.value = d.statistic || st.demand_statistic || '';
    $('sc').hidden = !!d.error; if (d.error) { $('dsay').textContent = d.error; return; }
    const f = d.fit;
    $('dsay').textContent = !f.enough ? f.why : 'Evenings ' + d.window + ' are ' + f.verdict + ' from the cold (R² ' + f.r2 + ', over ' + f.days +
      ' evenings): about ' + f.base_kwh + ' kWh plus ' + f.kwh_per_degree_hour + ' kWh per degree-hour, give or take ' + f.spread_kwh + ' kWh.';
    scatter(d.days || [], f);
    $('tomorrow').textContent = d.tomorrow ? 'Tomorrow evening (' + d.tomorrow.degree_hours + ' degree-hours): likely ' + d.tomorrow.likely_kwh +
      ' kWh; 9 evenings in 10 under ' + d.tomorrow.nine_in_ten_kwh + ' kWh, and at least ' + d.tomorrow.at_least_kwh + ' kWh.' : '';
  } catch (e) { msg.textContent = e.message; }
}
$('save').onclick = async () => {
  const body = {}; FIELDS.forEach(k => { const e = $(k); body[k] = e.type === 'checkbox' ? e.checked : e.type === 'number' ? Number(e.value) : e.value.trim(); });
  try { await api('/v1/planner/settings', { method: 'PUT', body: JSON.stringify(body) }); $('saved').textContent = 'Saved'; load(); }
  catch (e) { $('saved').textContent = e.message; }
};
$('go').onclick = load;
load();
</script></main></body></html>"""


@page_router.get("/planner", response_class=HTMLResponse, include_in_schema=False)
async def planner_page(request: Request) -> str:
    return themed(PAGE, "planner", request)
