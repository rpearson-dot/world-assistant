"""The Logs pages: an overview and one page per raw feed (charger syslog, OCPP frames, network events). They hold no
data and never build HTML from it; everything is fetched from /v1/logs in the browser."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

page_router = APIRouter()

_HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant logs</title>
<style>
/*BASE_CSS*/
.feed { display: grid; gap: 2px; }
.line { display: grid; grid-template-columns: max-content max-content 1fr; gap: 10px; align-items: baseline;
  padding: 4px 6px; border-bottom: 1px solid var(--line); font-size: 13px; }
.line:last-child { border-bottom: 0; }
.line .at { color: var(--dim); white-space: nowrap; font-variant-numeric: tabular-nums; }
.line .msg { overflow-wrap: anywhere; font-family: ui-monospace, monospace; }
.line.block .msg, .line.sev-err .msg { color: var(--bad); }
.tag { font-size: 11px; font-weight: 600; padding: 1px 7px; border-radius: 6px; background: var(--chip); color: var(--dim); white-space: nowrap; }
.tag.bad { background: var(--bad-bg); color: var(--bad); } .tag.warn { background: var(--warn-bg); color: var(--warn); }
.tag.good { background: var(--good-bg); color: var(--good); } .tag.in { background: var(--good-bg); color: var(--good); }
.tag.out { background: var(--chip); }
.controls { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }
.controls select { padding: 6px 8px; }
.logcard { display: grid; gap: 4px; }
.logcard a { text-decoration: none; }
.loglink { display: flex; justify-content: space-between; gap: 12px; padding: 12px 14px; border-radius: var(--radius);
  background: var(--card); border: 1px solid var(--chip); align-items: center; }
.loglink .t { font-weight: 650; } .loglink .d { color: var(--dim); font-size: 13px; }
</style></head><body><main>
<!--NAV-->
"""
_TOKENBAR = """<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary" type="button">Load</button></div><p id="msg" class="err"></p>"""

_API = """
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
async function api(path) {
  const r = await fetch(path, { headers: { Authorization: 'Bearer ' + tok.value } });
  if (!r.ok) { let d = r.status; try { d = (await r.json()).detail; } catch (e) {} throw new Error(typeof d === 'string' ? d : JSON.stringify(d)); }
  return r.json();
}
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const when = s => new Date(s).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' });
async function chargers(sel) {
  const d = await api('/v1/logs/chargers');
  sel.replaceChildren(); (d.chargers || []).forEach(c => { const o = el('option', c.id + (c.connected ? ' (connected)' : '')); o.value = c.id; sel.append(o); });
  return d.chargers || [];
}
"""

OVERVIEW = _HEAD + """<h1>Logs</h1>
<p class="dim">The raw feeds for diagnosis, in one place. These stay readable even when the feature they belong to has
been switched off — which is often when you need them.</p>
<div class="card logcard">
  <a href="/logs/charger"><div class="loglink"><div><div class="t">Charger</div><div class="d">The charger's own syslog: plugging in, charging, faults, Wi-Fi drops — whatever its firmware sends.</div></div><div class="dim">›</div></div></a>
  <a href="/logs/ocpp"><div class="loglink"><div><div class="t">OCPP messages</div><div class="d">Every OCPP 1.6J frame to and from the charger, exactly as it went. Protocol-level.</div></div><div class="dim">›</div></div></a>
  <a href="/logs/network"><div class="loglink"><div><div class="t">Network</div><div class="d">The network event stream — DNS, firewall decisions, devices joining and leaving, DHCP.</div></div><div class="dim">›</div></div></a>
</div>
<p class="dim small">The charger log needs the charger pointed at this machine's syslog server (UDP 5514, WA_SYSLOG_PORT).
Lines are matched to a charger by the address it last connected from, and by the charge-point ID it names itself.</p>
</main></body></html>"""

