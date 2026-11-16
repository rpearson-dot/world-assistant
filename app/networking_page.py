"""Networking (/networking, In the home → Networking): the home network in one place — the devices on it, Pi-hole's
DNS picture, the UniFi gateway (Ubiquiti) firewall picture, and Discovery. Each card links to its tab. Holds no data;
it reads each part's API and builds nothing from it but counts."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

page_router = APIRouter()

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant networking</title>
<style>
/*BASE_CSS*/
.rooms { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }
.room { border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; text-decoration: none; color: var(--ink); display: block; }
.room b { display: block; font-size: 1.1em; } .room .now { font-size: 1.3em; font-weight: 650; margin-top: 4px; }
.room.on { border-color: var(--accent); } .room .sub { color: var(--dim); font-size: .9em; margin-top: 2px; } .room .off { color: var(--dim); }
</style></head><body><main>
<!--NAV-->
<h1>Networking</h1>
<p class="dim">Every device, the DNS lookups Pi-hole sees, the firewall decisions the UniFi gateway logs, and what each device probably is.</p>
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
  a.append(el('b', title), el('div', now, 'now' + (now === 'Set up' || now === 'Off' ? ' off' : '')));
  if (sub) a.append(el('div', sub, 'sub'));
  return a;
}
async function load() {
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  msg.textContent = '';
  const box = $('cards'); box.replaceChildren();
  const net = await get('/v1/network/devices');
  const devs = net.devices || [];
  const lookups = devs.reduce((s, d) => s + (Number(d.lookups) || 0), 0);
  const fw = devs.reduce((s, d) => s + (Number(d.firewall_blocks) || 0), 0);
  const blocked = devs.reduce((s, d) => s + (Number(d.blocked) || 0), 0);
  box.append(card('/network', 'Devices', devs.length ? String(devs.length) : 'None yet',
    devs.length ? lookups + ' lookups today' : 'Point the UniFi log here, or set up Pi-hole', devs.length > 0));
  // Pi-hole
  const ph = await get('/v1/pihole/settings');
  box.append(card('/pihole', 'Pi-hole', ph.off ? 'Set up' : (blocked + ' blocked'),
    ph.off ? 'DNS blocking' : (lookups + ' got past your lists today'), !ph.off));
  // Ubiquiti (UniFi)
  box.append(card('/unifi', 'Ubiquiti', fw ? fw + ' blocked' : 'Firewall',
    fw ? 'firewall blocks today' : 'Joins, leaves and firewall decisions', fw > 0));
  // Discovery
  const disc = await get('/v1/discovery');
  box.append(card('/discovery', 'Discovery', disc.off ? 'Set up' : ((disc.identified || 0) + ' known'),
    disc.off ? 'What each device is' : 'of ' + (disc.devices || []).length + ' devices identified', !disc.off));
}
$('go').onclick = () => load().catch(e => msg.textContent = e.message);
load().catch(e => msg.textContent = e.message);
</script></main></body></html>"""


@page_router.get("/networking", response_class=HTMLResponse, include_in_schema=False)
async def networking_page(request: Request) -> str:
    return themed(PAGE, "networking", request)
