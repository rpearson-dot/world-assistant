"""The Pi-hole page (/pihole, In the home → Networking → Pi-hole): the DNS picture — which devices look up the most,
what gets past your Pi-hole lists that does not serve them, and one-tap blocks (with Undo). Holds no data; it reads
/v1/network and /v1/pihole and never builds HTML from anything but counts and the suggestions those return."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

page_router = APIRouter()

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant Pi-hole</title>
<style>
/*BASE_CSS*/
.wrap { overflow-x: auto; }
table.t { width: 100%; border-collapse: collapse; font-size: .9em; } table.t td, table.t th { padding: 5px 6px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
table.t td.n, table.t th.n { text-align: right; } table.t tr.dev { cursor: pointer; } table.t tr.dev:hover td { background: var(--chip); }
.chip { display: inline-block; background: var(--chip); border-radius: 999px; padding: 1px 8px; margin: 1px; font-size: .85em; }
.chip.loud { border: 1px solid var(--bad); color: var(--bad); font-weight: 600; }
.why { color: var(--dim); font-size: .85em; } .breaks { color: var(--bad); font-size: .85em; }
</style></head><body><main>
<!--NAV-->
<h1>Pi-hole</h1>
<p class="dim">Who looks up the most, what gets past your lists that does not serve them, and one-tap blocks. Your Pi-hole groups are never changed on their own.</p>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary" type="button">Load</button></div>
<p id="msg" class="err"></p>
<div class="card" id="offcard" hidden><p class="dim">Pi-hole is switched off. Turn it on in Settings → Setup (In the home), then set its address and app password below.</p></div>
<div class="card"><h2>Devices by lookups</h2><p class="dim small" id="say"></p><div class="wrap"><table class="t" id="devs"></table></div></div>
<div class="card" id="one" hidden>
  <h2 id="oname"></h2><p class="dim small" id="oinfo"></p>
  <h3>Suggested blocks</h3><p class="dim small" id="ssay"></p>
  <div id="sugg"></div>
  <div class="row" id="sact" hidden>
    <label>Block for <select id="target"></select></label>
    <button id="add" class="primary" type="button">Add to Pi-hole</button> <button id="copy" type="button">Copy the list</button></div>
  <p id="smsg" class="dim"></p>
  <div id="added"></div>
  <details><summary>Keep (it needs these)</summary><div id="keep" class="small"></div></details>
  <h3>Most looked up (24 hours)</h3><div id="top" class="small"></div></div>
<div class="card" id="groupcard" hidden><h2>Your Pi-hole groups</h2><div id="groups"></div></div>
<details class="card"><summary>Settings</summary>
  <label>Pi-hole's address on your network <input id="phurl" placeholder="http://10.0.2.49:8080"></label>
  <label>Pi-hole's app password <input id="phkey" type="password" autocomplete="off" placeholder="Paste it to save or replace it"></label>
  <p class="dim small">In Pi-hole: Settings → Web interface / API → Configure app password. Kept with your other keys
  (Settings → Keys) and never shown again. <span id="phpw"></span></p>
  <p><button id="save" class="primary" type="button">Save</button> <button id="test" type="button">Test Pi-hole</button> <span id="setmsg" class="dim"></span></p>
</details>
<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
async function api(path, opts) {
  const r = await fetch(path, { ...(opts || {}), headers: { Authorization: 'Bearer ' + tok.value, 'Content-Type': 'application/json' } });
  if (!r.ok) { let d = r.status; try { d = (await r.json()).detail; } catch (e) {} throw new Error(typeof d === 'string' ? d : JSON.stringify(d)); }
  return r.json();
}
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const when = s => s ? new Date(s).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '–';
let current = null, groupsList = [], piholeOn = false, loud = 0;
function deviceRow(d) {
  const tr = el('tr', null, 'dev'); tr.onclick = () => open(d.mac);
  const n = el('td'); n.append(el('div', d.label)); n.append(el('div', d.mac + (d.ip ? ' · ' + d.ip : ''), 'why'));
  if (loud && d.lookups >= loud) n.append(el('span', 'talks a lot', 'chip loud'));
  (d.groups || []).forEach(g => n.append(el('span', g, 'chip')));
  tr.append(n, el('td', String(d.lookups), 'n'), el('td', String(d.blocked), 'n'));
  return tr;
}
async function load() {
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  msg.textContent = '';
  try {
    try { const ph = await api('/v1/pihole/settings'); piholeOn = true; $('phurl').value = ph.pihole_url || '';
      $('phpw').textContent = ph.pihole_password_set ? 'It is set.' : 'It is not set yet.'; }
    catch (e) { piholeOn = false; }
    $('offcard').hidden = piholeOn; $('phurl').disabled = $('phkey').disabled = $('test').disabled = !piholeOn;
    const d = await api('/v1/network/devices'), t = $('devs'); t.replaceChildren();
    const devs = [...d.devices].sort((a, b) => b.lookups - a.lookups);
    const top = devs.slice(0, 3).reduce((m, x) => Math.max(m, x.lookups), 0); loud = top >= 2000 ? Math.max(2000, top * 0.6) : 0;
    if (d.pihole) { try { const g = (await api('/v1/pihole/device-groups')).groups; devs.forEach(x => x.groups = g[x.mac]); } catch (e) {} }
    const h = el('tr'); [['Device'], ['Lookups today', 'n'], ['Blocked today', 'n']].forEach(([x, c]) => h.append(el('th', x, c))); t.append(h);
    devs.forEach(x => t.append(deviceRow(x)));
    $('say').textContent = devs.length ? devs.length + ' devices, busiest first. Choose one to see what it looks up and what to block.' :
      'No devices yet: set up Pi-hole below, or point the UniFi gateway log here.';
    if (d.pihole) { try { const g = await api('/v1/pihole/groups'); groupsList = g.groups; $('groupcard').hidden = !g.groups.length; const box = $('groups'); box.replaceChildren();
      g.groups.forEach(x => { const p = el('p'); p.append(el('b', x.name + ': '), document.createTextNode(x.devices.length + ' devices, ' + x.lookups_week + ' lookups got past your lists this week' + (x.devices.length ? ' (' + x.devices.join(', ') + ')' : ''))); box.append(p); }); } catch (e) {} }
  } catch (e) { msg.textContent = e.message; }
}
async function open(mac) {
  current = mac; $('one').hidden = false; $('smsg').textContent = '';
  const d = await api('/v1/network/devices/' + encodeURIComponent(mac));
  $('oname').textContent = d.device.label; $('oinfo').textContent = mac + (d.device.ip ? ' · ' + d.device.ip : '') + ' · first seen ' + when(d.device.first_seen);
  const topb = $('top'); topb.replaceChildren(); d.top.forEach(x => topb.append(el('span', x.dest + ' ' + x.n + (x.action === 'block' ? ' (blocked)' : ''), 'chip')));
  const ad = $('added'); ad.replaceChildren();
  if (d.added_blocks.length) { const p = el('p', 'Added to Pi-hole by World Assistant: ' + d.added_blocks.map(b => b.domain).join(', ') + ' ', 'small');
    const u = el('button', 'Undo all'); u.type = 'button'; u.onclick = async () => { try { await api('/v1/pihole/devices/' + encodeURIComponent(mac) + '/unblock', { method: 'POST', body: '{}' }); open(mac); } catch (e) { $('smsg').textContent = e.message; } };
    p.append(u); ad.append(p); }
  if (!piholeOn) { $('ssay').textContent = 'Turn on Pi-hole to see suggested blocks.'; $('sugg').replaceChildren(); $('sact').hidden = true; $('keep').replaceChildren(); return; }
  const s = await api('/v1/pihole/devices/' + encodeURIComponent(mac) + '/suggestions'), box = $('sugg'); box.replaceChildren();
  $('ssay').textContent = (s.learning ? 'Still learning (watched ' + s.watched_days + ' of 7 days): these may change. ' : '') +
    (s.suggested.length ? s.suggested.length + ' domains got past your Pi-hole lists that look like they do not serve this device (' + s.suggested_lookups + ' lookups).' : 'Nothing to suggest: what gets past your lists looks like what it needs.');
  s.suggested.forEach(x => { const l = el('label', null, 'row'); const c = el('input'); c.type = 'checkbox'; c.style.width = 'auto'; c.value = x.domain; c.checked = !x.may_break;
    const tt = el('div'); tt.append(el('div', x.domain + ' · ' + x.lookups + ' lookups (' + x.per_day + ' a day)'), el('div', x.why.join('; '), 'why'));
    if (x.may_break) tt.append(el('div', 'May break: ' + x.may_break, 'breaks')); l.append(c, tt); box.append(l); });
  $('sact').hidden = !s.suggested.length;
  const sel = $('target'); sel.replaceChildren(); const o = el('option', 'this device only'); o.value = ''; sel.append(o);
  groupsList.forEach(g => { const x = el('option', 'everything in ' + g.name); x.value = g.id; sel.append(x); });
  const k = $('keep'); k.replaceChildren(); s.keep.forEach(x => k.append(el('span', x.domain, 'chip')));
}
function ticked() { return [...$('sugg').querySelectorAll('input:checked')].map(i => i.value); }
let armed = false;
$('add').onclick = async () => {
  const doms = ticked(); if (!doms.length) { $('smsg').textContent = 'Tick at least one.'; return; }
  const tgt = $('target').value, words = tgt ? 'every device in ' + $('target').selectedOptions[0].textContent.replace('everything in ', '') : 'this device only';
  if (!armed) { armed = true; $('add').textContent = 'Press again to block ' + doms.length + ' domains for ' + words; return; }
  armed = false; $('add').textContent = 'Add to Pi-hole';
  try { const r = await api('/v1/pihole/devices/' + encodeURIComponent(current) + '/block', { method: 'POST', body: JSON.stringify({ domains: doms, group_id: tgt ? Number(tgt) : null }) });
    $('smsg').textContent = 'Added ' + r.domains.length + ' to Pi-hole' + (r.group ? ' (group ' + r.group + ')' : '') + '. Undo takes them back.'; open(current); }
  catch (e) { $('smsg').textContent = e.message; }
};
$('copy').onclick = async () => { const doms = ticked().join('\n'); try { await navigator.clipboard.writeText(doms); $('smsg').textContent = 'Copied ' + ticked().length + ' domains.'; } catch (e) { $('smsg').textContent = doms; } };
$('save').onclick = async () => { try {
  if (piholeOn) { await api('/v1/pihole/settings', { method: 'PUT', body: JSON.stringify({ pihole_url: $('phurl').value.trim() }) });
    if ($('phkey').value.trim()) { await api('/v1/settings/keys/pihole_app_password', { method: 'PUT', body: JSON.stringify({ value: $('phkey').value.trim() }) }); $('phkey').value = ''; } }
  $('setmsg').textContent = 'Saved.'; load(); } catch (e) { $('setmsg').textContent = e.message; } };
$('test').onclick = async () => { try { const r = await api('/v1/pihole/test', { method: 'POST' }); $('setmsg').textContent = r.ok ? 'Pi-hole answers. Your groups: ' + r.groups.join(', ') : r.error; } catch (e) { $('setmsg').textContent = e.message; } };
$('go').onclick = load;
load();
</script></main></body></html>"""


@page_router.get("/pihole", response_class=HTMLResponse, include_in_schema=False)
async def pihole_page(request: Request) -> str:
    return themed(PAGE, "pihole", request)