CHARGER = _HEAD + """<h1>Charger log</h1>
""" + _TOKENBAR + """
<div class="controls">
  <label>Charger <select id="cid"></select></label>
  <label>Show <select id="sev"><option value="">everything</option><option value="5">notices and worse</option><option value="4">warnings and worse</option><option value="3">errors and worse</option></select></label>
  <label class="dim"><input type="checkbox" id="raw"> raw lines</label>
</div>
<div class="card"><div class="feed" id="feed"></div><p id="empty" class="dim" hidden></p>
  <p><button id="more" type="button" hidden>Show more</button></p></div>
<script>""" + _API + """
let oldest = null;
function row(l) {
  const r = el('div', null, 'line sev-' + (l.severity != null && l.severity <= 3 ? 'err' : 'ok'));
  r.append(el('div', when(l.at), 'at'));
  r.append(el('div', l.severity_word || (l.facility != null ? 'log' : '·'), 'tag' + (l.severity != null && l.severity <= 3 ? ' bad' : l.severity === 4 ? ' warn' : '')));
  r.append(el('div', $('raw').checked ? l.raw : l.message, 'msg'));
  return r;
}
async function load(append) {
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  msg.textContent = '';
  const cid = $('cid').value; if (!cid) return;
  const sev = $('sev').value;
  let path = '/v1/logs/charger/' + encodeURIComponent(cid) + '?limit=200' + (sev ? '&severity=' + sev : '');
  if (append && oldest != null) path += '&before=' + oldest;
  try {
    const d = await api(path), feed = $('feed');
    if (!append) { feed.replaceChildren(); oldest = null; }
    d.lines.forEach(l => { feed.append(row(l)); oldest = oldest == null ? l.id : Math.min(oldest, l.id); });
    $('empty').hidden = feed.childElementCount > 0;
    $('empty').textContent = 'No charger syslog yet. Point the charger\\'s log server at this machine (UDP 5514).';
    $('more').hidden = d.lines.length < 200;
  } catch (e) { msg.textContent = e.message; }
}
async function start() { try { const cs = await chargers($('cid')); if (cs.length) load(false); else { $('empty').hidden = false; $('empty').textContent = 'No charger has connected yet.'; } } catch (e) { msg.textContent = e.message; } }
$('go').onclick = start; $('cid').onchange = () => load(false); $('sev').onchange = () => load(false);
$('raw').onchange = () => load(false); $('more').onclick = () => load(true);
start();
setInterval(() => { if (!document.hidden && oldest != null) load(false); }, 15000);
</script></main></body></html>"""

OCPP = _HEAD + """<h1>OCPP messages</h1>
""" + _TOKENBAR + """
<div class="controls"><label>Charger <select id="cid"></select></label>
  <label>Action <input id="action" placeholder="e.g. Heartbeat" style="padding:6px 8px"></label></div>
<div class="card"><div class="feed" id="feed"></div><p id="empty" class="dim" hidden></p>
  <p><button id="more" type="button" hidden>Show more</button></p></div>
<script>""" + _API + """
let oldest = null;
const KIND = { 2: 'call', 3: 'result', 4: 'error' };
function row(m) {
  const r = el('div', null, 'line');
  r.append(el('div', when(m.at), 'at'));
  r.append(el('div', (m.direction === 'in' ? '← ' : '→ ') + (m.action || KIND[m.kind] || ''), 'tag ' + (m.direction === 'in' ? 'in' : 'out')));
  r.append(el('div', m.raw, 'msg'));
  return r;
}
async function load(append) {
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  msg.textContent = '';
  const cid = $('cid').value; if (!cid) return;
  const a = $('action').value.trim();
  let path = '/v1/logs/ocpp/' + encodeURIComponent(cid) + '?limit=200' + (a ? '&action=' + encodeURIComponent(a) : '');
  if (append && oldest != null) path += '&before=' + oldest;
  try {
    const d = await api(path), feed = $('feed');
    if (!append) { feed.replaceChildren(); oldest = null; }
    d.messages.forEach(m => { feed.append(row(m)); oldest = oldest == null ? m.id : Math.min(oldest, m.id); });
    $('empty').hidden = feed.childElementCount > 0; $('empty').textContent = 'Nothing from this charger yet.';
    $('more').hidden = d.messages.length < 200;
  } catch (e) { msg.textContent = e.message; }
}
async function start() { try { const cs = await chargers($('cid')); if (cs.length) load(false); else { $('empty').hidden = false; $('empty').textContent = 'No charger has connected yet.'; } } catch (e) { msg.textContent = e.message; } }
$('go').onclick = start; $('cid').onchange = () => load(false);
$('action').onchange = () => load(false); $('more').onclick = () => load(true);
start();
setInterval(() => { if (!document.hidden && oldest != null) load(false); }, 20000);
</script></main></body></html>"""

