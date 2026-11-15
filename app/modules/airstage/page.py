"""The air conditioning page (/aircon): each Fujitsu Airstage unit with its temperature, an on/off switch, mode,
target and fan, and the units' setup folded away. The page holds no data and never builds HTML from it."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

page_router = APIRouter()

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant air conditioning</title>
<style>
/*BASE_CSS*/
.units { display: grid; gap: 14px; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); }
.unit h2 { margin: 0 0 2px; text-transform: none; letter-spacing: 0; font-size: 1.15em; color: var(--ink); }
.temps { display: flex; gap: 18px; margin: 8px 0 12px; } .temps b { display: block; font-size: 1.6em; }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 22px; padding: 3px; background: var(--chip); gap: 2px; flex-wrap: wrap; overflow: hidden; }
/* 22px keeps a single row a pill but stops a pressed button poking past the rounded corner when the row wraps */
.seg button { border: 0; border-radius: 999px; padding: 8px 14px; font-weight: 650; background: transparent; color: var(--ink); }
.seg button[aria-pressed="true"] { background: var(--accent); color: var(--on-accent); }
.seg.power button[data-v="off"][aria-pressed="true"] { background: var(--bad); }
.target { display: flex; align-items: center; gap: 10px; margin: 10px 0; }
.target button { width: 44px; height: 44px; border-radius: 50%; font-size: 1.3em; padding: 0; flex: none; }
.tnum { width: 4.6em; text-align: center; font-size: 1.3em; font-weight: 650; padding: 8px 6px; border: 1px solid var(--line); border-radius: 12px; background: var(--card); color: var(--ink); }
.allbar { display: grid; gap: 10px; } .allbar .row { align-items: center; gap: 8px; flex-wrap: wrap; } .allbar .tnum { font-size: 1em; width: 4em; }
.unit .ctl { display: grid; gap: 10px; }
details.box > summary { cursor: pointer; font-weight: 650; padding: 4px 0; }
#setup td, #setup th { padding: 4px; text-align: left; } #setup input { width: 100%; min-width: 0; }
.uhead { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.uhead h2 { margin: 0; }
.edit { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--line); display: grid; gap: 8px; }
.edit .field { display: grid; gap: 2px; } .edit input { width: 100%; min-width: 0; }
</style></head><body><main>
<!--NAV-->
<h1>Air conditioning</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary" type="button">Load</button></div>
<p id="msg" class="err"></p>
<p id="none" class="dim" hidden>No air conditioners yet: add one under "Add or edit units" below (name, address on your network, device id).</p>
<div class="card allbar" id="allbar" hidden></div>
<div class="units" id="units"></div>
<div class="card" style="margin-top:14px"><details class="box" id="setupbox"><summary>Add or edit units</summary>
  <p class="dim">To change or rename a unit already added, use <b>Edit</b> on its card above. Here you can add units and
  edit them together. Each unit's address on your home network (give it a fixed address in your router) and its device
  id (in the Airstage app: the unit, then its information; or on the Wi-Fi adapter's label). World Assistant talks to
  the units directly, never through Fujitsu's cloud. Renaming a unit keeps its history and its Home Assistant entity.</p>
  <table id="setup"><thead><tr><th>Name</th><th>Address</th><th>Device id</th><th></th></tr></thead><tbody id="rows"></tbody></table>
  <div class="row" style="margin-top:8px"><button id="add" type="button">Add a unit</button><button id="save" type="button" class="primary">Save the units</button></div>
  <p id="smsg" class="dim"></p>
</details></div>
<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
async function api(method, path, body) {
  const r = await fetch(path, { method, headers: { Authorization: 'Bearer ' + tok.value, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) { let d = r.status; try { d = (await r.json()).detail; } catch (e) {} throw new Error(typeof d === 'string' ? d : JSON.stringify(d)); }
  return r.json();
}
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const btn = (text, fn, attrs) => { const b = el('button', text); b.type = 'button'; b.onclick = fn; Object.assign(b.dataset, attrs || {}); return b; };
const deg = v => v == null ? '–' : v.toFixed(1) + '°';
const MODES = [['auto', 'Auto'], ['heat', 'Heat'], ['cool', 'Cool'], ['dry', 'Dry'], ['fan_only', 'Fan']];
const FANS = [['auto', 'Auto'], ['quiet', 'Quiet'], ['low', 'Low'], ['medium', 'Medium'], ['high', 'High']];
let data = null;

function seg(options, current, onpick, cls) {
  const s = el('div', null, 'seg' + (cls ? ' ' + cls : '')); s.setAttribute('role', 'group');
  for (const [v, label] of options) { const b = btn(label, () => onpick(v), { v }); b.setAttribute('aria-pressed', String(v === current)); s.append(b); }
  return s;
}
async function change(u, body, note) {
  const box = document.getElementById('m-' + u.id); if (box) box.textContent = note || 'Sending…';
  try { await api('POST', '/v1/airstage/' + u.id + '/set', body); if (box) box.textContent = ''; await load(); }
  catch (e) { if (box) { box.textContent = e.message; box.className = 'err'; } }
}
const half = v => Math.min(30, Math.max(16, Math.round((parseFloat(v) || 21) * 2) / 2));
// A temperature you can type, or step with − / +. The unit is written once you pause (700 ms) or leave the box, so
// a run of taps is one change to the air conditioner, not one each (which answered slowly).
function targetControl(u, current) {
  const wrap = el('div', null, 'target'), inp = el('input', null, 'tnum');
  inp.type = 'number'; inp.step = '0.5'; inp.min = '16'; inp.max = '30'; inp.inputMode = 'decimal';
  inp.value = (current == null ? 21 : current).toFixed(1);
  let timer = null;
  const send = () => { clearTimeout(timer); timer = null; change(u, { target_c: half(inp.value) }, 'Setting ' + half(inp.value) + '°…'); };
  const later = () => { clearTimeout(timer); timer = setTimeout(send, 700); };
  const step = d => { inp.value = half(half(inp.value) + d).toFixed(1); later(); };
  inp.oninput = later;
  inp.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); send(); inp.blur(); } };
  inp.onblur = () => { if (timer) send(); };
  wrap.append(btn('−', () => step(-0.5)), inp, btn('+', () => step(0.5)));
  return wrap;
}
async function setAll(body, note) {
  const m = $('allmsg'); m.className = 'dim'; m.textContent = note;
  try {
    const r = await api('POST', '/v1/airstage/set-all', body);
    const bad = r.results.filter(x => !x.ok);
    m.className = bad.length ? 'err' : 'dim';
    m.textContent = bad.length ? ('Changed ' + r.changed + ' of ' + r.results.length + '; ' + bad.map(x => x.name + ': ' + x.error).join('; '))
      : ('Done: ' + r.changed + ' unit' + (r.changed === 1 ? '' : 's') + '.');
    await load();
  } catch (e) { m.className = 'err'; m.textContent = e.message; }
}
function bulkBar() {
  const box = $('allbar'); if (box.dataset.built) { return; } box.dataset.built = '1';
  const r1 = el('div', null, 'row');
  r1.append(el('span', 'All units:', 'dim'), btn('All off', () => setAll({ power: false }, 'Turning all off…')),
            btn('All on', () => setAll({ power: true }, 'Turning all on…')));
  const inp = el('input', null, 'tnum'); inp.type = 'number'; inp.step = '0.5'; inp.min = '16'; inp.max = '30'; inp.value = '21.0'; inp.inputMode = 'decimal';
  const setT = btn('Set all to this', () => setAll({ target_c: half(inp.value) }, 'Setting all to ' + half(inp.value) + '°…'));
  inp.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); setT.click(); } };
  r1.append(el('span', '·', 'dim'), inp, el('span', '°', 'dim'), setT);
  const r2 = el('div', null, 'row'); r2.append(el('span', 'All to', 'dim'));
  for (const [v, label] of MODES) r2.append(btn(label, () => setAll({ power: true, mode: v }, 'All to ' + label + '…')));
  box.append(r1, r2, el('p', '', 'dim'));  // the message line
  box.lastChild.id = 'allmsg';
}
async function saveEdit(u, fields, msg) {
  const body = { id: u.id, name: fields.name.value.trim(), ip: fields.ip.value.trim(), device_id: fields.device_id.value.trim() };
  if (!body.name || !body.ip || !body.device_id) { msg.textContent = 'A name, an address and a device id.'; return; }
  try { await api('PATCH', '/v1/airstage/units/' + u.id, body); await load(); }
  catch (e) { msg.textContent = e.message; }
}
async function removeUnit(u, msg) {
  if (!confirm('Remove ' + u.name + '? Its controls go, and its Home Assistant entity becomes unavailable.')) return;
  try { await api('DELETE', '/v1/airstage/units/' + u.id); await load(); }
  catch (e) { msg.textContent = e.message; }
}
function editForm(u) {
  const box = el('div', null, 'edit'), fields = {}, msg = el('p', '', 'dim small');
  for (const [k, label, ph] of [['name', 'Name', 'e.g. Bedroom'], ['ip', 'Address on your network', 'e.g. 10.0.2.150'], ['device_id', 'Device id', 'e.g. A0B1C2D3E4F5']]) {
    const l = el('label', null, 'field'), i = el('input'); i.value = u[k] || ''; i.placeholder = ph; fields[k] = i;
    l.append(el('span', label, 'dim small'), i); box.append(l);
  }
  const r = el('div', null, 'row'), save = btn('Save', () => saveEdit(u, fields, msg)); save.className = 'primary';
  r.append(save, btn('Remove', () => removeUnit(u, msg)));
  box.append(r, msg);
  return box;
}
function card(u) {
  const c = el('div', null, 'card unit'), s = u.state || {};
  const head = el('div', null, 'uhead'); head.append(el('h2', u.name));
  const editBtn = btn('Edit', () => { const open = c.querySelector('.edit'); if (open) { open.remove(); return; } c.append(editForm(u)); });
  head.append(editBtn); c.append(head);
  const when = u.good_at ? new Date(u.good_at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : null;
  c.append(el('div', u.ok === false ? 'Not answering' + (when ? ' since ' + when : '') + ': ' + (u.error || '') : when ? 'Read at ' + when : 'Not read yet', u.ok === false ? 'err' : 'dim'));
  const t = el('div', null, 'temps');
  for (const [label, v] of [['Inside', s.indoor_c], ['Outside', s.outdoor_c]]) { const d = el('div'); d.append(el('span', label, 'dim'), el('b', deg(v))); t.append(d); }
  c.append(t);
  if (s.error_code) c.append(el('p', 'Error code ' + s.error_code, 'err'));
  if (s.filter_due) c.append(el('p', 'The filter needs cleaning.', 'err'));
  if (!u.state) { c.append(el('p', 'Waiting for the unit to answer.', 'dim')); return c; }
  const ctl = el('div', null, 'ctl');
  ctl.append(seg([['off', 'Off'], ['on', 'On']], s.power ? 'on' : 'off', v => change(u, { power: v === 'on' }), 'power'));
  ctl.append(seg(MODES, s.mode, v => change(u, { power: true, mode: v })));
  if (s.mode !== 'fan_only') {
    ctl.append(targetControl(u, s.target_c));
  }
  const fan = s.fan === 'medium_low' ? 'low' : s.fan === 'medium_high' ? 'high' : s.fan;
  ctl.append(el('span', 'Fan', 'dim'), seg(FANS, fan, v => change(u, { fan: v })));
  const extras = el('div', null, 'row');
  for (const [k, label] of [['swing_vertical', 'Swing'], ['economy', 'Economy'], ['powerful', 'Powerful'], ['low_noise', 'Quiet outdoor unit'], ['minimum_heat', '10° minimum heat']]) {
    if (s[k] == null) continue;
    const l = el('label', null, 'tick'), i = el('input'); i.type = 'checkbox'; i.checked = s[k];
    i.onchange = () => change(u, { [k]: i.checked }); l.append(i, document.createTextNode(' ' + label)); extras.append(l);
  }
  ctl.append(extras);
  c.append(ctl);
  const m = el('p', '', 'dim'); m.id = 'm-' + u.id; c.append(m);
  return c;
}
function row(u) {
  const tr = el('tr');
  tr.dataset.id = u.id || '';  // kept so a renamed unit keeps its history and Home Assistant entity
  for (const [k, ph] of [['name', 'e.g. Bedroom'], ['ip', 'e.g. 10.0.2.150'], ['device_id', 'e.g. A0B1C2D3E4F5']]) {
    const td = el('td'), i = el('input'); i.value = u[k] || ''; i.placeholder = ph; i.dataset.k = k; td.append(i); tr.append(td);
  }
  const td = el('td'); td.append(btn('Remove', () => tr.remove())); tr.append(td);
  $('rows').append(tr);
}
async function load() {
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  msg.textContent = '';
  try {
    data = await api('GET', '/v1/airstage');
    $('units').replaceChildren(...data.units.map(card));
    $('none').hidden = data.units.length > 0;
    bulkBar(); $('allbar').hidden = data.units.length < 2;
    if (!$('rows').children.length || !$('setupbox').open) { $('rows').replaceChildren(); data.units.forEach(row); }
    if (!data.units.length) $('setupbox').open = true;
  } catch (e) { msg.textContent = e.message; }
}
$('add').onclick = () => row({});
$('save').onclick = async () => {
  const list = [...$('rows').children].map(tr => ({ id: tr.dataset.id || null, ...Object.fromEntries([...tr.querySelectorAll('input')].map(i => [i.dataset.k, i.value.trim()])) })).filter(u => u.name || u.ip);
  try { await api('PUT', '/v1/airstage/units', list); $('smsg').textContent = 'Saved: the units are read within two minutes.'; $('rows').replaceChildren(); load(); }
  catch (e) { $('smsg').textContent = e.message; }
};
$('go').onclick = load;
load();
// refresh in the background, but never while you are typing in a field (it would clear what you are entering)
setInterval(() => { const a = document.activeElement; if (!document.hidden && !(a && a.tagName === 'INPUT')) load(); }, 60000);
</script></main></body></html>"""


@page_router.get("/aircon", response_class=HTMLResponse, include_in_schema=False)
async def aircon_page(request: Request) -> str:
    return themed(PAGE, "aircon", request)
