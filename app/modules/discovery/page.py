"""The discovery page (/discovery, In the home → Discovery): what each device probably is and what World Assistant
could do with it. The page holds no data and never builds HTML from it."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

page_router = APIRouter()

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant discovery</title>
<style>
/*BASE_CSS*/
.wrap { overflow-x: auto; }
table.t { width: 100%; border-collapse: collapse; font-size: .9em; } table.t td, table.t th { padding: 5px 6px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
.why { color: var(--dim); font-size: .85em; } .sug { font-weight: 600; } .sug.done { font-weight: 400; color: var(--dim); }
</style></head><body><main>
<!--NAV-->
<h1>Discovery</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary" type="button">Load</button></div>
<p id="msg" class="err"></p>
<div class="card"><p id="say"></p><div class="wrap"><table class="t" id="devs"></table></div>
  <p class="dim small">From what the Network module has seen: who made each device's network adapter, the name the router
  gave it, and what it looks up. Nothing is changed: each suggestion takes you to the module's page.</p></div>
<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
async function load() {
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  msg.textContent = '';
  try {
    const r = await fetch('/v1/discovery', { headers: { Authorization: 'Bearer ' + tok.value } });
    if (r.status === 404) { $('say').textContent = 'Discovery needs the Network module: turn it on in Settings → Setup.'; return; }
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json(), t = $('devs'); t.replaceChildren();
    $('say').textContent = d.devices.length ? d.identified + ' of ' + d.devices.length + ' devices recognised; ' + d.suggestions + ' suggestions.' +
      (d.oui_loaded ? '' : ' The register of adapter makers is fetched within the week.') : 'No devices yet: set up the Network module first.';
    const h = el('tr'); ['Device', 'Probably', 'What World Assistant could do'].forEach(x => h.append(el('th', x))); t.append(h);
    for (const x of d.devices) {
      const tr = el('tr'), a = el('td'); a.append(el('div', x.label), el('div', x.mac + (x.ip ? ' · ' + x.ip : '') + (x.vendor ? ' · ' + x.vendor : ''), 'why'));
      const b = el('td'); if (x.kind) { b.append(el('div', x.sure + ' ' + x.what), el('div', x.why.join('; '), 'why')); } else b.append(el('span', 'not recognised', 'why'));
      const c = el('td'); if (x.suggestion) { const s = x.suggestion;
        if (s.link) { const l = el('a', s.say, 'sug' + (s.state === 'done' ? ' done' : '')); l.href = s.link; c.append(l); } else c.append(el('span', s.say, 'why')); }
      tr.append(a, b, c); t.append(tr);
    }
  } catch (e) { msg.textContent = e.message; }
}
$('go').onclick = load;
load();
</script></main></body></html>"""


@page_router.get("/discovery", response_class=HTMLResponse, include_in_schema=False)
async def discovery_page(request: Request) -> str:
    return themed(PAGE, "discovery", request)
