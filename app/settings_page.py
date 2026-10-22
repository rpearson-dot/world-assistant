"""The Settings page: a setup wizard, what is working, keys, backups, updates and the system's own status.

It replaces the old Status page (/status now redirects here). The page holds no data; everything comes from
/v1/setup, /v1/settings/keys, /v1/places, /v1/backups, /v1/auto-deploy and /v1/status.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.pagestyle import themed

page_router = APIRouter(include_in_schema=False)

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Settings</title>
<style>
/*BASE_CSS*/
  .seg { display: flex; gap: 4px; background: var(--chip); border-radius: 12px; padding: 4px; margin-bottom: 14px; overflow-x: auto; }
  .seg button { flex: 1 0 auto; border: 0; background: none; border-radius: 9px; padding: 7px 12px; font-weight: 600; color: var(--dim); }
  .seg button.on { background: var(--card); color: var(--ink); box-shadow: 0 1px 2px rgb(0 0 0 / .12); }
  .sum { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
  .sum .big { font-size: 28px; font-weight: 750; } .sum .meter { flex: 1 1 160px; height: 10px; }
  .item { display: grid; grid-template-columns: 1fr auto; gap: 2px 12px; padding: 10px 0; border-bottom: 1px solid var(--line); }
  .item:last-child { border-bottom: 0; } .item .name { font-weight: 650; } .item .badge { white-space: nowrap; align-self: start; }
  .item .what, .item .more { grid-column: 1 / -1; } .item .what { color: var(--dim); font-size: 13px; } .item .more { font-size: 14px; }
  .group { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: var(--dim); margin: 14px 0 0; }
  .wiz { border-color: var(--accent); } .steps { display: flex; gap: 6px; margin-bottom: 12px; }
  .steps span { flex: 1; height: 4px; border-radius: 2px; background: var(--line); } .steps span.done { background: var(--accent); }
  .wiz h2 { font-size: 20px; text-transform: none; letter-spacing: 0; color: var(--ink); margin-bottom: 6px; }
  .wiz .nav { display: flex; justify-content: space-between; gap: 8px; margin-top: 16px; }
  .keycard { border: 1px solid var(--line); border-radius: 12px; padding: 12px; margin: 10px 0; }
  .keycard .top { display: flex; justify-content: space-between; gap: 8px; align-items: start; }
  .keycard .field { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
  .keycard .field input { flex: 1 1 200px; min-width: 0; } .keycard label { font-size: 13px; color: var(--dim); font-weight: 600; }
  label.switch { display: flex; gap: 10px; align-items: flex-start; font-weight: 400; cursor: pointer; }
  label.switch input { width: 40px; height: 22px; appearance: none; -webkit-appearance: none; border-radius: 999px; background: var(--line);
    position: relative; padding: 0; flex: none; margin-top: 2px; transition: background .15s; }
  label.switch input::after { content: ""; position: absolute; top: 2px; left: 2px; width: 18px; height: 18px; border-radius: 50%;
    background: #fff; box-shadow: 0 1px 2px rgb(0 0 0 / .3); transition: left .15s; }
  label.switch input:checked { background: var(--good); } label.switch input:checked::after { left: 20px; }
  .note { background: var(--warn-bg); color: var(--warn); border-radius: 10px; padding: 10px 12px; font-size: 14px; }
</style></head><body><main>
<!--NAV-->
<h1>Settings</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary">Load</button></div>
<p id="msg" class="err"></p>

<section id="wizard" class="card wiz" hidden></section>

<div class="seg" role="tablist">
  <button data-tab="setup" class="on" role="tab">Setup</button><button data-tab="keys" role="tab">Keys</button>
  <button data-tab="mqtt" role="tab">MQTT</button><button data-tab="backup" role="tab">Backups and updates</button><button data-tab="system" role="tab">System</button>
</div>

<section data-pane="setup">
  <div class="card"><div class="sum"><span class="big" id="sumn">-</span>
    <div><b>working</b><div class="dim" id="sumtext">Loading...</div></div>
    <div class="meter"><span id="sumbar" style="width:0"></span></div>
    <button id="wizgo" class="primary" type="button">Run the setup wizard</button></div></div>
  <div class="card" id="checklist"></div>
</section>

<section data-pane="keys" hidden>
  <div class="card"><h2>Keys</h2>
  <p class="dim">Every key is free and optional; a feature without its key says so. Keys saved here are kept on the
  server in its own keys folder and work straight away. They are never shown again, only whether one is set.</p>
  <p id="kwarn" class="note" hidden></p>
  <div id="keylist"></div></div>
</section>

<section data-pane="mqtt" hidden>
  <div class="card"><h2>MQTT broker</h2>
  <p class="dim">Where World Assistant sends its sensors for Home Assistant. Leave the server empty to send nothing.</p>
  <p id="mqnow"></p>
  <div class="form">
    <label>Server<span class="hint">A host name or IP address. With encryption on, use the name the certificate is for.</span>
      <input id="mqhost" autocomplete="off" placeholder="192.168.1.10 or mqtt.example.com"></label>
    <div class="row">
      <label>Port<input id="mqport" type="number" min="1" max="65535" style="width:110px"></label>
      <label>User<input id="mquser" autocomplete="off" style="width:180px"></label>
      <label>Password<input id="mqpw" type="password" autocomplete="new-password" placeholder="Leave empty to keep"></label>
    </div>
    <label class="switch"><input id="mqtls" type="checkbox"> <span><b>Encrypted (TLS)</b><br>
      <span class="hint" id="mqtlshint">The broker's certificate is checked against the server name.</span></span></label>
  </div>
  <p id="mqbundled" class="note" hidden>This is the broker bundled with World Assistant. Its password is managed by the stack
  (mqtt_password.txt), and inside the stack the connection never leaves this server, so encryption is not needed here.
  To encrypt connections from outside (a car's OVMS module), add certificates to the broker (docs/mqtt-tls.md).</p>
  <div class="row" style="margin-top:12px"><button id="mqtest" type="button">Test connection</button>
    <button id="mqsave" class="primary" type="button">Save</button><button id="mqreset" type="button">Use .env</button>
    <span id="mqmsg" class="dim"></span></div></div>
</section>

<section data-pane="backup" hidden>
  <div class="card"><h2>Backups</h2>
    <p class="dim">Your places, trips, vehicles, boards and bus history. Fuel prices and road events refill by themselves
    and are not included. Download a copy and keep it somewhere other than this server.</p>
    <div class="row"><button id="bk" class="primary" type="button">Back up now</button>
    <span id="bkmsg" class="dim"></span></div>
    <div class="scroll"><table id="bklist"></table></div></div>
  <div class="card"><h2>Automatic updates</h2>
    <p class="dim">When on, a job on this server checks every night at 04:00 for a newer signed release, backs up,
    updates, checks that every page and endpoint still answers, and puts the old version back if they do not.
    It can only apply what has been released. The one-time setup of that job is in the runbook.</p>
    <label class="row" style="font-weight:600"><input id="au" type="checkbox" style="width:auto"> Update this server automatically</label>
    <p id="aumsg" class="dim"></p></div>
</section>

<section data-pane="system" hidden>
  <div class="card"><h2>System</h2><p id="sys" class="dim"></p>
    <p class="dim">API reference: <a href="/docs">/docs</a>. For AI assistants: <code>/mcp</code>. Home Assistant: <a href="/ha">/ha</a>.</p>
    <div class="row"><button id="copy" type="button">Copy status</button><button id="refresh" type="button">Refresh</button></div></div>
  <details class="card"><summary>Raw status</summary><pre id="out">Loading...</pre></details>
</section>

<script>
const $ = id => document.getElementById(id), tok = $('t');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
const hdr = (json) => Object.assign({ Authorization: 'Bearer ' + tok.value }, json ? { 'Content-Type': 'application/json' } : {});
async function api(path, opts) {
  const r = await fetch(path, Object.assign({ headers: hdr(opts && opts.body) }, opts || {}));
  if (!r.ok) { let d = r.status; try { d = (await r.json()).detail || d; } catch (e) {} throw new Error(d); }
  return r.status === 204 ? null : r.json();
}
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const STATE = { ready: ['good', 'Working'], limited: ['warn', 'Works, better with a key'], waiting: ['warn', 'Starting'],
  off: ['', 'Off'], missing: ['warn', 'Not set up'], failing: ['bad', 'Failing'] };
const LABELS = { nh_subscription_key: 'Primary key', nh_subscription_key_secondary: 'Secondary key (optional)',
  fuel_finder_client_id: 'Client ID', fuel_finder_client_secret: 'Client secret', skiddle_api_key: 'Skiddle API key',
  ticketmaster_api_key: 'Ticketmaster consumer key', tfl_app_key: 'App key', nr_access_token: 'Consumer key',
  bods_api_key: 'API key', pollen_api_key: 'API key', octopus_api_key: 'API key' };
let setup = null, keys = null;

function ago(iso) {
  if (!iso) return '';
  const m = Math.round((Date.now() - new Date(iso)) / 60000);
  return m < 1 ? 'just now' : m < 120 ? m + ' min ago' : m < 2880 ? Math.round(m / 60) + ' h ago' : Math.round(m / 1440) + ' days ago';
}
function badge(state) { const [c, w] = STATE[state] || ['', state]; return el('span', 'badge ' + c, w); }
function details(x) {
  const bits = [];
  if (x.state === 'failing' && x.error) bits.push(x.error);
  else if (x.last_ok) bits.push('Updated ' + ago(x.last_ok));
  if (x.note) bits.push(x.note);
  return bits.map(b => /[.!?]$/.test(b) ? b : b + '.').join(' ');
}
function item(x) {
  const d = el('div', 'item');
  d.append(el('div', 'name', x.name), badge(x.state), el('div', 'what', x.unlocks));
  const more = el('div', 'more', details(x));
  if (x.link) {
    const a = el('a', null, x.link.startsWith('/') ? ' Open' : ' Get a key'); a.href = x.link;
    if (!x.link.startsWith('/')) { a.target = '_blank'; a.rel = 'noopener'; }
    if (x.link.startsWith('/') || ['missing', 'limited', 'off'].includes(x.state)) more.append(a);
  }
  if (x.keys && x.keys.length && x.state !== 'ready') {
    const b = el('button', 'link', 'Add the key here'); b.type = 'button'; b.onclick = () => tab('keys'); more.append(' ', b);
  }
  d.append(more);
  return d;
}
function renderSetup() {
  const all = [...setup.you, ...setup.keys, ...setup.free], n = all.filter(x => x.state === 'ready' || x.state === 'limited').length;
  $('sumn').textContent = n + ' / ' + all.length;
  $('sumbar').style.width = Math.round(100 * n / all.length) + '%';
  $('sumtext').textContent = setup.local ? 'You are on the home network, so no token is needed here.' : 'Signed in with the token.';
  const c = $('checklist'); c.replaceChildren();
  for (const [title, list] of [['You', setup.you], ['Sources that need a free key', setup.keys], ['Sources that need no key', setup.free]]) {
    c.append(el('div', 'group', title), ...list.map(item));
  }
}
function keyField(name, onsaved) {
  const k = (keys.keys.find(x => x.name === name) || {}), wrap = el('div');
  const lab = el('label', null, (LABELS[name] || name) + (k.set ? ' (set' + (k.source === 'settings' ? ' here' : ' in the ' + k.source) + ')' : ''));
  const f = el('div', 'field'), inp = el('input'); inp.type = 'password'; inp.autocomplete = 'off'; inp.placeholder = k.set ? 'Paste a new key to replace it' : 'Paste the key';
  inp.disabled = !keys.writable;
  const save = el('button', 'primary', 'Save'); save.type = 'button'; save.disabled = !keys.writable;
  const st = el('span', 'dim');
  save.onclick = async () => {
    if (!inp.value.trim()) return;
    try { await api('/v1/settings/keys/' + name, { method: 'PUT', body: JSON.stringify({ value: inp.value }) });
      inp.value = ''; st.textContent = 'Saved.'; await refreshKeys(); if (onsaved) onsaved(); }
    catch (e) { st.textContent = e.message; }
  };
  f.append(inp, save);
  if (k.source === 'settings') {
    const del = el('button', null, 'Forget'); del.type = 'button';
    del.onclick = async () => { try { await api('/v1/settings/keys/' + name, { method: 'DELETE' }); await refreshKeys(); if (onsaved) onsaved(); }
      catch (e) { st.textContent = e.message; } };
    f.append(del);
  }
  f.append(st); wrap.append(lab, f); return wrap;
}
function keyCard(src, onsaved) {
  const c = el('div', 'keycard'), top = el('div', 'top'), left = el('div');
  left.append(el('div', 'name', src.name), el('div', 'dim', src.unlocks));
  top.append(left, badge(src.state)); c.append(top);
  if (src.note) c.append(el('div', 'dim', src.note));
  if (src.link) { const a = el('a', null, 'Get a key'); a.href = src.link; a.target = '_blank'; a.rel = 'noopener'; c.append(a); }
  for (const name of src.keys) c.append(keyField(name, onsaved));
  return c;
}
async function refreshKeys() {
  [keys, setup] = await Promise.all([api('/v1/settings/keys'), api('/v1/setup')]);
  $('kwarn').hidden = keys.writable; $('kwarn').textContent = keys.writable ? '' :
    'Keys cannot be saved from here yet: ' + keys.reason + '. You can still put them in the secrets folder on the server (scripts/setup.sh).';
  $('keylist').replaceChildren(...setup.keys.map(s => keyCard(s, renderAll)));
  renderSetup();
}
function renderAll() { renderSetup(); if (!$('wizard').hidden) wizard(wizStep); }

// --- the wizard -------------------------------------------------------------------------------------
let wizStep = 0;
const STEPS = 5;
function wizard(step) {
  wizStep = step;
  const w = $('wizard'); w.hidden = false; w.replaceChildren();
  const dots = el('div', 'steps'); for (let i = 0; i < STEPS; i++) dots.append(el('span', i <= step ? 'done' : ''));
  w.append(dots);
  const nav = el('div', 'nav'), back = el('button', null, 'Back'), next = el('button', 'primary', step === STEPS - 1 ? 'Finish' : 'Next');
  back.type = next.type = 'button'; back.disabled = step === 0;
  back.onclick = () => wizard(step - 1);
  next.onclick = () => step === STEPS - 1 ? wizDone() : wizard(step + 1);
  const skip = el('button', 'link', 'Close'); skip.type = 'button'; skip.onclick = wizDone;
  if (step === 0) {
    w.append(el('h2', null, 'Set up World Assistant'),
      el('p', null, 'A few minutes: where home is, the free keys for the things you want, and Home Assistant if you use it. Everything is optional and you can come back any time.'));
  } else if (step === 1) {
    const home = setup.you.find(x => x.id === 'home');
    w.append(el('h2', null, 'Where is home?'), el('p', 'dim', 'Fuel, events, pollen and the map start from here. Only this server knows it.'));
    if (home.state === 'ready') w.append(el('p', 'ok', 'Home is set. You can change it on the Places page.'));
    const f = el('div', 'field row'), pc = el('input'); pc.placeholder = 'Postcode, e.g. KT4 7AA'; pc.autocomplete = 'postal-code';
    const save = el('button', 'primary', home.state === 'ready' ? 'Change home' : 'Save home'); save.type = 'button';
    const st = el('span', 'dim');
    save.onclick = async () => {
      try { await api('/v1/places/home', { method: 'PUT', body: JSON.stringify({ name: 'Home', kind: 'home', postcode: pc.value.trim() }) });
        st.textContent = 'Saved.'; setup = await api('/v1/setup'); renderSetup(); }
      catch (e) { st.textContent = e.message; }
    };
    f.append(pc, save, st); w.append(f);
  } else if (step === 2) {
    w.append(el('h2', null, 'Free keys'), el('p', 'dim', 'Each one unlocks something. Open "Get a key", sign up, paste it here and Save. Skip any you do not want.'));
    if (!keys.writable) w.append(el('p', 'note', 'Keys cannot be saved from the browser on this server yet (' + keys.reason + '). Use scripts/setup.sh on the server instead.'));
    w.append(...setup.keys.map(s => keyCard(s, renderAll)));
  } else if (step === 3) {
    const ha = setup.you.find(x => x.id === 'ha');
    w.append(el('h2', null, 'Home Assistant'), badge(ha.state), el('p', null, details(ha) || 'Publishing sensors to Home Assistant.'));
    const a = el('a', null, 'Open the Home Assistant setup page'); a.href = '/ha';
    w.append(el('p', 'dim', 'The page gives you the packages, the secrets.yaml lines and the dashboards for this version.'), a);
    if (ha.state === 'off') w.append(el('p', 'dim', 'No broker is set yet. Skip this if you do not use Home Assistant.'));
    const set = el('button', null, 'Set the MQTT broker'); set.type = 'button'; set.onclick = () => { wizDone(); tab('mqtt'); };
    w.append(el('p'), set);
  } else {
    const all = [...setup.you, ...setup.keys, ...setup.free], n = all.filter(x => x.state === 'ready' || x.state === 'limited').length;
    w.append(el('h2', null, 'All set'), el('p', null, n + ' of ' + all.length + ' things are working. Some sources take a few minutes to load the first time; the Setup list shows them arrive.'));
    const go = el('div', 'row');
    for (const [t, h] of [['Open the map', '/map'], ['Buses', '/bus'], ['Fuel', '/fuel']]) { const a = el('a', null, t); a.href = h; go.append(a); }
    w.append(go);
  }
  nav.append(back, el('span'), skip, next); w.append(nav);
}
function wizDone() { $('wizard').hidden = true; try { localStorage.setItem('wa_wizard', 'done'); } catch (e) {} }
$('wizgo').onclick = () => wizard(0);

// --- tabs, backups, updates, system ------------------------------------------------------------------
function tab(name) {
  document.querySelectorAll('.seg button').forEach(b => b.classList.toggle('on', b.dataset.tab === name));
  document.querySelectorAll('[data-pane]').forEach(p => p.hidden = p.dataset.pane !== name);
  try { history.replaceState(null, '', '#' + name); } catch (e) {}
}
document.querySelectorAll('.seg button').forEach(b => b.onclick = () => tab(b.dataset.tab));
const AU_WORDS = { updated: 'Updated', unchanged: 'Already up to date', refused: 'Not applied',
  rolled_back: 'Update failed its checks; the previous version is running again', failed: 'Needs attention' };
async function auLoad() {
  try {
    const d = await api('/v1/auto-deploy'), l = d.last_run; $('au').checked = d.enabled;
    $('aumsg').textContent = 'Running ' + d.running.version + '. ' + (l ? 'Last run ' + new Date(l.reported).toLocaleString() + ': '
      + (AU_WORDS[l.result] || l.result) + (l.message ? ' (' + l.message + ')' : '') + '.' : 'The nightly job has not reported yet.');
  } catch (e) { $('aumsg').textContent = e.message; }
}
$('au').onchange = async ev => {
  try { await api('/v1/auto-deploy', { method: 'PUT', body: JSON.stringify({ enabled: ev.target.checked }) }); await auLoad(); }
  catch (e) { ev.target.checked = !ev.target.checked; $('aumsg').textContent = 'Could not save: ' + e.message; }
};
async function bkLoad() {
  try {
    const d = await api('/v1/backups');
    $('bklist').replaceChildren(...d.backups.map(b => {
      const tr = document.createElement('tr');
      tr.insertCell().textContent = b.name; tr.insertCell().textContent = Math.max(1, Math.round(b.size_bytes / 1024)) + ' KB';
      const btn = el('button', null, 'Download'); btn.type = 'button'; btn.onclick = () => bkDownload(b.name); tr.insertCell().append(btn);
      return tr;
    }));
    $('bkmsg').textContent = d.backups.length ? 'Keeping the latest ' + d.keep + '.' : 'No backups yet.';
  } catch (e) { $('bkmsg').textContent = e.message; }
}
async function bkDownload(name) {
  const r = await fetch('/v1/backups/' + encodeURIComponent(name), { headers: hdr() });
  if (!r.ok) { $('bkmsg').textContent = 'Download failed: ' + r.status; return; }
  const url = URL.createObjectURL(await r.blob()), a = document.createElement('a');
  a.href = url; a.download = name; document.body.append(a); a.click(); a.remove(); URL.revokeObjectURL(url);
}
$('bk').onclick = async () => {
  $('bkmsg').textContent = 'Backing up...';
  try { const b = await api('/v1/backups', { method: 'POST' }); await bkLoad(); $('bkmsg').textContent = 'Saved ' + b.name + '.'; }
  catch (e) { $('bkmsg').textContent = e.message; }
};
async function sysLoad() {
  const d = await api('/v1/status');
  $('out').textContent = JSON.stringify(d, null, 2);
  $('sys').textContent = 'Version ' + d.version + ' (' + String(d.commit).slice(0, 7) + '). Worker last seen: '
    + (d.worker_heartbeat ? ago(d.worker_heartbeat) : 'not yet') + '.';
}
$('copy').onclick = () => {
  const t = $('out').textContent;
  if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(t); return; }
  const ta = document.createElement('textarea'); ta.value = t; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
};
$('refresh').onclick = () => load();

// --- MQTT --------------------------------------------------------------------------------------------------
let mq = null;
function mqForm() {
  return { host: $('mqhost').value.trim(), port: parseInt($('mqport').value, 10) || ($('mqtls').checked ? 8883 : 1883),
    user: $('mquser').value.trim(), tls: $('mqtls').checked, password: $('mqpw').value || null };
}
function mqShow(d) {
  mq = d;
  $('mqhost').value = d.host; $('mqport').value = d.port; $('mquser').value = d.user; $('mqtls').checked = d.tls; $('mqpw').value = '';
  const WHERE = { settings: 'saved here', file: 'in mqtt_password.txt', env: 'in the environment' };
  $('mqpw').placeholder = d.password_set ? 'Set ' + (WHERE[d.password_source] || 'in the ' + d.password_source) + ': leave empty to keep' : 'None set';
  $('mqbundled').hidden = !d.bundled; $('mqpw').disabled = d.bundled;
  const lp = d.last_publish, where = d.host ? d.host + ':' + d.port : 'nowhere';
  const state = !d.active ? 'Not publishing: no server set.'
    : lp && lp.ok ? 'Publishing to ' + where + (d.tls ? ', encrypted' : ', not encrypted') + '. Last sent ' + ago(lp.at) + '.'
    : lp ? 'Could not publish to ' + where + ': ' + lp.error : 'Publishing to ' + where + '; the first send is due within a few minutes.';
  $('mqnow').textContent = state + (d.source === 'settings' ? ' (Settings here, which override .env.)' : ' (From .env.)');
  $('mqnow').className = lp && !lp.ok && d.active ? 'err' : 'dim';
  $('mqreset').disabled = d.source !== 'settings';
}
// switching encryption moves the port between the usual pair, unless a custom one was chosen
$('mqtls').onchange = () => {
  const p = $('mqport').value;
  if ($('mqtls').checked && (p === '' || p === '1883')) $('mqport').value = 8883;
  if (!$('mqtls').checked && (p === '' || p === '8883')) $('mqport').value = 1883;
};
async function mqLoad() { try { mqShow(await api('/v1/settings/mqtt')); } catch (e) { $('mqmsg').textContent = e.message; } }
$('mqtest').onclick = async () => {
  $('mqmsg').textContent = 'Connecting...';
  try { const r = await api('/v1/settings/mqtt/test', { method: 'POST', body: JSON.stringify(mqForm()) });
    $('mqmsg').textContent = r.ok ? 'Connected.' : 'Could not connect: ' + r.error; $('mqmsg').className = r.ok ? 'ok' : 'err'; }
  catch (e) { $('mqmsg').textContent = e.message; $('mqmsg').className = 'err'; }
};
$('mqsave').onclick = async () => {
  try { mqShow(await api('/v1/settings/mqtt', { method: 'PUT', body: JSON.stringify(mqForm()) }));
    $('mqmsg').textContent = 'Saved. The worker uses it from its next send (every few minutes).'; $('mqmsg').className = 'dim'; refreshKeys(); }
  catch (e) { $('mqmsg').textContent = e.message; $('mqmsg').className = 'err'; }
};
$('mqreset').onclick = async () => {
  try { mqShow(await api('/v1/settings/mqtt', { method: 'DELETE' })); $('mqmsg').textContent = 'Back to the settings in .env.'; $('mqmsg').className = 'dim'; refreshKeys(); }
  catch (e) { $('mqmsg').textContent = e.message; }
};

async function load() {
  $('msg').textContent = '';
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  try { await refreshKeys(); } catch (e) { $('msg').textContent = 'Could not load: ' + e.message; return; }
  bkLoad(); auLoad(); mqLoad(); sysLoad().catch(e => $('sys').textContent = e.message);
  let done = false; try { done = localStorage.getItem('wa_wizard') === 'done'; } catch (e) {}
  if (!done && setup.you.find(x => x.id === 'home').state !== 'ready') wizard(0);
}
$('go').onclick = load;
const TABS = ['setup', 'keys', 'mqtt', 'backup', 'system'];
const want = location.hash.slice(1); if (TABS.includes(want)) tab(want);
window.addEventListener('hashchange', () => { const h = location.hash.slice(1); if (TABS.includes(h)) tab(h); });
if (tok.value) load();
</script></main></body></html>"""


@page_router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> str:
    """The page holds no data; it calls /v1/setup, /v1/settings/keys and friends."""
    return themed(PAGE, "settings", request)


@page_router.get("/status")
async def old_status_page() -> RedirectResponse:
    """The Status page is now Settings (System has the raw status)."""
    return RedirectResponse("/settings#system", status_code=308)
