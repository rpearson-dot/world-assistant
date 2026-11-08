"""The Environment page: air quality, pollen, floods and rivers, tides and storm overflows for each of your places, a
tab each (Pollen is its own page, /pollen, under the same tabs).

    /environment             air quality: Defra's 1-10 index and each pollutant
    /environment/floods      flood warnings and alerts, river levels and rain
    /environment/tides       the nearest tide gauge
    /environment/overflows   storm overflows near the place and on the rivers you watch

The page holds no data itself; it calls /v1/environment, /v1/floods and /v1/places.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

page_router = APIRouter(include_in_schema=False)

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant environment</title>
<style>
/*BASE_CSS*/
.big { font-size: 44px; font-weight: 800; line-height: 1; }
.band { display: inline-block; padding: 2px 10px; border-radius: var(--pill); font-weight: 650; }
.band.low { background: var(--good-bg); color: var(--good); } .band.mod { background: var(--warn-bg); color: var(--warn); }
.band.high { background: var(--bad-bg); color: var(--bad); } .band.vhigh { background: var(--bad); color: var(--card); }
.daqi { display: flex; gap: 2px; margin: 8px 0; } .daqi i { flex: 1; height: 8px; border-radius: 2px; background: var(--chip); }
.daqi i.on { background: var(--c); }
table.env { width: 100%; border-collapse: collapse; font-size: 14px; } table.env td, table.env th { padding: 6px 4px; border-top: 1px solid var(--line); text-align: left; }
.warn { border-left: 4px solid var(--warn); padding: 6px 10px; margin: 8px 0; background: var(--warn-bg); border-radius: var(--radius-sm); }
.warn.sev { border-color: var(--bad); background: var(--bad-bg); }
.top { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.src { font-size: 12px; color: var(--dim); margin-top: 8px; }
.trend { font-weight: 700; }
</style></head><body><main>
<!--NAV-->
<div class="top"><h1 id="h">Environment</h1><label>Place <select id="place"></select></label></div>
<form id="auth" class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button class="primary">Load</button></form>
<p id="msg" class="err"></p>
<div id="out"></div>
<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
async function api(path) {
  const r = await fetch(path, { headers: { Authorization: 'Bearer ' + tok.value } });
  let d = null; try { d = await r.json(); } catch (e) {}
  if (!r.ok) { let m = d && d.detail; if (Array.isArray(m)) m = m.map(x => x.msg).join('; '); throw new Error(m || ('HTTP ' + r.status)); }
  return d;
}
const TAB = (location.pathname.split('/')[2] || 'air');
const TITLES = { air: 'Air quality', floods: 'Floods and rivers', tides: 'Tides', overflows: 'Storm overflows' };
const P = () => '?place=' + encodeURIComponent($('place').value);
const when = iso => iso ? new Date(iso).toLocaleString('en-GB', { weekday: 'short', hour: '2-digit', minute: '2-digit' }) : '';
const ARROW = { rising: '↑ rising', falling: '↓ falling', steady: '→ steady' };
function card(title) { const c = el('div', 'card'); if (title) c.append(el('h2', null, title)); $('out').append(c); return c; }
function bandClass(i) { return i <= 3 ? 'low' : i <= 6 ? 'mod' : i <= 9 ? 'high' : 'vhigh'; }
function failed(c, e) { c.append(el('p', 'err', e.message)); }

async function air() {
  const c = card(null);
  try {
    const d = await api('/v1/environment/air' + P());
    if (d.index == null) { c.append(el('p', 'dim', d.note || 'No recent readings near here.')); return; }
    const top = el('div', 'top'); top.append(el('span', 'big', d.index), el('span', 'band ' + bandClass(d.index), d.band), el('span', 'dim', 'Daily Air Quality Index, worst: ' + d.worst));
    const bar = el('div', 'daqi'); for (let i = 1; i <= 10; i++) { const x = el('i', i <= d.index ? 'on' : ''); x.style.setProperty('--c', i <= 3 ? 'var(--good)' : i <= 6 ? 'var(--warn)' : 'var(--bad)'); bar.append(x); }
    c.append(top, bar, el('p', 'dim small', d.index >= 7 ? 'High: people with lung or heart conditions, and older people, should cut down on strenuous activity outdoors.'
      : d.index >= 4 ? 'Moderate: if you have a lung or heart condition and feel it, cut down on strenuous activity outdoors.' : 'Low: enjoy your usual outdoor activities.'));
    const t = el('table', 'env'); t.innerHTML = '<tr><th>Pollutant</th><th>Mean</th><th>Index</th><th>Station</th></tr>';
    for (const r of Object.values(d.readings)) { const tr = el('tr');
      for (const v of [r.pollutant, r.mean + ' ' + r.unit + (r.mean_hours > 1 ? ' (' + r.mean_hours + ' h)' : ''), r.index, r.station + ' (' + r.station_km + ' km)']) tr.append(el('td', null, v)); t.append(tr); }
    c.append(t, el('div', 'src', d.source));
  } catch (e) { failed(c, e); }
}
async function floods() {
  const w = card('Flood warnings');
  try {
    const d = await api('/v1/floods/at' + P() + '&within_km=5');
    if (!d.warnings.length) w.append(el('p', 'dim', 'No flood warnings or alerts within 5 km.'));
    for (const x of d.warnings) { const b = el('div', 'warn' + (x.level <= 2 ? ' sev' : '')); b.append(el('b', null, x.severity + ': ' + x.area), el('div', 'small', (x.message || '').slice(0, 400)),
      el('div', 'dim small', 'Raised ' + when(x.raised_at))); w.append(b); }
  } catch (e) { failed(w, e); }
  const r = card('Rivers');
  try {
    const d = await api('/v1/environment/rivers' + P() + '&km=8');
    if (!d.rivers.length) r.append(el('p', 'dim', 'No river level gauge within 8 km.'));
    const t = el('table', 'env'); t.innerHTML = '<tr><th>River</th><th>Level</th><th>Usual</th><th>Now</th></tr>';
    for (const x of d.rivers) { const tr = el('tr'); tr.append(el('td', null, (x.river || '') + ' at ' + x.station + ' (' + x.km + ' km)'),
      el('td', null, x.level_m == null ? '' : x.level_m.toFixed(2) + ' ' + x.unit), el('td', null, x.typical_low_m != null ? x.typical_low_m + '–' + x.typical_high_m : ''),
      el('td', x.state === 'high' ? 'err' : null, (x.state || '') + ' ' + (ARROW[x.trend] || ''))); t.append(tr); }
    if (d.rivers.length) r.append(t); r.append(el('div', 'src', d.source));
  } catch (e) { failed(r, e); }
  const g = card('Rain');
  try {
    const d = await api('/v1/environment/rain' + P());
    if (!d.gauges.length) g.append(el('p', 'dim', 'No rain gauge within 10 km.'));
    for (const x of d.gauges) g.append(el('p', null, x.gauge + ' (' + x.km + ' km): ' + x.last_hour_mm + ' mm in the last hour, ' + x.last_24h_mm + ' mm in 24 hours'));
    g.append(el('div', 'src', d.source));
  } catch (e) { failed(g, e); }
}
async function tides() {
  const c = card(null);
  try {
    const d = await api('/v1/environment/tides' + P() + '&km=40');
    if (!d.gauge) { c.append(el('p', 'dim', d.note || 'No tide gauge near here.')); return; }
    c.append(el('div', 'big', d.level_m.toFixed(2) + ' ' + d.unit), el('p', null, d.gauge + ' (' + d.km + ' km) · ' + (ARROW[d.trend] || d.trend || '') + ' · ' + when(d.at)),
      el('p', 'dim small', 'The level measured now. High and low water times need the Admiralty tide key (not set up yet).'), el('div', 'src', d.source));
  } catch (e) { failed(c, e); }
}
async function overflows() {
  const c = card(null);
  try {
    const d = await api('/v1/environment/overflows' + P());
    c.append(el('p', null, d.discharging.length ? d.discharging.length + ' spilling now' : 'None spilling now'),
      el('p', 'dim small', (d.recent.length ? d.recent.length + ' in the last 48 hours. ' : '') + (d.offline ? d.offline + ' monitors offline. ' : '') +
        'Within ' + d.km + ' km' + (d.rivers && d.rivers.length ? ', and on ' + d.rivers.join(', ') + ' within ' + d.river_km + ' km' : '') + '.'));
    const t = el('table', 'env'); t.innerHTML = '<tr><th>Overflow</th><th>Into</th><th>Status</th><th>Last</th></tr>';
    for (const o of d.overflows.slice(0, 30)) { const tr = el('tr'); tr.append(el('td', null, o.name + (o.km != null ? ' (' + o.km + ' km)' : '')), el('td', null, o.river || ''),
      el('td', o.status === 'discharging' ? 'err' : null, o.status), el('td', null, when(o.last_end || o.last_start))); t.append(tr); }
    if (d.overflows.length) c.append(t);
    c.append(el('p', 'dim small', d.note), el('div', 'src', d.source));
  } catch (e) { failed(c, e); }
}
async function show() {
  $('out').replaceChildren(); $('h').textContent = TITLES[TAB] || 'Environment';
  try { localStorage.setItem('wa_env_place', $('place').value); } catch (e) {}
  await ({ air, floods, tides, overflows }[TAB] || air)();
}
async function start() {
  const ps = await api('/v1/places'), sel = $('place'), list = ps.places || ps;
  sel.replaceChildren(...list.map(p => new Option(p.name, p.id)));
  if (!list.length) { $('auth').hidden = true; msg.textContent = 'No places yet: add your home on the Places page.'; return; }
  let keep = null; try { keep = localStorage.getItem('wa_env_place'); } catch (e) {}
  if (keep && list.some(p => p.id === keep)) sel.value = keep;
  $('auth').hidden = true; await show();
}
$('place').onchange = () => show();
$('auth').onsubmit = e => { e.preventDefault(); try { sessionStorage.setItem('wa_token', tok.value); } catch (x) {} start().catch(e => { msg.textContent = e.message; }); };
if (tok.value) start().catch(e => { msg.textContent = e.message; });
</script></main></body></html>"""

TABS = {"": "environment", "floods": "floods", "tides": "env-tides", "overflows": "env-overflows"}


@page_router.get("/environment", response_class=HTMLResponse)
async def environment_page(request: Request) -> str:
    return themed(PAGE, "environment", request)


@page_router.get("/environment/{tab}", response_class=HTMLResponse)
async def environment_tab(request: Request, tab: str) -> str:
    from fastapi import HTTPException

    if tab not in TABS:
        raise HTTPException(404, "No such tab")
    return themed(PAGE, TABS[tab], request)
