"""In the home (/home): the hub for the house, in two areas — Climate control (the thermostat, the heat pump and the
air conditioning) and Networking (the devices, Pi-hole and the UniFi gateway). Each card links to its area and shows a
one-line state. Holds no data; it reads each area's API and builds nothing from it but counts."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

page_router = APIRouter()

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant in the home</title>
<style>
/*BASE_CSS*/
.rooms { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); }
.room { border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; text-decoration: none; color: var(--ink); display: block; }
.room b { display: block; font-size: 1.15em; } .room .now { font-size: 1.3em; font-weight: 650; margin-top: 4px; }
.room.on { border-color: var(--accent); } .room .sub { color: var(--dim); font-size: .9em; margin-top: 4px; }
</style></head><body><main>
<!--NAV-->
<h1>In the home</h1>
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
function card(href, title, now, sub, on) {
  const a = el('a', null, 'room' + (on ? ' on' : '')); a.href = href;
  a.append(el('b', title), el('div', now, 'now'));
  if (sub) a.append(el('div', sub, 'sub'));
  return a;
}
async function load() {
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  msg.textContent = '';
  const box = $('cards'); box.replaceChildren();
  // Climate control: the one-line state from whichever parts are on.
  try {
    const [nest, eco, ac] = await Promise.all([get('/v1/nest'), get('/v1/ecodan'), get('/v1/airstage')]);
    const bits = [];
    const t = !nest.off && !nest.not_set_up && (nest.thermostats || [])[0];
    if (t) bits.push(t.hvac === 'HEATING' ? 'heating now' : (t.ambient_c != null ? 'home ' + t.ambient_c.toFixed(1) + '°' : 'thermostat on'));
    const u = !eco.off && !eco.not_set_up && (eco.units || [])[0];
    if (u && u.tank_c != null) bits.push('tank ' + u.tank_c.toFixed(0) + '°' + (u.forced_hot_water ? ' (boost)' : ''));
    const units = !ac.off ? (ac.units || []) : [], running = units.filter(x => x.state && x.state.power);
    if (units.length) bits.push(running.length ? running.length + ' of ' + units.length + ' AC on' : 'AC all off');
    const anyOn = (t && t.hvac === 'HEATING') || (u && u.forced_hot_water) || running.length > 0;
    box.append(card('/climate', 'Climate control', bits.length ? (t && t.hvac === 'HEATING' ? 'Heating' : (running.length ? 'Cooling / on' : 'Idle')) : 'Set up',
      bits.length ? bits.join(' · ') : 'Thermostat, heat pump and air conditioning', anyOn));
  } catch (e) { box.append(card('/climate', 'Climate control', '–', 'Thermostat, heat pump and air conditioning')); }
  // Networking
  try {
    const net = await get('/v1/network/devices'), devs = net.devices || [];
    const fw = devs.reduce((s, d) => s + (Number(d.firewall_blocks) || 0), 0);
    box.append(card('/networking', 'Networking', devs.length ? devs.length + ' devices' : 'Set up',
      devs.length ? (devs.reduce((s, d) => s + (Number(d.lookups) || 0), 0)) + ' lookups today' + (fw ? ' · ' + fw + ' firewall blocks' : '') : 'Devices, Pi-hole and the UniFi gateway', devs.length > 0));
  } catch (e) { box.append(card('/networking', 'Networking', '–', 'Devices, Pi-hole and the UniFi gateway')); }
}
$('go').onclick = () => load().catch(e => msg.textContent = e.message);
load().catch(e => msg.textContent = e.message);
</script></main></body></html>"""


@page_router.get("/home", response_class=HTMLResponse, include_in_schema=False)
async def home_page(request: Request) -> str:
    return themed(PAGE, "home", request)
