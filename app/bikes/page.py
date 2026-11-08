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
    <button class="primary">Add</button></form></details></div>
  <p id="rmsg" class="err"></p>
</div>

<div class="card" id="duecard" hidden><h2>Due</h2><ul class="parts" id="due"></ul></div>
<div class="card" id="askcard" hidden><h2>Rides to confirm</h2>
  <p class="dim small">Which bike each ride was on (Garmin keeps the last one chosen, so it is often wrong), and whether it was wet:
  wet rides wear chains, cassettes and pads faster. The wet suggestion comes from the nearest rain gauge.</p>
  <ul class="parts" id="ask"></ul></div>
<div class="card" id="unassignedcard" hidden><h2>Rides without a bike</h2><ul class="parts" id="unassigned"></ul></div>

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
  for (const bat of b.batteries || []) info.append(el('p', 'small', 'Battery fitted: ' + bat.name + ' (' + Math.round(bat.wear.share * 100) + '% of a charge used)'));
  const photoIn = el('input'); photoIn.type = 'file'; photoIn.accept = 'image/*'; photoIn.capture = 'environment'; photoIn.hidden = true;
  photoIn.onchange = () => guard(async () => { await api('PUT', '/v1/bikes/bikes/' + b.id + '/photo', { data_url: await shrink(photoIn.files[0]) }); await load(); });
  info.append(el('div', 'row'));
  info.lastChild.append(button('Photo', () => photoIn.click()), photoIn,
    button('Retire', () => { if (confirm('Retire ' + b.label + '? Its rides, parts, history and cost are kept, and you can bring it back.')) guard(async () => { await api('PATCH', '/v1/bikes/bikes/' + b.id, { retired: true }); await load(); }); }));
  if (!b.km) info.lastChild.append(button('Delete', () => { if (confirm('Delete ' + b.label + ' and its parts and wheels? This is for a bike added by mistake; it cannot be undone.')) guard(async () => { await api('DELETE', '/v1/bikes/bikes/' + b.id); await load(); }); }));
  top.append(pic, info); card.append(top);
  const list = el('ul', 'parts'); (b.parts || []).forEach(p => list.append(partRow(p, b)));
  const d = el('details'); d.append(el('summary', 'small', 'Frame, drivetrain and brakes (' + (b.parts || []).length + ')'), list); card.append(d);
  for (const s of b.sets || []) {
    const w = el('div', 'wheel'), h4 = el('h4', null, s.label);
    if (s.grp) h4.append(el('span', 'chip', s.grp));
    const sel = el('select'); sel.append(new Option('Move to…', ''));
    for (const other of g.bikes) if (other.id !== b.id && !other.retired_at) sel.append(new Option('onto ' + other.label, other.id));
    sel.append(new Option('off (into storage)', 'off'));
    if (s.grp) for (const other of g.bikes) if (other.id !== b.id && !other.retired_at) sel.append(new Option('whole set "' + s.grp + '" onto ' + other.label, 'g' + other.id));
    sel.onchange = () => { const v = sel.value; if (!v) return; guard(async () => {
      if (v[0] === 'g') await api('POST', '/v1/bikes/fit', { item: 'group', group: s.grp, rider: rider(), bike_id: Number(v.slice(1)) });
      else await api('POST', '/v1/bikes/fit', { item: 'set', id: s.id, bike_id: v === 'off' ? null : Number(v) });
      await load(); }); };
    h4.append(sel, button('Rename', () => { const n = prompt('Name for ' + s.label, s.label); if (n) guard(async () => { await api('PATCH', '/v1/bikes/sets/' + s.id, { label: n }); await load(); }); }),
      button('Set group', () => { const n = prompt('Group (wheels with the same group swap together), e.g. Winter wheels', s.grp || ''); if (n !== null) guard(async () => { await api('PATCH', '/v1/bikes/sets/' + s.id, { group: n }); await load(); }); }));
    const ul = el('ul', 'parts'); (s.parts || []).forEach(p => ul.append(partRow(p, b)));
    w.append(h4, ul); card.append(w);
  }
  return card;
}
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
  $('unassignedcard').hidden = !g.unassigned_rides.length; $('unassigned').replaceChildren();
  for (const r of g.unassigned_rides.slice(-30).reverse()) {
    const li = el('li'); li.append(el('span', 'ic', '🚲'));
    li.append(el('div', null, new Date(r.started).toLocaleDateString('en-GB') + ', ' + fmt(r.km) + ' km' + (r.title ? ' (' + r.title + ')' : '')));
    const sel = el('select'); sel.append(new Option('Which bike?', '')); g.bikes.filter(b => !b.retired_at).forEach(b => sel.append(new Option(b.label, b.id)));
    sel.onchange = () => guard(async () => { await api('PATCH', '/v1/bikes/rides/' + encodeURIComponent(r.id), { bike_id: Number(sel.value) }); await load(); });
    li.append(sel); $('unassigned').append(li);
  }
  $('bikes').replaceChildren(...g.bikes.filter(b => !b.retired_at).map(bikeCard));
  $('logcard').hidden = !g.log.length; $('log').replaceChildren();
  for (const x of g.log) $('log').append(logRow(x, true));
  $('recentrides').replaceChildren();
  for (const r of g.recent_rides) {
    const li = el('li'); li.append(el('span', 'ic', '🚲'));
    li.append(el('div', 'small', new Date(r.started).toLocaleDateString('en-GB') + ', ' + fmt(r.km) + ' km' + (r.bike ? ' on ' + r.bike : '') + (r.title ? ' (' + r.title + ')' : '') + (r.wet ? ', wet' : '')));
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
function findPart(id) { for (const b of g.bikes) for (const p of b.parts.concat(...b.sets.map(s => s.parts), b.batteries)) if (p.id === id) return p; return g.kit.find(p => p.id === id); }
function answers() {
  const f = $('answers'), t = $('btype').value, d = Object.assign({}, cat.defaults[t], { ebike: $('bebike').checked }); f.replaceChildren();
  const names = { drive: 'Drive', shifting: 'Shifting', front_brake: 'Front brake', rear_brake: 'Rear brake', front_fluid: 'Front brake fluid', rear_fluid: 'Rear brake fluid',
    front_suspension: 'Front suspension', rear_suspension: 'Rear suspension', dropper: 'Dropper post', front_tyre: 'Front tyre', rear_tyre: 'Rear tyre', bottom_bracket: 'Bottom bracket', pedals: 'Pedals' };
  for (const [k, lab] of Object.entries(names)) { const l = el('label', null, lab + ' '), s = el('select'); s.name = k;
    for (const [v, txt] of Object.entries(cat.choices[k])) s.append(new Option(txt, v)); s.value = d[k]; l.append(s); f.append(l); }
  for (const k of ['front_width', 'rear_width']) { const l = el('label', null, (k[0] === 'f' ? 'Front' : 'Rear') + ' tyre width mm '), i = el('input'); i.type = 'number'; i.name = k; i.min = 15; i.max = 130; i.value = d[k]; l.append(i); f.append(l); }
  { const l = el('label', null, 'Front chainrings '), i = el('input'); i.type = 'number'; i.name = 'front_rings'; i.min = 1; i.max = 3; i.value = d.front_rings; l.append(i); f.append(l); }
  if (t === 'tandem') { const l = el('label', null, 'Stoker weight kg '), i = el('input'); i.type = 'number'; i.name = 'stoker_kg'; i.min = 20; i.max = 200; l.append(i); f.append(l); }
}
async function load() {
  if (!rider()) return;
  g = await api('GET', '/v1/bikes?rider=' + encodeURIComponent(rider()));
  draw();
}
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
  $('rname').value = ''; await start(); $('rider').value = r.id; try { localStorage.setItem('wa_bike_rider', r.id); } catch (x) {} await load(); }, $('rmsg')); };
$('addbikego').onclick = () => guard(async () => {
  if (!rider()) throw new Error('Add yourself as a rider first');
  const cfg = {}; for (const i of $('answers').elements) if (i.name) cfg[i.name] = i.type === 'number' ? Number(i.value || 0) : i.value;
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
