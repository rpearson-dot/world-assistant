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
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 999px; padding: 3px; background: var(--chip); gap: 2px; }
.seg button { border: 0; border-radius: 999px; padding: 10px 26px; font-size: 1.05em; font-weight: 650; background: transparent; color: var(--ink); }
.seg button[aria-pressed="true"] { background: var(--accent); color: var(--on-accent); }
.seg button[data-m="off"][aria-pressed="true"] { background: var(--bad); }
details.box > summary { cursor: pointer; font-weight: 650; padding: 4px 0; }
.top { display: flex; flex-wrap: wrap; align-items: center; gap: 18px 28px; }
.batt { display: flex; align-items: center; gap: 10px; } .batt b { font-size: 1.6em; font-variant-numeric: tabular-nums; }
.now b { display: block; font-size: 1.6em; } .now span { display: block; }
@keyframes fill { 0%, 100% { opacity: .25; } 50% { opacity: 1; } } .filling { animation: fill 1.6s ease-in-out infinite; }
pre.raw { white-space: pre-wrap; word-break: break-all; font-size: .8em; max-height: 360px; overflow: auto; }
</style></head><body><main>
<!--NAV-->
<h1>Charger</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<select id="cp" aria-label="Charger" hidden></select><button id="go" class="primary" type="button">Load</button></div>
<p id="msg" class="err"></p>

<div class="card hero" id="none" hidden><div class="say">No charger has connected yet.</div>
  <div class="dim">In the charger's settings (its app or web page), set the central system (OCPP server) address to
  <b id="addr"></b><b class="cpid">charge point ID</b>, OCPP 1.6J.
  The <b>charge point ID</b> is the name the charger gives itself, set in the same settings next to the address
  (keep the one it has: Home Assistant's OCPP integration shows it, and it can be put under Chargers allowed to
  connect below). The address is the server's address on your home
  network, never a Tailscale one, because the charger is on your home network. The charger can talk to only one
  central system, so this replaces Home Assistant's OCPP integration.</div></div>

<section id="view" hidden>
<div class="card">
  <div class="top">
  <div class="seg" role="group" aria-label="Charging">
    <button type="button" data-m="off" aria-pressed="false">Off</button><button type="button" data-m="on" aria-pressed="false">On</button><button type="button" data-m="auto" aria-pressed="false">Auto</button>
  </div>
  <div class="batt"><svg id="batt" viewBox="0 0 132 52" width="132" height="52" role="img" aria-label="Car battery"></svg><b id="pct">–</b></div>
  <div class="now"><b id="status">–</b><span id="sub" class="dim"></span></div>
  </div>
  <p class="dim" id="modewhy"></p><p id="cmsg" class="dim"></p>
</div>
<div class="card"><h2>What happened</h2>
  <div class="row"><label>Show <select id="kind"><option value="">everything</option><option value="boot">restarts</option>
    <option value="disconnected">drops</option><option value="fault">faults</option><option value="status">status changes</option>
    <option value="command">commands</option><option value="session">sessions</option></select></label>
    <label>Over <select id="days"><option value="1">a day</option><option value="7" selected>a week</option><option value="30">a month</option><option value="365">a year</option></select></label></div>
  <div class="ev" id="events"></div></div>
<div class="card"><h2>Sessions</h2><table id="sessions"></table></div>
<div class="card"><details class="box"><summary>Settings and tools</summary>
<p class="dim" id="seen"></p>
<div class="stats" id="stats"></div>
<h3>Settings</h3>
  <div class="form">
    <label>Chargers allowed to connect <input id="ids" placeholder="e.g. U1HKP (blank: any on the home network)">
      <span class="hint">Separate several with commas.</span></label>
    <div class="row"><label>Ask for meter readings while charging every <input id="every" type="number" min="15" max="900" style="width:6em"> s</label>
      <label>This server's home network address <input id="srv" placeholder="e.g. 10.0.2.29"></label>
      <label class="tick"><input id="ha" type="checkbox"> Show the charger in Home Assistant</label>
      <button id="save" type="button">Save</button></div>
    <p class="dim">The address for a charger: <b id="addr2"></b><b class="cpid">charge point ID</b> (the charger's own
    name for itself, set next to the address in its settings)</p>
    <p id="smsg" class="dim"></p>
  </div>
