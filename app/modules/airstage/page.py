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
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 999px; padding: 3px; background: var(--chip); gap: 2px; flex-wrap: wrap; }
.seg button { border: 0; border-radius: 999px; padding: 8px 14px; font-weight: 650; background: transparent; color: var(--ink); }
.seg button[aria-pressed="true"] { background: var(--accent); color: var(--on-accent); }
.seg.power button[data-v="off"][aria-pressed="true"] { background: var(--bad); }
.target { display: flex; align-items: center; gap: 10px; margin: 10px 0; } .target b { font-size: 1.5em; min-width: 3.2em; text-align: center; }
.target button { width: 44px; height: 44px; border-radius: 50%; font-size: 1.3em; padding: 0; }
.unit .ctl { display: grid; gap: 10px; }
details.box > summary { cursor: pointer; font-weight: 650; padding: 4px 0; }
#setup td, #setup th { padding: 4px; text-align: left; } #setup input { width: 100%; min-width: 0; }
</style></head><body><main>
<!--NAV-->
<h1>Air conditioning</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary" type="button">Load</button></div>
<p id="msg" class="err"></p>
<p id="none" class="dim" hidden>No air conditioners yet: add them under "Units" below (name, address on your network, device id).</p>
<div class="units" id="units"></div>
<div class="card" style="margin-top:14px"><details class="box" id="setupbox"><summary>Units</summary>
  <p class="dim">Each unit's address on your home network (give it a fixed address in your router) and its device id
  (in the Airstage app: the unit, then its information; or on the Wi-Fi adapter's label). World Assistant talks to the
  units directly, never through Fujitsu's cloud.</p>
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
  const box = document.getElementById('m-' + u.id); box.textContent = note || 'Sending…';
  try { await api('POST', '/v1/airstage/' + u.id + '/set', body); box.textContent = ''; await load(); }
  catch (e) { box.textContent = e.message; box.className = 'err'; }
}
function card(u) {
  const c = el('div', null, 'card unit'), s = u.state || {};
  c.append(el('h2', u.name));
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
    const tg = el('div', null, 'target');
    tg.append(btn('−', () => change(u, { target_c: (s.target_c || 21) - 0.5 })), el('b', deg(s.target_c)), btn('+', () => change(u, { target_c: (s.target_c || 21) + 0.5 })));
    ctl.append(tg);
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
    if (!$('rows').children.length || !$('setupbox').open) { $('rows').replaceChildren(); data.units.forEach(row); }
    if (!data.units.length) $('setupbox').open = true;
  } catch (e) { msg.textContent = e.message; }
}
$('add').onclick = () => row({});
$('save').onclick = async () => {
  const list = [...$('rows').children].map(tr => Object.fromEntries([...tr.querySelectorAll('input')].map(i => [i.dataset.k, i.value.trim()]))).filter(u => u.name || u.ip);
  try { await api('PUT', '/v1/airstage/units', list); $('smsg').textContent = 'Saved: the units are read within two minutes.'; $('rows').replaceChildren(); load(); }
  catch (e) { $('smsg').textContent = e.message; }
};
$('go').onclick = load;
load();
setInterval(() => { if (!document.hidden) load(); }, 60000);
</script></main></body></html>"""


@page_router.get("/aircon", response_class=HTMLResponse, include_in_schema=False)
async def aircon_page(request: Request) -> str:
    return themed(PAGE, "aircon", request)
