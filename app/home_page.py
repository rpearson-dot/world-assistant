"""In the home (/home): what is running in the house at a glance, each part a tab of its own (Air conditioning now;
more as modules for the home are added). The overview reads each part's API and holds no data itself."""

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
.rooms { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }
.room { border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; text-decoration: none; color: var(--ink); display: block; }
.room b { display: block; font-size: 1.1em; } .room .now { font-size: 1.4em; font-weight: 650; margin-top: 4px; }
.room.on { border-color: var(--accent); } .room.bad { border-color: var(--bad); }
</style></head><body><main>
<!--NAV-->
<h1>In the home</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary" type="button">Load</button></div>
<p id="msg" class="err"></p>
<div class="card"><h2>Air conditioning</h2><p id="acsay" class="dim">Loading…</p><div class="rooms" id="ac"></div></div>
<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const VERB = { heat: 'Heating', cool: 'Cooling', dry: 'Drying', fan_only: 'Fan only', auto: 'Auto' };
async function load() {
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  msg.textContent = '';
  const r = await fetch('/v1/airstage', { headers: { Authorization: 'Bearer ' + tok.value } });
  const box = $('ac'); box.replaceChildren();
  if (r.status === 404) { $('acsay').textContent = 'Switched off. Turn on Air conditioning in Settings → Setup to see your units here.'; return; }
  if (!r.ok) { msg.textContent = 'Air conditioning: HTTP ' + r.status; return; }
  const d = await r.json();
  if (!d.units.length) { $('acsay').textContent = 'No units added yet: add them on the Air conditioning tab.'; return; }
  const running = d.units.filter(u => u.state && u.state.power);
  $('acsay').textContent = running.length ? running.length + ' of ' + d.units.length + ' on.' : 'All off.';
  for (const u of d.units) {
    const s = u.state || {}, a = el('a', null, 'room' + (s.power ? ' on' : '') + (u.ok === false || s.error_code ? ' bad' : ''));
    a.href = '/aircon';
    a.append(el('b', u.name));
    const now = u.ok === false ? 'Not answering' : !u.state ? 'Not read yet' : s.power
      ? (VERB[s.mode] || 'On') + (s.target_c != null && s.mode !== 'fan_only' ? ' to ' + s.target_c + '°' : '') : 'Off';
    a.append(el('div', now, 'now'));
    if (s.indoor_c != null) a.append(el('div', s.indoor_c.toFixed(1) + '° inside' + (s.outdoor_c != null ? ', ' + s.outdoor_c.toFixed(0) + '° outside' : ''), 'dim'));
    if (s.error_code) a.append(el('div', 'Error code ' + s.error_code, 'err'));
    if (s.filter_due) a.append(el('div', 'Filter needs cleaning', 'dim'));
    box.append(a);
  }
}
$('go').onclick = () => load().catch(e => msg.textContent = e.message);
load().catch(e => msg.textContent = e.message);
</script></main></body></html>"""


@page_router.get("/home", response_class=HTMLResponse, include_in_schema=False)
async def home_page(request: Request) -> str:
    return themed(PAGE, "home", request)