<h3>Tools</h3>
  <div class="row"><button id="cfg" type="button">Read its configuration</button><button id="soft" type="button">Restart (soft)</button>
    <button id="hard" type="button">Restart (hard)</button><button id="trig" type="button">Ask for its status</button></div>
  <table id="config"></table>
  <details><summary>The last 100 messages, as sent</summary><pre class="raw" id="raw"></pre></details>
</details></div>
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
    const ids = data.settings.chargers || [];
    if (Array.isArray(ids) && ids.length === 1) document.querySelectorAll('.cpid').forEach(x => x.textContent = ids[0]);
    const keep = $('cp').value; $('cp').replaceChildren(...data.chargers.map(c => new Option(c.id, c.id)));
    if (keep && data.chargers.some(c => c.id === keep)) $('cp').value = keep;
    $('none').hidden = data.chargers.length > 0; $('view').hidden = !data.chargers.length;
    if (document.activeElement.tagName !== 'INPUT') { $('ids').value = data.settings.chargers.join(', '); $('every').value = data.settings.meter_every_s; $('ha').checked = data.settings.publish_ha; $('srv').value = data.settings.server_address || ''; }
    if (data.chargers.length) await draw();
  } catch (e) { msg.textContent = e.message; }
}
let soc = null;  // the car's charge, from its OVMS module when that module is on
function battery(charging) {
  const svg = $('batt'), NS = 'http://www.w3.org/2000/svg', mk = (t, a) => { const e = document.createElementNS(NS, t); for (const k in a) e.setAttribute(k, a[k]); return e; };
  svg.replaceChildren();
  svg.append(mk('rect', { x: 2, y: 2, width: 118, height: 48, rx: 8, fill: 'none', stroke: 'var(--ink)', 'stroke-width': 3 }),
             mk('rect', { x: 122, y: 16, width: 8, height: 20, rx: 2, fill: 'var(--ink)' }));
  const full = soc == null ? 0 : Math.ceil(soc / 20), low = soc != null && soc <= 20;
  for (let i = 0; i < 5; i++) {
    const r = mk('rect', { x: 8 + i * 22.4, y: 8, width: 19, height: 36, rx: 3,
      fill: i < full ? (low ? 'var(--bad)' : 'var(--good)') : 'var(--chip)' });
    if (charging && (i === full || (full === 5 && i === 4))) { r.setAttribute('fill', 'var(--good)'); r.classList.add('filling'); }
    svg.append(r);
  }
  $('pct').textContent = soc == null ? '–' : Math.round(soc) + '%';
  svg.setAttribute('aria-label', soc == null ? 'Car charge not known' : 'Car charged to ' + Math.round(soc) + '%');
}
async function draw() {
  try { const o = await api('GET', '/v1/ovms'); const car = o.cars.find(x => x.soc != null); soc = car ? car.soc : null; } catch (e) { soc = null; }
  const c = cur(); if (!c) return;
  const m = c.meter || {}, s = c.session, live = s && !s.stopped_at;
  const b = c.boot || {};
  $('seen').textContent = (b.chargePointVendor ? b.chargePointVendor + ' ' + (b.chargePointModel || '') + ' · ' : '') + 'last heard ' + ago(c.last_seen) +
    (c.booted_at ? ' · started up ' + when(c.booted_at) : '') + (live ? ' · session ' + s.transaction_id + ' since ' + when(s.started_at) : '');
  $('cp').hidden = data.chargers.length < 2;
  const kwh = live && s.meter_start_wh != null && (s.last_meter_wh ?? m.energy_wh) != null ? (((s.last_meter_wh ?? m.energy_wh) - s.meter_start_wh) / 1000).toFixed(2) : null;
  const st = $('stats'); st.replaceChildren();
  for (const [k, v] of [['Power', c.status === 'Charging' && m.power_w != null ? (m.power_w / 1000).toFixed(2) + ' kW' : '0 kW'],
      ['This session', kwh != null ? kwh + ' kWh' : '–'], ['Meter', m.energy_wh != null ? (m.energy_wh / 1000).toFixed(1) + ' kWh' : '–'],
      ['Restarts this week', c.week.boots], ['Drops this week', c.week.drops], ['Faults this week', c.week.faults]]) {
    const d = el('div', k, 'dim'); d.appendChild(el('b', String(v))); st.appendChild(d);
  }
  const fault = c.error_code && c.error_code !== 'NoError';
  const word = !c.connected ? 'Offline' : fault || c.status === 'Faulted' ? 'Fault' : c.status === 'Charging' ? 'Charging'
    : c.status === 'Available' ? 'Unplugged' : c.status === 'Unavailable' ? 'Switched off'
    : ['Preparing', 'SuspendedEV', 'SuspendedEVSE', 'Finishing', 'Reserved'].includes(c.status) ? 'Connected' : (c.status || '–');
  $('status').textContent = word;
  $('status').style.color = word === 'Charging' ? 'var(--good)' : word === 'Fault' || word === 'Offline' ? 'var(--bad)' : '';
  $('sub').textContent = word === 'Charging' && m.power_w != null ? (m.power_w / 1000).toFixed(1) + ' kW' + (kwh != null ? ' · ' + kwh + ' kWh added' : '')
    : word === 'Fault' ? c.error_code : word === 'Connected' && c.status === 'SuspendedEV' ? 'the car is not taking charge' : '';
  battery(c.status === 'Charging');
  const mode = data.settings.mode || 'auto';
  document.querySelectorAll('.seg button').forEach(b => { b.setAttribute('aria-pressed', String(b.dataset.m === mode)); b.disabled = false; });
  $('modewhy').textContent = { off: 'Off: the charger will not charge, even if the car is plugged in.',
    on: 'On: charges whenever the car is plugged in, whatever the plan says.',
    auto: 'Auto: Home Assistant’s charging plan starts and stops it (cheap hours, free sessions, your target).' }[mode] +
    (c.connected ? '' : ' The charger is not connected, so this applies when it next connects.');
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
document.querySelectorAll('.seg button').forEach(b => b.onclick = async () => {
  document.querySelectorAll('.seg button').forEach(x => x.disabled = true); $('cmsg').textContent = 'Setting ' + b.textContent + '…';
  try {
    const r = await api('PUT', '/v1/ocpp/mode', { mode: b.dataset.m });
    const bad = Object.entries(r.applied).filter(([, v]) => !v.ok);
    $('cmsg').textContent = bad.length ? 'Saved; ' + bad.map(([k, v]) => k + ': ' + v.error).join('; ') : 'Done.';
  } catch (e) { $('cmsg').textContent = e.message; }
  load();
});
$('soft').onclick = () => cmd('/command', { action: 'Reset', args: { type: 'Soft' } }, 'Restart the charger (soft)? A session in progress stops.');
$('hard').onclick = () => cmd('/command', { action: 'Reset', args: { type: 'Hard' } }, 'Restart the charger (hard, like switching it off and on)? A session in progress stops.');
$('trig').onclick = () => cmd('/command', { action: 'TriggerMessage', args: { message: 'StatusNotification' } });
$('cfg').onclick = () => cmd('/command', { action: 'GetConfiguration', args: {} });
$('save').onclick = async () => {
  try { await api('PUT', '/v1/ocpp/settings', { chargers: $('ids').value.split(',').map(x => x.trim()).filter(Boolean), meter_every_s: Number($('every').value) || 60, publish_ha: $('ha').checked, server_address: $('srv').value.trim() });
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
