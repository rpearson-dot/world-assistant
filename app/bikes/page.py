"""The Bikes page: a rider's garage. What is due first, then rides to confirm as wet or dry, then each bike with its
wheels and parts (each with how much of its life is used), batteries, the spares shelf, kit, rides and running cost.

The page holds no data itself; it calls /v1/bikes. Pictures: a part's or bike's own photo, else the brand picture
linked from its product page (credited), else a generic drawing of its kind.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant bikes</title>
<style>
/*BASE_CSS*/
.chip { font-size: 12px; padding: 1px 8px; border-radius: 9px; background: var(--chip); white-space: nowrap; }
.chip.due { background: var(--bad-bg); color: var(--bad); } .chip.coming { background: var(--warn-bg); color: var(--warn); }
.chip.ok { background: var(--good-bg); color: var(--good); }
.bike { display: grid; grid-template-columns: 150px 1fr; gap: 14px; align-items: start; }
.bike .pic { color: var(--ink); } .bike .pic svg, .bike .pic img { width: 150px; height: auto; border-radius: 10px; }
@media (max-width: 560px) { .bike { grid-template-columns: 1fr; } .bike .pic svg, .bike .pic img { width: 100%; max-width: 260px; }
  .parts li { grid-template-columns: 34px 1fr; } .parts li > .row { grid-column: 2; } }
.bike h3 { color: var(--ink); margin: 0 0 4px; font-size: 20px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
select { max-width: 100%; } .wheel h4 select { max-width: 220px; }
.parts { list-style: none; margin: 6px 0 0; padding: 0; }
.parts li { display: grid; grid-template-columns: 34px 1fr auto; gap: 8px; align-items: center; padding: 6px 0;
  border-top: 1px solid var(--line); }
.parts .ic { width: 30px; height: 30px; color: var(--dim); } .parts .ic svg, .parts .ic img { width: 30px; height: 30px; border-radius: 6px; object-fit: cover; }
.meter { height: 6px; border-radius: 3px; background: var(--chip); overflow: hidden; margin-top: 3px; max-width: 260px; }
.meter i { display: block; height: 100%; background: var(--good); } .meter i.coming { background: var(--warn); } .meter i.due { background: var(--bad); }
.wheel { margin-top: 10px; } .wheel h4 { margin: 0; font-size: 14px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
details > summary { cursor: pointer; } .small { font-size: 13px; }
form.grid { display: grid; gap: 8px; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); align-items: end; }
.edit { background: var(--bg); border: 1px solid var(--line); border-radius: 10px; padding: 10px; margin: 6px 0; grid-column: 1 / -1; }
.press { display: flex; gap: 14px; flex-wrap: wrap; font-size: 14px; }
table.costs { border-collapse: collapse; width: 100%; font-size: 14px; } table.costs td, table.costs th { padding: 4px 6px; border-top: 1px solid var(--line); text-align: left; }
button.small { padding: 3px 9px; font-size: 13px; }
/* the workshop: bikes in a row, shelves below; parts and wheels dragged between them */
.ws-bikes { display: flex; gap: 12px; overflow-x: auto; padding-bottom: 6px; }
.ws-bike { flex: 0 0 280px; border: var(--frame) solid var(--line); border-radius: var(--radius); padding: 10px; background: var(--card); }
.ws-bike h4 { margin: 0 0 4px; display: flex; justify-content: space-between; gap: 8px; }
.ws-pic { color: var(--ink); } .ws-pic svg { width: 100%; height: auto; }
.odo { font-variant-numeric: tabular-nums; font-family: ui-monospace, monospace; font-size: 12px; background: var(--ink); color: var(--card);
  border-radius: 4px; padding: 1px 6px; letter-spacing: .04em; white-space: nowrap; }
.ws-zone { border: 2px dashed var(--line); border-radius: var(--radius-sm); padding: 6px; margin-top: 6px; min-height: 34px; }
.ws-zone.over { border-color: var(--accent); background: var(--chip); }
.ws-zone.bad { border-color: var(--bad); }
.ws-zone .zl { font-size: 12px; color: var(--dim); display: flex; justify-content: space-between; gap: 6px; align-items: center; }
.ws-wheels { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.ws-chip, .ws-wheel { display: inline-flex; align-items: center; gap: 4px; padding: 2px 7px; margin: 2px; border-radius: var(--pill);
  background: var(--chip); border: 1px solid var(--line); font-size: 12px; cursor: grab; touch-action: none; user-select: none; }
.ws-chip svg { width: 16px; height: 16px; } .ws-chip.due { border-color: var(--bad); } .ws-chip.coming { border-color: var(--warn); }
.ws-chip.sel, .ws-wheel.sel { outline: 2px solid var(--accent); }
.ws-wheel { display: flex; justify-content: space-between; border-radius: var(--radius-sm); background: var(--card); font-weight: 600; }
.ws-ghost { position: fixed; z-index: 99; pointer-events: none; opacity: .85; transform: translate(-50%, -50%); }
.shelf { border-bottom: 6px solid var(--line); border-radius: 0 0 4px 4px; padding: 6px 4px 2px; margin-top: 10px; min-height: 40px; }
.shelf h4 { margin: 0; font-size: 13px; color: var(--dim); }
.shelf.over { background: var(--chip); }
#wsinfo { margin-top: 8px; }
</style></head><body><main>
<!--NAV-->
<h1>Bikes</h1>
<form id="auth" class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button class="primary">Load</button></form>
<p id="msg" class="err"></p>

<div class="card">
  <div class="row"><label>Rider <select id="rider"></select></label>
  <details id="newrider"><summary class="small">Add a rider</summary>
    <form id="addrider" class="grid"><label>Name <input id="rname" maxlength="40" required></label>
    <label>Weight kg <input id="rkg" type="number" min="25" max="240" step="0.1" placeholder="for tyre pressures"></label>
    <label>Garmin key <input id="rkey" type="password" autocomplete="off" placeholder="optional: Personal API Key"></label>
    <label>Key runs out on <input id="rexp" type="date"></label>
    <button class="primary">Add</button></form>
    <p class="dim small">The Garmin key is the rider's own: Garmin Connect on the web, Settings, then Personal API Key. It can be
    added later too.</p></details></div>
  <p id="rmsg" class="err"></p>
  <div id="garmincard" hidden>
    <p id="gwarn" class="err"></p>
    <div class="row"><span id="gstate" class="small"></span><button id="gsync" type="button" class="small">Sync now</button></div>
    <details id="gkeybox"><summary class="small">This rider's Garmin key</summary>
      <p class="dim small">Make one in Garmin Connect on the web: Settings, then Personal API Key. Copy it here: it is kept on this
      server (the keys folder) and never shown again. Rides then come in every morning; the first sync reads the whole history.</p>
      <form id="gkey" class="grid"><label>Key <input id="gkeyv" type="password" autocomplete="off" required></label>
      <label>Runs out on <input id="gexp" type="date"></label><button class="primary">Save the key</button></form>
      <div class="row"><button id="gforget" type="button" class="small">Forget the key</button></div></details>
    <details id="grulebox"><summary class="small">Which bike each kind of Garmin ride goes to</summary>
      <p class="dim small">For new rides from Garmin; you are still asked to confirm the bike of each recent ride. A ride Garmin
      marks as a commute goes to the Commute bike first, whatever kind of ride it is.</p>
      <form id="grules" class="grid"></form>
      <label class="small"><input type="checkbox" id="gweight"> Take this rider's weight from Garmin (for tyre pressures)</label>
      <div class="row"><button id="grulesgo" type="button" class="primary small">Save</button>
      <button id="grulesapply" type="button" class="small">Save, and give rides without a bike theirs</button></div></details>
    <p id="gmsg" class="dim"></p>
  </div>
</div>

<div class="card" id="wscard" hidden><h2>Workshop</h2>
  <p class="dim small">Drag a part or a wheel onto a bike, a wheel or a shelf; a spare dropped where there is one already replaces
  it. Tap one to see its odometer and wear. Services and checks stay on each bike's card below.</p>
  <div class="ws-bikes" id="wsbikes"></div>
  <div class="shelf ws-drop" data-drop="shelf"><h4>Spares shelf</h4><div id="wsspares"></div></div>
  <div class="shelf ws-drop" data-drop="kit"><h4>Kit (batteries, helmet, shoes)</h4><div id="wskit"></div></div>
  <div class="shelf ws-drop" data-drop="storage"><h4>Wheels in storage</h4><div id="wsstore"></div></div>
  <div id="wsinfo" class="small"></div><p id="wsmsg" class="err small"></p>
</div>

<div class="card" id="duecard" hidden><h2>Due</h2><ul class="parts" id="due"></ul></div>
<div class="card" id="askcard" hidden><h2>Rides to confirm</h2>
  <p class="dim small">Which bike each ride was on (Garmin keeps the last one chosen, so it is often wrong), and whether it was wet:
  wet rides wear chains, cassettes and pads faster. The wet suggestion comes from the nearest rain gauge.</p>
  <div class="edit"><div id="askcount" class="small"></div>
    <div class="row"><button id="alldry" type="button" class="small">Mark every unanswered ride dry</button>
    <label class="small"><input type="checkbox" id="keepwet" checked> except those the rain gauge says were wet</label></div>
    <div class="row"><button id="allbikes" type="button" class="small">Keep every bike as it is</button></div>
    <p id="bulkmsg" class="dim small"></p></div>
  <ul class="parts" id="ask"></ul></div>
<div class="card" id="unassignedcard" hidden><h2>Rides without a bike</h2>
  <div class="edit"><div class="small"><b>By kind of ride</b> (Garmin's, from the sync or its CSV): choose a bike for each, and
  every ride of that kind without a bike gets it. Kept as the rule for new Garmin rides too.</div>
  <form id="bykind" class="grid"></form><div class="row"><button id="bykindgo" type="button" class="primary small">Give them these bikes</button></div>
  <p id="bykindmsg" class="dim small"></p></div>
  <ul class="parts" id="unassigned"></ul></div>

<div id="bikes"></div>

<div class="card"><h2>Add a bike</h2>
  <form id="addbike" class="grid">
    <label>Name <input id="blabel" maxlength="60" required placeholder="Gravel"></label>
    <label>Type <select id="btype"></select></label>
    <label><input type="checkbox" id="bebike"> E-bike</label>
    <label>Parts fitted on <input id="bdate" type="date"></label>
    <label>Bike weight kg <input id="bkg" type="number" min="4" max="60" step="0.1" placeholder="about 10"></label>
  </form>
  <details><summary class="small">Its answers (the type's defaults; change any)</summary><form id="answers" class="grid"></form></details>
  <div class="row"><button id="addbikego" class="primary">Add the bike</button><span id="bmsg" class="err"></span></div>
</div>

<div class="card"><h2>Rides</h2>
  <form id="addride" class="grid">
    <label>Bike <select id="ridebike"></select></label>
    <label>Start <input id="ridewhen" type="datetime-local" required></label>
    <label>km <input id="ridekm" type="number" min="0" max="2000" step="0.1" required></label>
    <label>Hours <input id="ridehours" type="number" min="0" max="72" step="0.01"></label>
    <label><input type="checkbox" id="rideindoor"> Indoor</label>
    <button class="primary">Add ride</button></form>
  <details><summary class="small">Import from Garmin Connect</summary>
    <p class="dim small">In Garmin Connect on the web: Activities, then Export CSV. Choose the file here; rides already imported are skipped.</p>
    <form id="import" class="grid"><input id="csv" type="file" accept=".csv,text/csv">
    <label>Bike for these rides <select id="importbike"></select></label>
    <label><input type="checkbox" id="miles"> Distances in miles</label><button class="primary">Import</button></form></details>
  <p id="ridemsg" class="dim"></p>
  <details><summary class="small">Recent rides (delete one logged twice)</summary><ul class="parts" id="recentrides"></ul></details>
</div>

<div class="card" id="storecard" hidden><h2>Wheels in storage</h2>
  <p class="dim small">Wheels off every bike: their parts keep their wear for when they go back on.</p><div id="store"></div></div>

<div class="card" id="logcard" hidden><h2>Recent work</h2>
  <p class="dim small">What was logged, newest first. Undo puts the part's count back; undoing a replacement puts the old part
  back and takes the new one away (a spare goes back on the shelf).</p>
  <ul class="parts" id="log"></ul></div>

<div class="card" id="retiredcard" hidden><h2>Retired bikes</h2>
  <p class="dim small">Sold, stolen or written off: their rides, parts, history and cost are kept. Bring one back to ride it again.</p>
  <div id="retired"></div></div>

<div class="card"><h2>Shelf and kit</h2>
  <p class="dim small">Spares on the shelf are used first when a part is due. Kit is yours, not a bike's: helmet, shoes, AXS batteries.</p>
  <ul class="parts" id="shelf"></ul><ul class="parts" id="kit"></ul>
  <details><summary class="small">Add a spare or kit</summary>
    <form id="addspare" class="grid"><label>Kind <select id="skind"></select></label>
    <label>On <select id="sstatus"><option value="shelf">the shelf</option><option value="kit">my kit</option></select></label>
    <label>Label <input id="slabel" maxlength="60" placeholder="Battery A"></label>
    <label>Brand <input id="sbrand" maxlength="60"></label><label>Model <input id="smodel" maxlength="120"></label>
    <label>How many <input id="sqty" type="number" min="1" max="99" value="1"></label>
    <label>Price £ <input id="sprice" type="number" min="0" step="0.01"></label><button class="primary">Add</button></form></details>
</div>

<div class="card"><h2>Running cost</h2><div id="costs" class="small"></div></div>

<div class="card"><h2>Settings</h2>
  <form id="settings" class="grid">
    <label>Due parts to the Outdoors list <select id="to_outdoors"><option value="auto">automatically</option>
      <option value="tap">when I tap</option><option value="never">never</option></select></label>
    <label>A wet ride counts as <input id="wet_factor" type="number" min="1" max="3" step="0.1"> times</label>
    <label><input type="checkbox" id="trainer"> My trainer has its own cassette</label>
    <label>My weight kg <input id="weight" type="number" min="25" max="240" step="0.1"></label>
    <button class="primary">Save</button></form><p id="smsg" class="dim"></p>
</div>

<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function button(text, onclick, cls) { const b = el('button', cls || 'small', text); b.type = 'button'; b.onclick = onclick; return b; }
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
async function api(method, path, body) {
  const r = await fetch(path, { method, headers: { Authorization: 'Bearer ' + tok.value, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined });
  if (r.status === 204) return null;
  let d = null; try { d = await r.json(); } catch (e) {}
  if (!r.ok) { let m = d && d.detail; if (Array.isArray(m)) m = m.map(x => x.msg).join('; '); throw new Error(m || ('HTTP ' + r.status)); }
  return d;
}
async function guard(fn, out) { (out || msg).textContent = ''; try { await fn(); } catch (e) { (out || msg).textContent = e.message; } }
let cat = null, g = null;
const rider = () => $('rider').value;
const svg = (html, cls) => { const s = el('span', cls); s.innerHTML = html; return s; };  // drawings made by the server
async function photo(path, alt) {  // a photo behind the token: fetched, then shown
  const img = el('img'); img.alt = alt || '';
  try { const r = await fetch(path, { headers: { Authorization: 'Bearer ' + tok.value } }); if (r.ok) img.src = URL.createObjectURL(await r.blob()); } catch (e) {}
  return img;
}
function picture(item, kindIcon) {
  const box = el('span', 'ic');
  if (item.has_photo) { photo((item.kind ? '/v1/bikes/parts/' : '/v1/bikes/bikes/') + item.id + '/photo', item.name || item.label).then(i => box.replaceChildren(i)); }
  else if (item.photo_url) { const i = el('img'); i.src = item.photo_url; i.alt = item.name || item.label || ''; i.referrerPolicy = 'no-referrer'; i.title = 'Picture from ' + (item.url || item.photo_url); box.append(i); }
  else box.innerHTML = kindIcon || '';
  return box;
}
const fmt = n => n == null ? '' : (Math.round(n * 10) / 10).toLocaleString('en-GB');
function wearText(w) {
  const m = w.measures || {}, bits = [];
  if (m.km) bits.push(fmt(m.km.used) + ' of ' + fmt(m.km.life) + ' km');
  if (m.hours) bits.push(fmt(m.hours.used) + ' of ' + fmt(m.hours.life) + ' h');
  if (m.days) bits.push(Math.round(m.days.used) + ' of ' + Math.round(m.days.life) + ' days');
  if (w.wear_km > w.km + 0.5) bits.push('(' + fmt(w.km) + ' km ridden, ' + w.wet_rides + ' wet)');
  return bits.join(' · ');
}
const LABEL = { replaced: 'Replaced', serviced: 'Serviced', checked: 'Checked', charged: 'Charged', topped_up: 'Topped up', bled: 'Bled' };
const OWN = { tyre_check: 'Pumped', lube: 'Lubed' };
const act = kind => { const a = (cat.kinds[kind] || {}).action || 'replaced'; return [a, OWN[kind] || LABEL[a]]; };
function partRow(p, bike) {
  const li = el('li'), w = p.wear || { measures: {}, share: 0, state: 'untracked' };
  li.append(picture(p, cat.icons[(cat.kinds[p.kind] || {}).icon] || cat.icons.part));
  const mid = el('div'), title = el('div');
  title.append(el('b', null, p.name), document.createTextNode(' '));
  if (p.describe) title.append(el('span', 'dim small', p.describe + ' '));
  if (w.state !== 'untracked') title.append(el('span', 'chip ' + w.state, w.state === 'ok' ? Math.round(w.share * 100) + '%' : w.state === 'coming' ? 'coming up' : 'due'));
  mid.append(title);
  if (w.state !== 'untracked') { const m = el('div', 'meter'), i = el('i', w.state); i.style.width = Math.min(100, Math.round(w.share * 100)) + '%'; m.append(i); mid.append(m, el('div', 'dim small', wearText(w))); }
  if (p.measure && w.state !== 'ok') mid.append(el('div', 'dim small', 'Check: ' + p.measure));
  if (p.health && p.health.charges) { const h = p.health; mid.append(el('div', 'dim small', h.charges + ' charges' + (h.learned_hours ? ', about ' + h.learned_hours + ' h a charge' : '') +
    (h.early ? '; ' + h.early + ' h when new, ' + h.recent + ' h now' + (h.wearing ? ' (wearing: replace soon)' : '') : ''))); }
  const acts = el('div', 'row');
  const [action, label] = act(p.kind);
  acts.append(button(label, () => action === 'replaced' ? replaceForm(li, p) : service(p, action)));
  if (action !== 'replaced' && (cat.kinds[p.kind] || {}).km) acts.append(button('Replaced', () => replaceForm(li, p)));
  acts.append(button('Edit', () => editPart(li, p)));
  if ((cat.kinds[p.kind] || {}).on === 'wheel') {  // onto another wheel (the cassette onto the new wheel, say)
    const mv = el('select'); mv.append(new Option('Move to…', ''));
    for (const [s, sb] of allWheels()) if (s.id !== p.set_id) mv.append(new Option(s.label + (sb ? ' (' + sb.label + ')' : ' (storage)'), s.id));
    mv.onchange = () => { if (mv.value) guard(async () => { await api('POST', '/v1/bikes/fit', { item: 'part', id: p.id, set_id: Number(mv.value) }); await load(); }); };
    acts.append(mv);
  }
  li.append(mid, acts);
  if ((p.history || []).length) {
    const h = el('details', 'small'); h.style.gridColumn = '2 / -1'; h.append(el('summary', 'dim', 'History (' + p.history.length + ')'));
    const ul = el('ul'); for (const x of p.history) ul.append(el('li', null, new Date(x.at).toLocaleDateString('en-GB') + ': ' + (LABEL[x.action] || x.action) +
      (x.note ? ', ' + x.note : '') + (x.cost != null ? ' (£' + Number(x.cost).toFixed(2) + ')' : '')));
    h.append(ul); li.append(h);
  }
  return li;
}
function specFields(f, kind, details) {  // a kind's model details, as inputs named spec_<key>
  for (const [key, lab] of (cat.specs[kind] || [])) { const l = el('label', null, lab + ' '), i = el('input'); i.name = 'spec_' + key; i.value = (details || {})[key] == null ? '' : details[key]; i.maxLength = 60; l.append(i); f.append(l); }
}
function specValues(f) {
  const d = {}; for (const i of f.elements) if (i.name && i.name.startsWith('spec_') && i.value !== '') { const v = i.value.trim(); d[i.name.slice(5)] = /^[0-9]+(\.[0-9]+)?$/.test(v) ? Number(v) : v === 'yes' ? true : v === 'no' ? false : v; }
  return d;
}
function replaceForm(li, p) {
  if (li.querySelector('.edit')) { li.querySelector('.edit').remove(); return; }
  const box = el('div', 'edit'), out = el('span', 'err');
  box.append(el('div', 'small', 'Replacing ' + p.name + (p.describe ? ' (' + p.describe + ')' : '') + '. Its count starts again from now.'));
  const row = el('div', 'row');
  const spare = (g.shelf || []).find(s => s.kind === p.kind && s.quantity > 0);
  if (spare) row.append(button('Use the spare: ' + (spare.describe || spare.name) + ' (' + spare.quantity + ')', () => guard(async () => {
    await api('POST', '/v1/bikes/service', { rider: rider(), action: 'replaced', part_id: p.id, spare_id: spare.id }); await load(); }, out), 'primary small'));
  row.append(button('The same again' + (p.describe ? ': ' + p.describe : ''), () => guard(async () => {
    const price = prompt('What did it cost (£)? Leave blank if not known.', p.price != null ? p.price : '');
    if (price === null) return;
    await api('POST', '/v1/bikes/service', { rider: rider(), action: 'replaced', part_id: p.id, cost: price === '' ? null : Number(price) }); await load(); }, out)));
  box.append(row, el('div', 'small', 'Or something different:'));
  const f = el('form', 'grid');
  const field = (lab, name, val, type) => { const l = el('label', null, lab + ' '), i = el('input'); i.name = name; i.value = val || ''; if (type) i.type = type; l.append(i); f.append(l); };
  field('Brand', 'brand', '', null); field('Model', 'model', '', null); specFields(f, p.kind, p.details);
  field('Price £', 'price', '', 'number'); field('Product page', 'url', '', 'url');
  f.append(el('button', 'primary small', 'Fitted this instead'));
  f.onsubmit = e => { e.preventDefault(); guard(async () => {
    const v = n => f.elements[n].value.trim();
    if (!v('brand') && !v('model')) throw new Error('Say the brand or model of what was fitted');
    const n = { brand: v('brand') || null, model: v('model') || null, details: specValues(f), url: v('url') || null };
    if (v('price') !== '') n.price = Number(v('price'));
    const r = await api('POST', '/v1/bikes/service', { rider: rider(), action: 'replaced', part_id: p.id, new: n });
    msg.textContent = r.note ? 'Logged: ' + r.note : ''; await load(); }, out); };
  box.append(f, out); li.append(box);
}
async function service(p, action) {
  const body = { rider: rider(), action, part_id: p.id };
  if (action === 'charged') { const lv = prompt('Battery level when you put it on charge (%)? Leave blank if not known.', ''); if (lv === null) return; if (lv !== '') body.level_before = Number(lv); }
  if (action === 'replaced') {
    const spare = (g.shelf || []).find(s => s.kind === p.kind && s.quantity > 0);
    if (spare && confirm('Use the spare from the shelf (' + spare.name + ', ' + spare.quantity + ' there)?')) body.spare_id = spare.id;
    else if (!confirm('Log ' + p.name + ' as replaced with a new one? Its count starts again.')) return;
  }
  await guard(async () => { await api('POST', '/v1/bikes/service', body); await load(); });
}
function editPart(li, p) {
  if (li.querySelector('.edit')) { li.querySelector('.edit').remove(); return; }
  const f = el('form', 'edit grid'), out = el('span', 'err');
  const life = p.life || {}, k = cat.kinds[p.kind] || {};
  const field = (lab, name, val, type, extra) => { const l = el('label', null, lab + ' '), i = el('input'); i.name = name; i.value = val == null ? '' : val; if (type) i.type = type; Object.assign(i, extra || {}); l.append(i); f.append(l); return i; };
  field('Label', 'label', p.label, null, { maxLength: 60, placeholder: 'Chain A' });
  field('Brand', 'brand', p.brand, null, { maxLength: 60 }); field('Model', 'model', p.model, null, { maxLength: 120 });
  specFields(f, p.kind, p.details);
  field('Price £', 'price', p.price, 'number', { min: 0, step: 0.01 });
  field('Product page', 'url', p.url, 'url', { placeholder: 'https://...' });
  field('Life km', 'km', life.km != null ? life.km : k.km, 'number', { min: 0 });
  field('Life hours', 'hours', life.hours != null ? life.hours : k.hours, 'number', { min: 0 });
  field('Life days', 'days', life.days != null ? life.days : k.days, 'number', { min: 0 });
  field('Distance before it was added (km)', 'start_km', p.start_km, 'number', { min: 0 });
  if (p.kind === 'tyre') { field('Pressure psi (blank: suggested)', 'psi', (p.details || {}).psi, 'number', { min: 5, max: 160 }); field('Max psi (tyre or rim)', 'max_psi', (p.details || {}).max_psi, 'number', { min: 5, max: 200 }); field('Width mm', 'width_mm', (p.details || {}).width_mm, 'number', { min: 15, max: 130 }); }
  const ph = el('label', null, 'Photo '), file = el('input'); file.type = 'file'; file.accept = 'image/*'; file.capture = 'environment'; ph.append(file); f.append(ph);
  const row = el('div', 'row');
  row.append(button('Look up the product page', () => guard(async () => {
    const u = f.elements.url.value; if (!u) throw new Error('Paste the product page first');
    const d = await api('POST', '/v1/bikes/lookup', { url: u });
    if (d.brand && !f.elements.brand.value) f.elements.brand.value = d.brand;
    if (d.name && !f.elements.model.value) f.elements.model.value = d.name;
    f.dataset.photo = d.image || ''; out.textContent = d.image ? 'Picture found (linked from the product page).' : 'No picture on that page.';
  }, out)), el('button', 'primary small', 'Save'),
  button('Delete', () => { if (confirm('Delete ' + p.name + ' and its log? This is for a part added by mistake: a used part is replaced instead, keeping its history.')) guard(async () => { await api('DELETE', '/v1/bikes/parts/' + p.id); await load(); }, out); }), out);
  f.append(row);
  f.onsubmit = e => { e.preventDefault(); guard(async () => {
    const v = n => f.elements[n] && f.elements[n].value !== '' ? f.elements[n].value : null;
    const num = n => v(n) == null ? null : Number(v(n));
    const lifeOut = {}; for (const u of ['km', 'hours', 'days']) if (num(u)) lifeOut[u] = num(u);
    const body = { label: v('label'), brand: v('brand'), model: v('model'), price: num('price'), url: v('url'), life: lifeOut, start_km: num('start_km') || 0 };
    if (f.dataset.photo) body.photo_url = f.dataset.photo;
    body.details = Object.assign({}, p.details || {}, specValues(f));
    if (p.kind === 'tyre') Object.assign(body.details, { psi: num('psi'), max_psi: num('max_psi'), width_mm: num('width_mm') || (p.details || {}).width_mm });
    await api('PATCH', '/v1/bikes/parts/' + p.id, body);
    if (file.files[0]) await api('PUT', '/v1/bikes/parts/' + p.id + '/photo', { data_url: await shrink(file.files[0]) });
    await load();
  }, out); };
  li.append(f);
}
function shrink(file) {  // a photo made small enough to keep: 1000 px on its longer side, as a JPEG
  return new Promise((ok, no) => { const img = new Image(); img.onload = () => {
    const s = Math.min(1, 1000 / Math.max(img.width, img.height)), c = document.createElement('canvas');
    c.width = Math.round(img.width * s); c.height = Math.round(img.height * s); c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
    ok(c.toDataURL('image/jpeg', 0.82)); }; img.onerror = () => no(new Error('That file is not a picture')); img.src = URL.createObjectURL(file); });
}
function bikeCard(b) {
  const card = el('div', 'card'), top = el('div', 'bike'), pic = el('div', 'pic');
  if (b.has_photo) photo('/v1/bikes/bikes/' + b.id + '/photo', b.label).then(i => pic.replaceChildren(i)); else pic.append(svg(b.art));
  const info = el('div');
  const h = el('h3', null, b.label + ' '); h.append(el('span', 'chip', (cat.types[(b.config || {}).type] || '') + ((b.config || {}).ebike ? ', e-bike' : '')));
  info.append(h, el('p', 'dim small', fmt(b.km) + ' km logged'));
  const pr = el('div', 'press');
  for (const end of ['front', 'rear']) { const p = (b.pressures || {})[end]; if (p) pr.append(el('span', null, (end === 'front' ? 'Front ' : 'Rear ') + p.psi + ' psi (' + p.bar + ' bar)' + (p.own ? '' : ' suggested'))); }
  info.append(pr);
  info.append(batteryBox(b));
  const photoIn = el('input'); photoIn.type = 'file'; photoIn.accept = 'image/*'; photoIn.capture = 'environment'; photoIn.hidden = true;
  photoIn.onchange = () => guard(async () => { await api('PUT', '/v1/bikes/bikes/' + b.id + '/photo', { data_url: await shrink(photoIn.files[0]) }); await load(); });
  info.append(el('div', 'row'));
  info.lastChild.append(button('Photo', () => photoIn.click()), photoIn,
    button('Its answers', () => changeAnswers(card, b)),
    button('Retire', () => { if (confirm('Retire ' + b.label + '? Its rides, parts, history and cost are kept, and you can bring it back.')) guard(async () => { await api('PATCH', '/v1/bikes/bikes/' + b.id, { retired: true }); await load(); }); }));
  if (!b.km) info.lastChild.append(button('Delete', () => { if (confirm('Delete ' + b.label + ' and its parts and wheels? This is for a bike added by mistake; it cannot be undone.')) guard(async () => { await api('DELETE', '/v1/bikes/bikes/' + b.id); await load(); }); }));
  top.append(pic, info); card.append(top);
  const list = el('ul', 'parts'); (b.parts || []).forEach(p => list.append(partRow(p, b)));
  const d = el('details'); d.append(el('summary', 'small', 'Frame, drivetrain and brakes (' + (b.parts || []).length + ')'), list); card.append(d);
  for (const s of b.sets || []) card.append(wheelBlock(s, b));
  const add = button('Add a wheel', () => wheelForm(card, b)); add.className = 'small'; card.append(el('div', 'row'), add);
  return card;
}
function allWheels() {  // every wheel: on a bike, or in storage
  const out = []; for (const b of g.bikes.filter(x => !x.retired_at)) for (const s of b.sets || []) out.push([s, b]);
  for (const s of g.loose_sets || []) out.push([s, null]); return out.filter(([s]) => s.kind === 'wheel');
}
function wheelBlock(s, b) {  // a wheel (or other set) with its parts: move it, rename it, group it, add parts
  const w = el('div', 'wheel'), h4 = el('h4', null, s.label);
  if (s.grp) h4.append(el('span', 'chip', s.grp));
  const since = el('input'); since.type = 'date'; since.title = 'Since when (blank: now). Rides from then count on the wheel where it went.';
  const at = () => since.value ? since.value + 'T00:00:00' : null;
  const sel = el('select'); sel.append(new Option(b ? 'Move to…' : 'Fit to…', ''));
  for (const other of g.bikes) if ((!b || other.id !== b.id) && !other.retired_at) sel.append(new Option('onto ' + other.label, other.id));
  if (b) sel.append(new Option('off (into storage)', 'off'));
  if (s.grp) for (const other of g.bikes) if ((!b || other.id !== b.id) && !other.retired_at) sel.append(new Option('whole set "' + s.grp + '" onto ' + other.label, 'g' + other.id));
  sel.onchange = () => { const v = sel.value; if (!v) return; guard(async () => {
    if (v[0] === 'g') await api('POST', '/v1/bikes/fit', { item: 'group', group: s.grp, rider: rider(), bike_id: Number(v.slice(1)), at: at() });
    else await api('POST', '/v1/bikes/fit', { item: 'set', id: s.id, bike_id: v === 'off' ? null : Number(v), at: at() });
    await load(); }); };
  const sl = el('label', 'small dim', 'from '); sl.append(since);
  h4.append(sel, sl, button('Rename', () => { const n = prompt('Name for ' + s.label, s.label); if (n) guard(async () => { await api('PATCH', '/v1/bikes/sets/' + s.id, { label: n }); await load(); }); }),
    button('Set group', () => { const n = prompt('Group (wheels with the same group swap together), e.g. Winter wheels', s.grp || ''); if (n !== null) guard(async () => { await api('PATCH', '/v1/bikes/sets/' + s.id, { group: n }); await load(); }); }),
    button('Add a part', () => partForm(w, s)));
  const ul = el('ul', 'parts'); (s.parts || []).forEach(p => ul.append(partRow(p, b)));
  w.append(h4, ul); return w;
}
function wheelForm(card, b) {  // a new wheel: for this bike (fitted now or kept as a spare), with its usual parts or none
  if (card.querySelector('.wheelform')) { card.querySelector('.wheelform').remove(); return; }
  const f = el('form', 'edit grid wheelform'), out = el('span', 'err');
  const end = el('select'); end.append(new Option('Rear', 'rear'), new Option('Front', 'front'));
  const name = el('input'); name.maxLength = 60; name.required = true; name.placeholder = 'e.g. Winter rear wheel';
  const grp = el('input'); grp.maxLength = 60; grp.placeholder = 'e.g. Winter wheels';
  const now = el('input'); now.type = 'checkbox'; const usual = el('input'); usual.type = 'checkbox'; usual.checked = true;
  const when = el('input'); when.type = 'date';
  const lab = (t, i) => { const l = el('label', null, t + ' '); l.append(i); return l; };
  f.append(lab('Which', end), lab('Name', name), lab('Wheelset group', grp), lab('Since', when));
  const l1 = el('label', 'small'); l1.append(now, document.createTextNode(' Fit it now (the ' + b.label + "'s current one goes into storage)"));
  const l2 = el('label', 'small'); l2.append(usual, document.createTextNode(' With the usual parts for ' + b.label + ' (tyre, tube or sealant, rotor, cassette...)'));
  f.append(l1, l2, el('button', 'primary small', 'Add the wheel'), out);
  f.onsubmit = e => { e.preventDefault(); guard(async () => {
    const r = await api('POST', '/v1/bikes/sets', { rider: rider(), label: name.value.trim(), kind: 'wheel', position: end.value, group: grp.value.trim() || null,
      bike_id: now.checked ? b.id : null, parts: usual.checked, parts_from: b.id, fitted_at: when.value || null });
    msg.textContent = 'Added ' + r.label + (r.parts.length ? ' with ' + r.parts.join(', ').toLowerCase() : '') + (r.took_off.length ? '; ' + r.took_off.join(', ') + ' into storage' : now.checked ? '' : ', in storage') + '.';
    await load(); }, out); };
  card.append(f);
}
function partForm(w, s) {  // a part straight onto a wheel (or set)
  if (w.querySelector('.partform')) { w.querySelector('.partform').remove(); return; }
  const f = el('form', 'edit grid partform'), out = el('span', 'err'), kind = el('select');
  for (const [k, v] of Object.entries(cat.kinds)) if (s.kind !== 'wheel' || v.on === 'wheel') kind.append(new Option(v.name, k));
  const field = (t, type) => { const l = el('label', null, t + ' '), i = el('input'); if (type) i.type = type; l.append(i); f.append(l); return i; };
  const lk = el('label', null, 'Kind '); lk.append(kind); f.append(lk);
  const brand = field('Brand'), model = field('Model'), price = field('Price £', 'number'); price.min = 0; price.step = '0.01';
  f.append(el('button', 'primary small', 'Add'), out);
  f.onsubmit = e => { e.preventDefault(); guard(async () => {
    await api('POST', '/v1/bikes/parts', { rider: rider(), kind: kind.value, position: s.position || null, set_id: s.id, brand: brand.value || null,
      model: model.value || null, price: price.value === '' ? null : Number(price.value) }); await load(); }, out); };
  w.append(f);
}
// --- the workshop: drag parts and wheels between bikes, wheels and shelves (mouse or finger) ---------------------------
const SINGLE_SKIP = new Set(['other', 'axs_battery', 'coin_cells']);
function wsIndex() {  // every part and wheel by id, with where it is
  const parts = {}, sets = {};
  for (const b of g.bikes.filter(x => !x.retired_at)) {
    for (const p of b.parts || []) parts[p.id] = { p, bike: b, where: 'bike' };
    for (const p of b.batteries || []) parts[p.id] = { p, bike: b, where: 'bike' };
    for (const s of b.sets || []) { sets[s.id] = { s, bike: b }; for (const p of s.parts || []) parts[p.id] = { p, bike: b, set: s, where: 'wheel' }; }
  }
  for (const s of g.loose_sets || []) { sets[s.id] = { s, bike: null }; for (const p of s.parts || []) parts[p.id] = { p, set: s, where: 'wheel' }; }
  for (const p of g.shelf || []) parts[p.id] = { p, where: 'shelf' };
  for (const p of g.kit || []) if (!parts[p.id]) parts[p.id] = { p, where: 'kit' };
  return { parts, sets };
}
// things you can pick up: replaced parts and batteries, not services and checks (those stay on the bike's card)
const wsThing = p => (cat.kinds[p.kind] || {}).action === 'replaced' || p.kind === 'axs_battery';
const wsIcon = p => svg(cat.icons[(cat.kinds[p.kind] || {}).icon] || cat.icons.part);
function wsChip(p) {
  const c = el('span', 'ws-chip ' + ((p.wear || {}).state || '')); c.dataset.drag = 'part:' + p.id;
  c.append(wsIcon(p), document.createTextNode((p.label || (cat.kinds[p.kind] || {}).name || p.kind) + (p.position && p.position !== 'front' && p.position !== 'rear' ? '' : '') +
    (p.status === 'shelf' && p.quantity > 1 ? ' ×' + p.quantity : '')));
  c.title = p.name + (p.describe ? ' (' + p.describe + ')' : ''); return c;
}
const wsKm = km => el('span', 'odo', String(Math.round(km || 0)).padStart(5, '0') + ' km');
function wsWheel(s) {
  const z = el('div', 'ws-zone ws-drop'); z.dataset.drop = 'wheel:' + s.id;
  const w = el('div', 'ws-wheel'); w.dataset.drag = 'set:' + s.id; w.append(el('span', null, s.label), wsKm(s.km)); z.append(w);
  for (const p of (s.parts || []).filter(wsThing)) z.append(wsChip(p));
  return z;
}
function workshop() {
  $('wscard').hidden = !g.bikes.some(b => !b.retired_at);
  const row = $('wsbikes'); row.replaceChildren();
  for (const b of g.bikes.filter(x => !x.retired_at)) {
    const t = el('div', 'ws-bike'), h = el('h4'); h.append(el('span', null, b.label), wsKm(b.km)); t.append(h);
    const pic = el('div', 'ws-pic ws-drop'); pic.dataset.drop = 'bike:' + b.id; pic.append(svg(b.art)); t.append(pic);
    const frame = el('div', 'ws-zone ws-drop'); frame.dataset.drop = 'bike:' + b.id; frame.append(el('div', 'zl', 'Frame, drivetrain and brakes'));
    for (const p of (b.parts || []).concat(b.batteries || []).filter(wsThing)) frame.append(wsChip(p));
    const wheels = el('div', 'ws-wheels');
    for (const end of ['front', 'rear']) {
      const s = (b.sets || []).find(x => x.kind === 'wheel' && x.position === end);
      if (s) wheels.append(wsWheel(s));
      else { const z = el('div', 'ws-zone ws-drop'); z.dataset.drop = 'bike:' + b.id; z.append(el('div', 'zl', 'No ' + end + ' wheel: drop one here')); wheels.append(z); }
    }
    t.append(wheels, frame); row.append(t);
  }
  $('wsspares').replaceChildren(...(g.shelf || []).map(wsChip));
  $('wskit').replaceChildren(...(g.kit || []).filter(p => !p.bike_id).map(wsChip));
  $('wsstore').replaceChildren(...(g.loose_sets || []).filter(s => s.kind === 'wheel').map(wsWheel));
}
function wsInfo(kind, id) {  // what a tap shows: the odometer and wear
  const ix = wsIndex(), box = $('wsinfo'); box.replaceChildren();
  document.querySelectorAll('.ws-chip.sel, .ws-wheel.sel').forEach(x => x.classList.remove('sel'));
  document.querySelectorAll('[data-drag="' + kind + ':' + id + '"]').forEach(x => x.classList.add('sel'));
  if (kind === 'set') { const s = ix.sets[id]; if (!s) return;
    box.append(el('b', null, s.s.label + ' '), wsKm(s.s.km), document.createTextNode(s.bike ? ' on ' + s.bike.label : ' in storage')); return; }
  const it = ix.parts[id]; if (!it) return; const p = it.p, w = p.wear;
  box.append(el('b', null, p.name + ' '), w && w.km != null ? wsKm(w.km) : '', document.createTextNode((p.describe ? ' ' + p.describe : '') +
    (w && w.state !== 'untracked' ? ': ' + wearText(w) + ' (' + Math.round(w.share * 100) + '% of its life)' : '') +
    (it.bike ? ', on ' + it.bike.label + (it.set ? ', ' + it.set.label : '') : it.set ? ', ' + it.set.label + ' (storage)' : it.where === 'shelf' ? ', on the shelf' : ', in your kit')));
}
async function wsDrop(kind, id, target) {
  const ix = wsIndex(), [tk, tv] = target.split(':'), say = $('wsmsg'); say.textContent = '';
  if (kind === 'set') {
    if (tk === 'storage') return api('POST', '/v1/bikes/fit', { item: 'set', id, bike_id: null });
    const bikeId = tk === 'bike' ? Number(tv) : tk === 'wheel' ? (ix.sets[tv] || {}).bike && ix.sets[tv].bike.id : null;
    if (!bikeId) throw new Error('Drop a wheel onto a bike, or onto the storage shelf');
    const r = await api('POST', '/v1/bikes/fit', { item: 'set', id, bike_id: bikeId });
    if ((r.warnings || []).length) say.textContent = 'Fitted, but: ' + r.warnings.join('; ');
    return r;
  }
  const it = ix.parts[id]; if (!it) return;
  const p = it.p;
  if (tk === 'shelf' || tk === 'kit' || tk === 'storage') return api('POST', '/v1/bikes/fit', { item: 'part', id, bike_id: null });
  const body = tk === 'wheel' ? { set_id: Number(tv) } : { bike_id: Number(tv) };
  // already one of these there? From the shelf that is a replacement; from anywhere else it has to come off first
  const there = tk === 'wheel' ? ((ix.sets[tv] || {}).s || {}).parts || [] : (g.bikes.find(b => b.id === Number(tv)) || {}).parts || [];
  const same = SINGLE_SKIP.has(p.kind) ? [] : there.filter(x => x.kind === p.kind && x.id !== p.id);
  if (same.length && it.where === 'shelf') {
    let old = same[0];
    if (same.length > 1) { const end = prompt('Replace which: ' + same.map(x => x.position || x.name).join(' or ') + '?', same[0].position || ''); if (end === null) return;
      old = same.find(x => x.position === end.trim().toLowerCase()) || same[0]; }
    if (!confirm('Replace ' + old.name + (old.describe ? ' (' + old.describe + ')' : '') + ' with ' + p.name + (p.describe ? ' (' + p.describe + ')' : '') + '? Its count starts again.')) return;
    return api('POST', '/v1/bikes/service', { rider: rider(), action: 'replaced', part_id: old.id, spare_id: p.id });
  }
  return api('POST', '/v1/bikes/fit', { item: 'part', id, ...body });
}
(() => {  // pointer dragging: works with a mouse, a pen or a finger
  let drag = null;
  const target = (x, y) => { const e = document.elementFromPoint(x, y); return e && e.closest('.ws-drop'); };
  document.addEventListener('pointerdown', e => {
    const src = e.target.closest('[data-drag]'); if (!src || !src.closest('#wscard')) return;
    drag = { src, x: e.clientX, y: e.clientY, ghost: null, over: null }; src.setPointerCapture(e.pointerId);
  });
  document.addEventListener('pointermove', e => {
    if (!drag) return;
    if (!drag.ghost && Math.hypot(e.clientX - drag.x, e.clientY - drag.y) > 6) {
      drag.ghost = drag.src.cloneNode(true); drag.ghost.classList.add('ws-ghost'); document.body.append(drag.ghost); }
    if (!drag.ghost) return;
    drag.ghost.style.left = e.clientX + 'px'; drag.ghost.style.top = e.clientY + 'px';
    const t = target(e.clientX, e.clientY);
    if (t !== drag.over) { if (drag.over) drag.over.classList.remove('over'); drag.over = t; if (t) t.classList.add('over'); }
  });
  const end = e => {
    if (!drag) return; const d = drag; drag = null;
    if (d.over) d.over.classList.remove('over');
    const [kind, id] = d.src.dataset.drag.split(':');
    if (!d.ghost) { wsInfo(kind, Number(id)); return; }
    d.ghost.remove();
    const t = e.type === 'pointerup' && target(e.clientX, e.clientY); if (!t) return;
    guard(async () => { const r = await wsDrop(kind, Number(id), t.dataset.drop); if (r !== undefined) { await load(); wsInfo(kind, Number(id)); } }, $('wsmsg'));
  };
  document.addEventListener('pointerup', end); document.addEventListener('pointercancel', end);
})();
function draw() {
  const due = g.due.filter(d => d.state === 'due' || d.share >= 0.75);
  $('duecard').hidden = !due.length; $('due').replaceChildren();
  for (const d of due) {
    const li = el('li'); li.append(svg(cat.icons[(cat.kinds[d.kind] || {}).icon] || cat.icons.part, 'ic'));
    const b = g.bikes.find(x => x.id === d.bike_id), mid = el('div');
    const t = el('div'); t.append(el('b', null, d.name), document.createTextNode(b ? ' on ' + b.label + ' ' : ' '), el('span', 'chip ' + d.state, d.state === 'due' ? 'due' : 'coming up'));
    mid.append(t);
    if (d.spare) mid.append(el('div', 'small', 'Use the spare on the shelf: ' + d.spare.name + ' (' + d.spare.left + ')'));
    else if (d.on_list) mid.append(el('div', 'dim small', 'On the Outdoors list'));
    if (d.measure) mid.append(el('div', 'dim small', 'Check: ' + d.measure));
    const acts = el('div', 'row');
    const part = findPart(d.part_id);
    if (part) { const [a, label] = act(part.kind); acts.append(button(label, () => a === 'replaced' ? replaceForm(li, part) : service(part, a))); }
    if (!d.spare && !d.on_list && g.settings.to_outdoors !== 'never' && (cat.kinds[d.kind] || {}).bought) acts.append(button('To Outdoors list', () => guard(async () => { await api('POST', '/v1/bikes/parts/' + d.part_id + '/to-list'); await load(); })));
    li.append(mid, acts); $('due').append(li);
  }
  $('askcard').hidden = !g.ask_wet.length; $('ask').replaceChildren();
  for (const r of g.ask_wet.slice().reverse()) {
    const li = el('li'), b = g.bikes.find(x => x.id === r.bike_id), patch = body => guard(async () => { await api('PATCH', '/v1/bikes/rides/' + encodeURIComponent(r.id), body); await load(); });
    li.append(el('span', 'ic', r.doubt ? '⚠' : '🚲'));
    const mid = el('div');
    mid.append(el('b', null, new Date(r.started).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' }) + ', ' + fmt(r.km) + ' km' +
      (r.activity ? ', ' + r.activity : '') + (r.title ? ' (' + r.title + ')' : '')));
    if (!r.bike_checked) {
      const line = el('div', 'small'), sel = el('select');
      g.bikes.filter(x => !x.retired_at).forEach(x => sel.append(new Option(x.label, x.id))); sel.value = r.bike_id;
      sel.onchange = () => patch({ bike_id: Number(sel.value) });
      line.append(document.createTextNode((r.garmin_bike ? 'Garmin had it on ' : 'Bike: ')), sel);
      if (r.doubt) line.append(el('span', 'err', ' Looks wrong: ' + r.doubt + '.'));
      mid.append(line);
      if (r.garmin_link) { const a = el('a', 'small', 'Change it in Garmin Connect too'); a.href = r.garmin_link; a.target = '_blank'; a.rel = 'noopener'; mid.append(a); }
    }
    if (r.wet == null && !r.indoor) mid.append(el('div', 'dim small', r.wet_suggested == null ? 'Wet? No suggestion: ' + (r.wet_why || 'the rain gauges could not tell') :
      'Suggested ' + (r.wet_suggested ? 'wet' : 'dry') + ': ' + (r.wet_why || '')));
    const acts = el('div', 'row');
    const better = r.suggest_bike && g.bikes.find(x => x.id === r.suggest_bike);
    if (better) acts.append(button('It was ' + better.label, () => patch({ bike_id: better.id }), 'primary small'));
    if (!r.bike_checked && b) acts.append(button(b.label + ' is right', () => patch({ bike_checked: true })));
    if (r.wet == null && !r.indoor) acts.append(button('Wet', () => patch({ wet: true })), button('Dry', () => patch({ wet: false })));
    li.append(mid, acts); $('ask').append(li);
  }
  const n = g.counts || {};
  $('askcount').textContent = (n.to_confirm || 0) + ' rides to confirm (the latest 50 are below): ' + (n.unanswered_wet || 0) + ' not yet said wet or dry' +
    (n.suggested_wet ? ' (' + n.suggested_wet + ' look wet by the rain gauge)' : '') + ', ' + (n.bike_unchecked || 0) + ' with a bike to confirm.';
  $('alldry').hidden = !n.unanswered_wet; $('allbikes').hidden = !n.bike_unchecked;
  const f = $('bykind'); f.replaceChildren();
  const rules = (window.garminRules || {}), nice = k => k === 'default' ? 'Any other ride' : k === 'commute' ? 'Commute (any kind)' : k.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
  for (const [k, c] of Object.entries(n.kinds_without_bike || {}).sort((a, b) => b[1] - a[1])) {
    const l = el('label', null, nice(k) + ' (' + c + ') '), sel = el('select'); sel.name = k; sel.append(new Option('leave', ''));
    g.bikes.filter(b => !b.retired_at).forEach(b => sel.append(new Option(b.label, b.id))); if (rules[k] != null) sel.value = String(rules[k]); l.append(sel); f.append(l);
  }
  $('unassignedcard').hidden = !g.unassigned_rides.length; $('unassigned').replaceChildren();
  for (const r of g.unassigned_rides.slice(-30).reverse()) {
    const li = el('li'); li.append(el('span', 'ic', '🚲'));
    li.append(el('div', null, new Date(r.started).toLocaleDateString('en-GB') + ', ' + fmt(r.km) + ' km' + (r.title ? ' (' + r.title + ')' : '')));
    const sel = el('select'); sel.append(new Option('Which bike?', '')); g.bikes.filter(b => !b.retired_at).forEach(b => sel.append(new Option(b.label, b.id)));
    sel.onchange = () => guard(async () => { await api('PATCH', '/v1/bikes/rides/' + encodeURIComponent(r.id), { bike_id: Number(sel.value) }); await load(); });
    li.append(sel); $('unassigned').append(li);
  }
  $('bikes').replaceChildren(...g.bikes.filter(b => !b.retired_at).map(bikeCard));
  workshop();
  $('storecard').hidden = !(g.loose_sets || []).length; $('store').replaceChildren(...(g.loose_sets || []).map(s => wheelBlock(s, null)));
  $('logcard').hidden = !g.log.length; $('log').replaceChildren();
  for (const x of g.log) $('log').append(logRow(x, true));
  $('recentrides').replaceChildren();
  for (const r of g.recent_rides) {
    const li = el('li'); li.append(el('span', 'ic', '🚲'));
    li.append(el('div', 'small', new Date(r.started).toLocaleDateString('en-GB') + ', ' + fmt(r.km) + ' km' + (r.bike ? ' on ' + r.bike : '') + (r.wheels && r.wheels.length ? ' with ' + r.wheels.join(' and ') : '') + (r.title ? ' (' + r.title + ')' : '') + (r.wet ? ', wet' : '')));
    li.append(button('Delete', () => { if (confirm('Delete this ride? Its wear comes off the parts.')) guard(async () => { await api('DELETE', '/v1/bikes/rides/' + encodeURIComponent(r.id)); await load(); }); }));
    $('recentrides').append(li);
  }
  $('retiredcard').hidden = !g.retired.length; $('retired').replaceChildren(...g.retired.map(retiredCard));
  for (const id of ['ridebike', 'importbike']) { const s = $(id), keep = s.value; s.replaceChildren(new Option(id === 'importbike' ? 'ask for each' : '—', '')); g.bikes.filter(b => !b.retired_at).forEach(b => s.append(new Option(b.label, b.id))); s.value = keep; }
  $('shelf').replaceChildren(...g.shelf.map(p => { const li = partRow(p); li.querySelector('b').textContent = p.name + ' × ' + p.quantity; return li; }));
  $('kit').replaceChildren(...g.kit.map(p => partRow(p)));
  const c = g.costs, box = $('costs'); box.replaceChildren();
  const tbl = el('table', 'costs'), head = el('tr'); ['Bike', 'This year', 'km', '£ per km', 'All time'].forEach(x => head.append(el('th', null, x))); tbl.append(head);
  const year = String(new Date().getFullYear());
  for (const b of c.bikes) { const y = b.years[year] || {}, tr = el('tr'); [b.label, y.cost != null ? '£' + y.cost.toFixed(2) : '—', fmt(y.km), y.per_km != null ? '£' + y.per_km.toFixed(3) : '—', '£' + b.total.toFixed(2)].forEach(x => tr.append(el('td', null, x))); tbl.append(tr); }
  box.append(tbl);
  const kinds = Object.values(c.kinds); if (kinds.length) box.append(el('p', null, 'Per 1,000 km: ' + kinds.map(k => k.name + ' £' + k.per_1000km.toFixed(2)).join(', ')));
  $('to_outdoors').value = g.settings.to_outdoors; $('wet_factor').value = g.settings.wet_factor; $('trainer').checked = !!g.settings.trainer_has_cassette; $('weight').value = g.rider.weight_kg || '';
}
function logRow(x, undo) {
  const li = el('li'); li.append(el('span', 'ic', '🔧'));
  li.append(el('div', 'small', new Date(x.at).toLocaleDateString('en-GB') + ': ' + (LABEL[x.action] || x.action) + (x.part_name ? ' ' + x.part_name : '') +
    (x.bike ? ' on ' + x.bike : '') + (x.note ? ', ' + x.note : '') + (x.cost != null ? ' (£' + Number(x.cost).toFixed(2) + ')' : '')));
  if (undo) li.append(x.undo ? button('Undo', () => { if (confirm('Undo this?')) guard(async () => { await api('DELETE', '/v1/bikes/service/' + x.id); await load(); }); })
    : el('span', 'dim small', 'undo what came after first'));
  return li;
}
function retiredCard(b) {
  const box = el('details', 'card'), s = el('summary');
  s.append(el('b', null, b.label), el('span', 'dim small', ' retired ' + new Date(b.retired_at).toLocaleDateString('en-GB') + ', ' + fmt(b.km) + ' km in ' + b.rides + ' rides' +
    (b.cost ? ', £' + b.cost.total.toFixed(2) + ' in parts and work' : '')));
  box.append(s);
  const top = el('div', 'bike'), pic = el('div', 'pic');
  if (b.has_photo) photo('/v1/bikes/bikes/' + b.id + '/photo', b.label).then(i => pic.replaceChildren(i)); else pic.append(svg(b.art));
  const info = el('div');
  if (b.first_ride) info.append(el('p', 'small', 'Ridden ' + new Date(b.first_ride).toLocaleDateString('en-GB') + ' to ' + new Date(b.last_ride).toLocaleDateString('en-GB')));
  if (b.cost && b.cost.per_km != null) info.append(el('p', 'small', '£' + b.cost.per_km.toFixed(3) + ' per km' + (b.cost.purchase ? ', bought for £' + b.cost.purchase : '')));
  if (b.sets.length) info.append(el('p', 'small', 'Wheels still on it: ' + b.sets.join(', ')));
  info.append(button('Bring it back', () => guard(async () => { await api('PATCH', '/v1/bikes/bikes/' + b.id, { retired: false }); await load(); })));
  top.append(pic, info); box.append(top);
  if (b.parts.length) { box.append(el('h4', null, 'Parts on it')); const ul = el('ul', 'parts');
    for (const p of b.parts) { const li = el('li'); li.append(picture(p, cat.icons[(cat.kinds[p.kind] || {}).icon] || cat.icons.part));
      li.append(el('div', 'small', p.name + (p.describe ? ' (' + p.describe + ')' : '') + (p.wear && p.wear.measures && p.wear.measures.km ? ', ' + fmt(p.wear.measures.km.used) + ' km' : ''))); ul.append(li); }
    box.append(ul); }
  if (b.history.length) { box.append(el('h4', null, 'History')); const ul = el('ul', 'parts'); for (const x of b.history) ul.append(logRow(x, false)); box.append(ul); }
  return box;
}
function batteryBox(b) {  // the shifter (and dropper) batteries on this bike: fit one from your kit, add one, take one off
  const box = el('div', 'small'), cfg = b.config || {};
  const wireless = cfg.shifting === 'axs' || cfg.dropper === 'wireless' || (b.batteries || []).length;
  for (const bat of b.batteries || []) {
    const line = el('div', 'row'); line.append(el('span', null, '🔋 ' + bat.name + ': ' + Math.round(bat.wear.share * 100) + '% of a charge used'));
    line.append(button('Take off', () => guard(async () => { await api('POST', '/v1/bikes/fit', { item: 'part', id: bat.id, bike_id: null }); await load(); })));
    box.append(line);
  }
  if (!wireless) return box;
  const spare = g.kit.filter(p => p.kind === 'axs_battery' && p.bike_id !== b.id), row = el('div', 'row');
  if (spare.length) { const sel = el('select'); sel.append(new Option('Fit a battery…', ''));
    for (const p of spare) sel.append(new Option(p.name + (p.bike_id ? ' (now on ' + ((g.bikes.find(x => x.id === p.bike_id) || {}).label || 'another bike') + ')' : ''), p.id));
    sel.onchange = () => { if (sel.value) guard(async () => { await api('POST', '/v1/bikes/fit', { item: 'part', id: Number(sel.value), bike_id: b.id }); await load(); }); };
    row.append(sel); }
  row.append(button('Add a battery', () => guard(async () => {
    const n = prompt('A label for the battery (written on it), e.g. Battery A', 'Battery ' + String.fromCharCode(65 + g.kit.filter(p => p.kind === 'axs_battery').length)); if (!n) return;
    const r = await api('POST', '/v1/bikes/parts', { rider: rider(), kind: 'axs_battery', status: 'kit', label: n });
    await api('POST', '/v1/bikes/fit', { item: 'part', id: r.id, bike_id: b.id }); await load(); })));
  box.append(row); return box;
}
function changeAnswers(card, b) {  // change a bike's answers: parts it now needs are added, ones it no longer has retired
  if (card.querySelector('.answersform')) { card.querySelector('.answersform').remove(); return; }
  const box = el('div', 'edit answersform'), f = el('form', 'grid'), out = el('span', 'err'), cfg = b.config || {};
  box.append(el('div', 'small', 'What ' + b.label + ' has. Parts it now needs are added; ones it no longer has are retired (their history is kept). ' +
    'SRAM AXS shifting, or a wireless dropper, takes AXS batteries: fit them on the card.'));
  answersInto(f, cfg.type, Object.assign({}, cat.defaults[cfg.type], cfg));
  const save = el('button', 'primary small', 'Save'); save.type = 'button';
  save.onclick = () => guard(async () => {
    const c = {}; for (const i of f.elements) if (i.name) c[i.name] = i.type === 'number' ? Number(i.value || 0) : i.type === 'checkbox' ? i.checked : i.value;
    const r = await api('PATCH', '/v1/bikes/bikes/' + b.id, { config: c });
    const ch = r || {}; msg.textContent = b.label + ': ' + ((ch.added || []).length ? 'added ' + ch.added.join(', ') : 'nothing added') +
      ((ch.retired || []).length ? '; retired ' + ch.retired.join(', ') : '') + '.'; await load(); }, out);
  box.append(f, save, out); card.append(box);
}
function findPart(id) { for (const b of g.bikes) for (const p of b.parts.concat(...b.sets.map(s => s.parts), b.batteries)) if (p.id === id) return p; return g.kit.find(p => p.id === id); }
function answers() { answersInto($('answers'), $('btype').value, Object.assign({}, cat.defaults[$('btype').value], { ebike: $('bebike').checked })); }
function answersInto(f, t, d) {  // a bike's answers as a form: for a new bike (the type's defaults) or one to change
  f.replaceChildren();
  const names = { drive: 'Drive', shifting: 'Shifting', front_brake: 'Front brake', rear_brake: 'Rear brake', front_fluid: 'Front brake fluid', rear_fluid: 'Rear brake fluid',
    front_suspension: 'Front suspension', rear_suspension: 'Rear suspension', dropper: 'Dropper post', front_tyre: 'Front tyre', rear_tyre: 'Rear tyre', bottom_bracket: 'Bottom bracket', pedals: 'Pedals' };
  for (const [k, lab] of Object.entries(names)) { const l = el('label', null, lab + ' '), s = el('select'); s.name = k;
    for (const [v, txt] of Object.entries(cat.choices[k])) s.append(new Option(txt, v)); s.value = d[k]; l.append(s); f.append(l); }
  for (const k of ['front_width', 'rear_width']) { const l = el('label', null, (k[0] === 'f' ? 'Front' : 'Rear') + ' tyre width mm '), i = el('input'); i.type = 'number'; i.name = k; i.min = 15; i.max = 130; i.value = d[k]; l.append(i); f.append(l); }
  { const l = el('label', null, 'Front chainrings '), i = el('input'); i.type = 'number'; i.name = 'front_rings'; i.min = 1; i.max = 3; i.value = d.front_rings; l.append(i); f.append(l); }
  if (t === 'tandem') { const l = el('label', null, 'Stoker weight kg '), i = el('input'); i.type = 'number'; i.name = 'stoker_kg'; i.min = 20; i.max = 200; l.append(i); f.append(l); }
  // for its picture: the model (its own frame shape), and its paint
  const pick = (name, lab, opts, value, none) => { const l = el('label', null, lab + ' '), sel = el('select'); sel.name = name;
    sel.append(new Option(none, '')); for (const [v, txt] of opts) sel.append(new Option(txt, v)); sel.value = value || ''; l.append(sel); f.append(l); };
  pick('model', 'Model (for its picture)', Object.entries(cat.models || {}).filter(([, m]) => m.type === t).map(([k, m]) => [k, m.name]), d.model, 'a typical ' + (cat.types[t] || t).toLowerCase());
  pick('paint', 'Frame colour', Object.entries(cat.paints || {}), d.paint, "the theme's colour");
  pick('paint2', 'Second colour (fork and stays; the rear triangle on full suspension)', Object.entries(cat.paints || {}), d.paint2, 'the same');
  pick('pinstripe', 'Pinstripes', Object.entries(cat.paints || {}), d.pinstripe, 'none');
  { const l = el('label', null, 'Fade into the second colour '), i = el('input'); i.type = 'checkbox'; i.name = 'fade'; i.checked = !!d.fade; l.append(i); f.append(l); }
}
async function load() {
  if (!rider()) return;
  g = await api('GET', '/v1/bikes?rider=' + encodeURIComponent(rider()));
  await guard(garmin, $('gmsg'));  // first: the Garmin rules fill in the by-kind choices
  draw();
}
const R = () => '?rider=' + encodeURIComponent(rider());
async function garmin() {
  const s = await api('GET', '/v1/bikes/garmin' + R()); window.garminRules = s.rules;
  $('garmincard').hidden = false; $('gwarn').textContent = s.warn || (s.error ? 'Last sync failed: ' + s.error : '');
  const lr = s.last_result || {};
  $('gstate').textContent = !s.key_set ? 'No Garmin key for this rider yet: rides come only from the CSV import or by hand.'
    : !s.last_sync ? 'Key saved. The first sync reads your whole Garmin history.'
    : 'Last sync ' + new Date(s.last_sync).toLocaleString('en-GB') + ': ' + (lr.added || 0) + ' new rides, ' + (lr.updated || 0) + ' changed, ' +
      (lr.removed || 0) + ' deleted in Garmin' + (lr.from_csv ? ', ' + lr.from_csv + ' imported rides matched' : '') +
      (s.key_expires ? '. The key runs out ' + new Date(s.key_expires).toLocaleDateString('en-GB') + '.' : '.');
  $('gsync').hidden = !s.key_set; $('gforget').hidden = !s.key_set; $('gkeybox').open = !s.key_set;
  $('grulebox').hidden = !s.key_set; $('gweight').checked = s.weight !== false;
  const f = $('grules'); f.replaceChildren();
  const bikes = g.bikes.filter(b => !b.retired_at);
  for (const t of ['default', ...s.types]) {
    const l = el('label', null, (t === 'default' ? 'Any other ride' : t === 'commute' ? 'Commute (any kind)' : t.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase())) + ' '), sel = el('select');
    sel.name = t; sel.append(new Option('ask me', ''));
    for (const b of bikes) sel.append(new Option(b.label, b.id));
    sel.value = s.rules[t] != null ? String(s.rules[t]) : ''; l.append(sel); f.append(l);
  }
}
$('alldry').onclick = () => guard(async () => {
  const keep = $('keepwet').checked, n = g.counts || {}, count = (n.unanswered_wet || 0) - (keep ? (n.suggested_wet || 0) : 0);
  if (!confirm('Mark ' + count + ' rides dry?' + (keep && n.suggested_wet ? ' The ' + n.suggested_wet + ' the rain gauge says were wet stay to answer.' : ''))) return;
  const r = await api('POST', '/v1/bikes/rides/bulk', { rider: rider(), action: 'dry', keep_suggested_wet: keep });
  await load(); $('bulkmsg').textContent = r.changed + ' rides marked dry.'; }, $('bulkmsg'));
$('allbikes').onclick = () => guard(async () => {
  if (!confirm('Keep the bike every ride has now (' + ((g.counts || {}).bike_unchecked || 0) + ' rides)?')) return;
  const r = await api('POST', '/v1/bikes/rides/bulk', { rider: rider(), action: 'bikes_ok' });
  await load(); $('bulkmsg').textContent = r.changed + ' rides kept on their bike.'; }, $('bulkmsg'));
$('bykindgo').onclick = () => guard(async () => {
  const rules = {}; for (const i of $('bykind').elements) if (i.name && i.value) rules[i.name] = Number(i.value);
  if (!Object.keys(rules).length) throw new Error('Choose a bike for at least one kind of ride');
  const r = await api('POST', '/v1/bikes/rides/bulk', { rider: rider(), action: 'bike_by_kind', rules });
  await load(); $('bykindmsg').textContent = r.changed + ' rides given a bike.'; }, $('bykindmsg'));
async function saveRules(apply) {
  const rules = {}; for (const i of $('grules').elements) if (i.name) rules[i.name] = i.value ? Number(i.value) : null;
  const r = await api('PUT', '/v1/bikes/garmin/rules' + R(), { rules, weight: $('gweight').checked, apply });
  $('gmsg').textContent = 'Saved.' + (apply ? ' ' + r.given + ' rides given a bike.' : ''); await load();
}
$('grulesgo').onclick = () => guard(() => saveRules(false), $('gmsg'));
$('grulesapply').onclick = () => guard(() => saveRules(true), $('gmsg'));
$('gkey').onsubmit = e => { e.preventDefault(); guard(async () => {
  const r = await api('PUT', '/v1/bikes/garmin/key' + R(), { value: $('gkeyv').value, expires: $('gexp').value || null });
  $('gkeyv').value = ''; $('gmsg').textContent = r.message + '. Syncing now...';
  const s = await api('POST', '/v1/bikes/garmin/sync' + R());
  $('gmsg').textContent = r.message + '. ' + (s.skipped || ('Read ' + s.rides + ' rides: ' + s.added + ' new.')); await load(); }, $('gmsg')); };
$('gsync').onclick = () => guard(async () => { $('gmsg').textContent = 'Syncing...';
  const s = await api('POST', '/v1/bikes/garmin/sync' + R()); $('gmsg').textContent = 'Read ' + s.rides + ' rides: ' + s.added + ' new, ' + s.updated + ' changed, ' + s.removed + ' deleted in Garmin.'; await load(); }, $('gmsg'));
$('gforget').onclick = () => guard(async () => { if (!confirm('Forget the Garmin key saved for this rider? Rides already here stay.')) return;
  await api('DELETE', '/v1/bikes/garmin/key' + R()); await load(); }, $('gmsg'));
async function start() {
  cat = cat || await api('GET', '/v1/bikes/catalogue');
  if (!$('btype').options.length) { for (const [k, v] of Object.entries(cat.types)) $('btype').append(new Option(v, k)); answers();
    for (const [k, v] of Object.entries(cat.kinds)) if (['bike', 'wheel', 'rider'].includes(v.on)) $('skind').append(new Option(v.name, k)); }
  const rs = (await api('GET', '/v1/bikes/riders')).riders, sel = $('rider'), keep = sel.value || (() => { try { return localStorage.getItem('wa_bike_rider'); } catch (e) { return null; } })();
  sel.replaceChildren(...rs.map(r => new Option(r.name, r.id)));
  if (keep && rs.some(r => r.id === keep)) sel.value = keep;
  $('newrider').open = !rs.length;
  await load();
}
$('rider').onchange = () => { try { localStorage.setItem('wa_bike_rider', rider()); } catch (e) {} guard(load); };
$('btype').onchange = answers; $('bebike').onchange = answers;
$('auth').onsubmit = e => { e.preventDefault(); try { sessionStorage.setItem('wa_token', tok.value); } catch (x) {} guard(start); };
$('addrider').onsubmit = e => { e.preventDefault(); guard(async () => { const r = await api('POST', '/v1/bikes/riders', { name: $('rname').value, weight_kg: $('rkg').value ? Number($('rkg').value) : null });
  $('rname').value = ''; await start(); $('rider').value = r.id; try { localStorage.setItem('wa_bike_rider', r.id); } catch (x) {} await load();
  if ($('rkey').value) {  // the rider's Garmin key, checked with Garmin; the rider is kept even if Garmin refuses it
    const k = $('rkey').value; $('rkey').value = '';
    try { const g = await api('PUT', '/v1/bikes/garmin/key?rider=' + encodeURIComponent(r.id), { value: k, expires: $('rexp').value || null });
      $('gmsg').textContent = g.message + '. Syncing now...'; const s = await api('POST', '/v1/bikes/garmin/sync?rider=' + encodeURIComponent(r.id));
      $('gmsg').textContent = g.message + '. ' + (s.skipped || ('Read ' + s.rides + ' rides: ' + s.added + ' new.'));
    } catch (x) { $('rmsg').textContent = r.name + ' was added, but the Garmin key was not: ' + x.message; }
    await load(); } }, $('rmsg')); };
$('addbikego').onclick = () => guard(async () => {
  if (!rider()) throw new Error('Add yourself as a rider first');
  const cfg = {}; for (const i of $('answers').elements) if (i.name) cfg[i.name] = i.type === 'number' ? Number(i.value || 0) : i.type === 'checkbox' ? i.checked : i.value;
  await api('POST', '/v1/bikes/bikes', { rider: rider(), label: $('blabel').value, type: $('btype').value, ebike: $('bebike').checked, config: cfg,
    fitted_at: $('bdate').value || null, weight_kg: $('bkg').value ? Number($('bkg').value) : null });
  $('blabel').value = ''; await load(); }, $('bmsg'));
$('addride').onsubmit = e => { e.preventDefault(); guard(async () => {
  const r = await api('POST', '/v1/bikes/rides', { rider: rider(), bike_id: $('ridebike').value ? Number($('ridebike').value) : null, started: $('ridewhen').value,
    km: Number($('ridekm').value), hours: Number($('ridehours').value || 0), indoor: $('rideindoor').checked });
  $('ridemsg').textContent = r.wet_suggested == null ? 'Added.' : 'Added. Suggested ' + (r.wet_suggested ? 'wet' : 'dry') + ': ' + r.wet_why + '. Confirm it above.'; await load(); }, $('ridemsg')); };
$('import').onsubmit = e => { e.preventDefault(); guard(async () => {
  const f = $('csv').files[0]; if (!f) throw new Error('Choose the CSV file first');
  const r = await api('POST', '/v1/bikes/rides/import', { rider: rider(), csv: await f.text(), bike_id: $('importbike').value ? Number($('importbike').value) : null, miles: $('miles').checked });
  $('ridemsg').textContent = 'Read ' + r.read + ' rides: ' + r.added + ' added, ' + r.already + ' already here.'; await load(); }, $('ridemsg')); };
$('addspare').onsubmit = e => { e.preventDefault(); guard(async () => {
  await api('POST', '/v1/bikes/parts', { rider: rider(), kind: $('skind').value, status: $('sstatus').value, label: $('slabel').value || null, brand: $('sbrand').value || null,
    model: $('smodel').value || null, quantity: Number($('sqty').value || 1), price: $('sprice').value ? Number($('sprice').value) : null });
  for (const i of ['slabel', 'sbrand', 'smodel', 'sprice']) $(i).value = ''; await load(); }); };
$('settings').onsubmit = e => { e.preventDefault(); guard(async () => {
  await api('PUT', '/v1/bikes/settings?rider=' + encodeURIComponent(rider()), { to_outdoors: $('to_outdoors').value, wet_factor: Number($('wet_factor').value),
    trainer_has_cassette: $('trainer').checked, weight_kg: $('weight').value ? Number($('weight').value) : null });
  $('smsg').textContent = 'Saved.'; await load(); }, $('smsg')); };
guard(start);
</script></main></body></html>"""

page_router = APIRouter()


@page_router.get("/bikes", response_class=HTMLResponse, include_in_schema=False)
async def bikes_page(request: Request) -> str:
    """The page holds no data itself; it calls /v1/bikes."""
    return themed(PAGE, "bikes", request)
