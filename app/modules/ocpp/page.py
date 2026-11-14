"""The Charger page (/charger): the charger's state now, start and stop, its restarts, drops and faults in a
timeline, its sessions, its settings and its configuration. Holds no data; calls /v1/ocpp."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

page_router = APIRouter()

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant charger</title>
<style>
/*BASE_CSS*/
.stats { display: flex; flex-wrap: wrap; gap: 18px; margin: 6px 0; }
.stats div { min-width: 7em; } .stats b { display: block; font-size: 1.3em; }
.ev { display: grid; grid-template-columns: max-content 1fr; gap: 2px 10px; font-size: .92em; }
.ev .when { white-space: nowrap; color: var(--dim); font-variant-numeric: tabular-nums; }
.ev .k-boot, .ev .k-fault { color: var(--bad); } .ev .k-disconnected, .ev .k-refused { color: var(--warn); }
pre.raw { white-space: pre-wrap; word-break: break-all; font-size: .8em; max-height: 360px; overflow: auto; }
</style></head><body><main>
<!--NAV-->
<h1>Charger</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<select id="cp" aria-label="Charger"></select><button id="go" class="primary" type="button">Load</button></div>
<p id="msg" class="err"></p>

<div class="card hero" id="none" hidden><div class="say">No charger has connected yet.</div>
  <div class="dim">In the charger's settings (its app or web page), set the central system (OCPP server) address to
  <b id="addr"></b><i>its id</i>, OCPP 1.6J. The charger can talk to only one central system, so this replaces Home
  Assistant's OCPP integration. World Assistant must be reached on your home network, not through Tailscale.</div></div>

<section id="view" hidden>
<div class="card hero"><div class="say" id="say"></div><div><span id="state" class="pill"></span><div class="dim" id="seen"></div></div></div>
<div class="card">
  <div class="stats" id="stats"></div>
  <div class="row"><button id="start" class="primary" type="button">Start charging</button><button id="stop" type="button">Stop charging</button>
    <span id="cmsg" class="dim"></span></div>
</div>
<div class="card"><h2>What happened</h2>
  <div class="row"><label>Show <select id="kind"><option value="">everything</option><option value="boot">restarts</option>
    <option value="disconnected">drops</option><option value="fault">faults</option><option value="status">status changes</option>
    <option value="command">commands</option><option value="session">sessions</option></select></label>
    <label>Over <select id="days"><option value="1">a day</option><option value="7" selected>a week</option><option value="30">a month</option><option value="365">a year</option></select></label></div>
  <div class="ev" id="events"></div></div>
<div class="card"><h2>Sessions</h2><table id="sessions"></table></div>
<div class="card"><h2>Settings</h2>
  <div class="form">
    <label>Chargers allowed to connect <input id="ids" placeholder="e.g. U1HKP (blank: any on the home network)">
      <span class="hint">Separate several with commas.</span></label>
    <div class="row"><label>Ask for meter readings while charging every <input id="every" type="number" min="15" max="900" style="width:6em"> s</label>
      <label class="tick"><input id="ha" type="checkbox"> Show the charger in Home Assistant</label>
      <button id="save" type="button">Save</button></div>
    <p class="dim">The address for a charger: <b id="addr2"></b><i>its id</i></p>
    <p id="smsg" class="dim"></p>
  </div></div>
<div class="card"><h2>More</h2>
  <div class="row"><button id="cfg" type="button">Read its configuration</button><button id="soft" type="button">Restart (soft)</button>
    <button id="hard" type="button">Restart (hard)</button><button id="trig" type="button">Ask for its status</button></div>
  <table id="config"></table>
  <details><summary>The last 100 messages, as sent</summary><pre class="raw" id="raw"></pre></details>
</div>
</section>

<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
async function api(method, path, body) {
  const r = await fetch(path, { method, headers: { Authorization: 'Bearer ' + tok.value, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) { let d = r.status; try { d = (await r.json()).detail; } catch (e) {} throw new Error(typeof d === 'string' ? d : JSON.stringify(d)); }
  return r.status === 204 ? null : r.json();
}
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const when = s => s ? new Date(s).toLocaleString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
const ago = s => { if (!s) return 'never'; const m = Math.round((Date.now() - new Date(s)) / 60000); return m < 1 ? 'just now' : m < 120 ? m + ' min ago' : Math.round(m / 60) + ' h ago'; };
let data = null;
const cur = () => data && data.chargers.find(c => c.id === $('cp').value);
async function load() {
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  msg.textContent = '';
  try {
    data = await api('GET', '/v1/ocpp');
    $('addr').textContent = $('addr2').textContent = data.address;
    const keep = $('cp').value; $('cp').replaceChildren(...data.chargers.map(c => new Option(c.id, c.id)));
    if (keep && data.chargers.some(c => c.id === keep)) $('cp').value = keep;
    $('none').hidden = data.chargers.length > 0; $('view').hidden = !data.chargers.length;
    if (document.activeElement.tagName !== 'INPUT') { $('ids').value = data.settings.chargers.join(', '); $('every').value = data.settings.meter_every_s; $('ha').checked = data.settings.publish_ha; }
    if (data.chargers.length) await draw();
  } catch (e) { msg.textContent = e.message; }
}
async function draw() {
  const c = cur(); if (!c) return;
  const m = c.meter || {}, s = c.session, live = s && !s.stopped_at;
  $('state').textContent = c.connected ? (c.status || 'connected') : 'not connected';
  $('state').className = 'pill ' + (!c.connected ? 'bad' : c.error_code && c.error_code !== 'NoError' ? 'bad' : c.status === 'Charging' ? 'good' : '');
  $('seen').textContent = 'Last heard ' + ago(c.last_seen) + (c.booted_at ? ' · started up ' + when(c.booted_at) : '');
  const b = c.boot || {};
  $('say').textContent = (b.chargePointVendor ? b.chargePointVendor + ' ' + (b.chargePointModel || '') : 'Charger ' + c.id) +
    (c.error_code && c.error_code !== 'NoError' ? ': fault ' + c.error_code : live ? ': charging session ' + s.transaction_id + ' since ' + when(s.started_at) : '');
  const kwh = live && s.meter_start_wh != null && (s.last_meter_wh ?? m.energy_wh) != null ? (((s.last_meter_wh ?? m.energy_wh) - s.meter_start_wh) / 1000).toFixed(2) : null;
  const st = $('stats'); st.replaceChildren();
  for (const [k, v] of [['Power', c.status === 'Charging' && m.power_w != null ? (m.power_w / 1000).toFixed(2) + ' kW' : '0 kW'],
      ['This session', kwh != null ? kwh + ' kWh' : '–'], ['Meter', m.energy_wh != null ? (m.energy_wh / 1000).toFixed(1) + ' kWh' : '–'],
      ['Restarts this week', c.week.boots], ['Drops this week', c.week.drops], ['Faults this week', c.week.faults]]) {
    const d = el('div', k, 'dim'); d.appendChild(el('b', String(v))); st.appendChild(d);
  }
  $('start').disabled = !c.connected || live; $('stop').disabled = !c.connected || !live;
  const ev = await api('GET', '/v1/ocpp/' + c.id + '/events?days=' + $('days').value + ($('kind').value ? '&kind=' + $('kind').value : ''));
  const box = $('events'); box.replaceChildren();
  if (!ev.events.length) box.appendChild(el('div', 'Nothing in this time.', 'dim'));
  for (const e of ev.events) { box.appendChild(el('div', when(e.at), 'when')); box.appendChild(el('div', e.text, 'k-' + e.kind)); }
  const ss = await api('GET', '/v1/ocpp/' + c.id + '/sessions');
  const t = $('sessions'); t.replaceChildren();
  const h = t.createTHead().insertRow(); ['Started', 'Stopped', 'kWh', 'Why it stopped'].forEach(x => h.appendChild(el('th', x)));
  for (const x of ss.sessions) { const r = t.insertRow(); [when(x.started_at), x.stopped_at ? when(x.stopped_at) : 'charging', x.kwh ?? '', x.reason || ''].forEach(v => r.insertCell().textContent = v); }
  if (!ss.sessions.length) t.insertRow().insertCell().textContent = 'No sessions yet.';
  const conf = c.config || {}, ct = $('config'); ct.replaceChildren();
  for (const k of Object.keys(conf).sort()) { const r = ct.insertRow(); r.insertCell().textContent = k; r.insertCell().textContent = conf[k] ?? ''; }
}
async function cmd(path, body, confirmText) {
  const c = cur(); if (!c) return;
  if (confirmText && !confirm(confirmText)) return;
  $('cmsg').textContent = 'Asking the charger…';
  try { const r = await api('POST', '/v1/ocpp/' + c.id + path, body);
    $('cmsg').textContent = r.ok ? (r.action + ': ' + ((r.answer && r.answer.status) || 'done')) : (r.action + ': ' + r.error);
  } catch (e) { $('cmsg').textContent = e.message; }
  setTimeout(load, 1500);
}
$('start').onclick = () => cmd('/start');
$('stop').onclick = () => cmd('/stop');
$('soft').onclick = () => cmd('/command', { action: 'Reset', args: { type: 'Soft' } }, 'Restart the charger (soft)? A session in progress stops.');
$('hard').onclick = () => cmd('/command', { action: 'Reset', args: { type: 'Hard' } }, 'Restart the charger (hard, like switching it off and on)? A session in progress stops.');
$('trig').onclick = () => cmd('/command', { action: 'TriggerMessage', args: { message: 'StatusNotification' } });
$('cfg').onclick = () => cmd('/command', { action: 'GetConfiguration', args: {} });
$('save').onclick = async () => {
  try { await api('PUT', '/v1/ocpp/settings', { chargers: $('ids').value.split(',').map(x => x.trim()).filter(Boolean), meter_every_s: Number($('every').value) || 60, publish_ha: $('ha').checked });
    $('smsg').textContent = 'Saved.'; load(); } catch (e) { $('smsg').textContent = e.message; }
};
$('raw').parentElement.ontoggle = async e => { if (!e.target.open || !cur()) return;
  const r = await api('GET', '/v1/ocpp/' + cur().id + '/messages?limit=100');
  $('raw').textContent = r.messages.map(m => when(m.at) + (m.direction === 'in' ? '  ← ' : '  → ') + m.raw).join('\\n'); };
$('kind').onchange = $('days').onchange = $('cp').onchange = () => draw().catch(e => msg.textContent = e.message);
$('go').onclick = load;
load();
setInterval(() => { if (!document.hidden) load(); }, 20000);
</script></main></body></html>"""


@page_router.get("/charger", response_class=HTMLResponse, include_in_schema=False)
async def charger_page(request: Request) -> str:
    return themed(PAGE, "charger", request)