NETWORK = _HEAD + """<h1>Network log</h1>
""" + _TOKENBAR + """
<div class="controls">
  <label>Kind <select id="kind"><option value="">all</option><option>dns</option><option>firewall</option><option>client</option><option>dhcp</option><option>system</option></select></label>
  <label>Action <select id="action"><option value="">all</option><option>allow</option><option>block</option><option>join</option><option>leave</option><option>lease</option></select></label>
  <label>Last <select id="hours"><option value="1">hour</option><option value="6" selected>6 hours</option><option value="24">day</option><option value="168">week</option></select></label>
</div>
<div class="card"><div class="feed" id="feed"></div><p id="empty" class="dim" hidden></p>
  <p><button id="more" type="button" hidden>Show more</button></p></div>
<script>""" + _API + """
let oldest = null;
function row(e) {
  const r = el('div', null, 'line' + (e.action === 'block' ? ' block' : ''));
  r.append(el('div', when(e.at), 'at'));
  const cls = e.action === 'block' ? ' bad' : e.action === 'allow' ? ' good' : '';
  r.append(el('div', e.kind + (e.action ? ' ' + e.action : ''), 'tag' + cls));
  const parts = [];
  if (e.device || e.ip) parts.push((e.device || e.ip));
  if (e.dest) parts.push('→ ' + e.dest + (e.port ? ':' + e.port : ''));
  if (e.proto) parts.push('(' + e.proto + ')');
  r.append(el('div', parts.join(' '), 'msg'));
  return r;
}
async function load(append) {
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  msg.textContent = '';
  let path = '/v1/logs/network?limit=300&hours=' + $('hours').value + ($('kind').value ? '&kind=' + $('kind').value : '') + ($('action').value ? '&action=' + $('action').value : '');
  if (append && oldest != null) path += '&before=' + oldest;
  try {
    const d = await api(path), feed = $('feed');
    if (!append) { feed.replaceChildren(); oldest = null; }
    d.events.forEach(e => { feed.append(row(e)); oldest = oldest == null ? e.id : Math.min(oldest, e.id); });
    $('empty').hidden = feed.childElementCount > 0;
    $('empty').textContent = 'Nothing in this window. The network log needs the UniFi gateway log switched on (In the home › Networking › Ubiquiti).';
    $('more').hidden = d.events.length < 300;
  } catch (e) { msg.textContent = e.message; }
}
['kind', 'action', 'hours'].forEach(id => $(id).onchange = () => load(false));
$('go').onclick = () => load(false); $('more').onclick = () => load(true);
load(false);
setInterval(() => { if (!document.hidden && oldest != null) load(false); }, 20000);
</script></main></body></html>"""


@page_router.get("/logs", response_class=HTMLResponse, include_in_schema=False)
async def logs_overview(request: Request) -> str:
    return themed(OVERVIEW, "logs", request)


@page_router.get("/logs/charger", response_class=HTMLResponse, include_in_schema=False)
async def logs_charger(request: Request) -> str:
    return themed(CHARGER, "logs-charger", request)


@page_router.get("/logs/ocpp", response_class=HTMLResponse, include_in_schema=False)
async def logs_ocpp(request: Request) -> str:
    return themed(OCPP, "logs-ocpp", request)


@page_router.get("/logs/network", response_class=HTMLResponse, include_in_schema=False)
async def logs_network(request: Request) -> str:
    return themed(NETWORK, "logs-network", request)
