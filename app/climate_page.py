"""Climate control (/climate, In the home → Climate control): heating and cooling in one place — the Nest thermostat,
the Ecodan heat pump and the air conditioning — each card linking to its own tab. Holds no data; it reads each part's
API and never builds HTML from anything but counts and the state each part reports."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

page_router = APIRouter()

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant climate control</title>
<style>
/*BASE_CSS*/
.rooms { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }
.room { border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; text-decoration: none; color: var(--ink); display: block; }
.room b { display: block; font-size: 1.1em; } .room .now { font-size: 1.3em; font-weight: 650; margin-top: 4px; }
.room.on { border-color: var(--accent); } .room.bad { border-color: var(--bad); } .room .sub { color: var(--dim); font-size: .9em; margin-top: 2px; }
.room .off { color: var(--dim); }
</style></head><body><main>
<!--NAV-->
<h1>Climate control</h1>
<p class="dim">Heating and cooling together: the thermostat, the heat pump's hot water, and the air conditioning.</p>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary" type="button">Load</button></div>
<p id="msg" class="err"></p>
<div class="rooms" id="cards"></div>
<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
async function get(path) {
  const r = await fetch(path, { headers: { Authorization: 'Bearer ' + tok.value } });
  if (r.status === 404) return { off: true };
  if (!r.ok) throw new Error(path + ': HTTP ' + r.status);
  return r.json();
}
function card(href, title, now, nowCls, sub) {
  const a = el('a', null, 'room' + (nowCls ? ' ' + nowCls : '')); a.href = href;
  a.append(el('b', title), el('div', now, 'now' + (now === 'Set up' || now === 'Off' ? ' off' : '')));
  if (sub) a.append(el('div', sub, 'sub'));
  return a;
}
async function load() {
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  msg.textContent = '';
  const box = $('cards'); box.replaceChildren();
  const [nest, eco, ac] = await Promise.all([get('/v1/nest'), get('/v1/ecodan'), get('/v1/airstage')]);
  // Heating (Nest)
  if (nest.off || nest.not_set_up) box.append(card('/nest', 'Heating', 'Set up', '', 'Nest thermostat'));
  else {
    const t = (nest.thermostats || [])[0];
    if (!t) box.append(card('/nest', 'Heating', 'No reading', '', 'Nest thermostat'));
    else box.append(card('/nest', t.name || 'Heating', t.hvac === 'HEATING' ? 'Heating now' : (t.ambient_c != null ? t.ambient_c.toFixed(1) + '°' : 'Idle'),
      t.hvac === 'HEATING' ? 'on' : '', [t.ambient_c != null ? 'room ' + t.ambient_c.toFixed(1) + '°' : null, t.setpoint_c != null ? 'target ' + t.setpoint_c.toFixed(1) + '°' : null].filter(Boolean).join(' · ')));
  }
  // Heat pump (Ecodan)
  if (eco.off || eco.not_set_up) box.append(card('/ecodan', 'Heat pump', 'Set up', '', 'Ecodan hot water'));
  else {
    const u = (eco.units || [])[0];
    if (!u) box.append(card('/ecodan', 'Heat pump', 'No reading', '', 'Ecodan hot water'));
    else box.append(card('/ecodan', 'Heat pump', u.tank_c != null ? 'Tank ' + u.tank_c.toFixed(1) + '°' : (u.offline ? 'Offline' : 'On'),
      u.forced_hot_water ? 'on' : (u.offline ? 'bad' : ''),
      [u.tank_target_c != null ? 'target ' + u.tank_target_c.toFixed(1) + '°' : null, u.forced_hot_water ? 'hot-water boost' : null, u.outdoor_c != null ? 'outside ' + u.outdoor_c.toFixed(0) + '°' : null].filter(Boolean).join(' · ')));
  }
  // Air conditioning (Airstage)
  if (ac.off) box.append(card('/aircon', 'Air conditioning', 'Set up', '', 'Fujitsu Airstage'));
  else {
    const units = ac.units || [], running = units.filter(u => u.state && u.state.power);
    box.append(card('/aircon', 'Air conditioning', units.length ? (running.length ? running.length + ' of ' + units.length + ' on' : 'All off') : 'No units',
      running.length ? 'on' : '', units.length ? units.map(u => u.name).join(', ') : 'Add units on the tab'));
  }
}
$('go').onclick = () => load().catch(e => msg.textContent = e.message);
load().catch(e => msg.textContent = e.message);
</script></main></body></html>"""


@page_router.get("/climate", response_class=HTMLResponse, include_in_schema=False)
async def climate_page(request: Request) -> str:
    return themed(PAGE, "climate", request)
