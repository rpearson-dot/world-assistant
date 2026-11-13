"""The Vehicles page: the cars you drive, what they run on and how far they go on it.

What a vehicle runs on decides which fuel the Fuel page offers (only your vehicles' fuels, unless you ask for
all) and how a trip is costed. The page holds no data itself; it calls /v1/vehicles.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant vehicles</title>
<style>
/*BASE_CSS*/
.veh { border-bottom: 1px solid var(--line); }
.veh:last-child { border-bottom: 0; }
.veh > summary { cursor: pointer; padding: 10px 2px; display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: baseline; list-style: none; }
.veh > summary::-webkit-details-marker { display: none; }
.veh > summary::before { content: '▸'; color: var(--dim); width: 1em; }
.veh[open] > summary::before { content: '▾'; }
.veh .vn { font-weight: 650; }
.veh .vs { color: var(--dim); font-size: .9em; }
.veh .vs.err { color: var(--bad); }
.veh .body { padding: 0 2px 12px 1.4em; display: grid; gap: 8px; }
.veh .acts { display: flex; gap: 8px; flex-wrap: wrap; }
.vform { border: 1px solid var(--line); border-radius: 12px; padding: 10px 12px; margin: 6px 0; }
.vform h3 { margin: 0 0 6px; font-size: 1em; }
#addpane { padding-top: 10px; }
</style></head><body><main>
<!--NAV-->
<h1>Vehicles</h1>
<form id="auth" class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button class="primary">Load</button></form>
<p id="msg" class="err"></p>

<div class="card"><h2>Your vehicles</h2>
<p id="none" class="dim" hidden>No vehicles yet. Add one: the Fuel page then shows only the fuels you use.</p>
<div id="vehicles"></div>
<p id="cmsg" class="err"></p>
<div id="addpane"><button id="addbtn" type="button" class="primary">Add a vehicle</button></div>
<p class="dim" id="checksnote">Give a vehicle its registration: DVLA says its tax and MOT status, DVSA its MOT history, advisories and any
outstanding recall. Checked every Monday, and with Check now. Needs the keys on Settings, Keys (DVLA, DVSA or both).</p>
</div>

<div id="formhome" hidden>
<form id="addveh" class="vform">
  <h3 id="formtitle">Add a vehicle</h3>
  <div class="row">
  <label>Name <input id="vname" placeholder="e.g. Toyota Estima" required maxlength="80"></label>
  <label>Runs on <select id="vtype">
    <option value="petrol">Petrol</option><option value="diesel">Diesel</option>
    <option value="lpg">LPG</option><option value="electric">Electric</option></select></label>
  <span id="liquid" class="row">
    <label id="gradel">Grade <select id="vfuel"></select></label>
    <label>Real-world mpg (UK) <input id="vmpg" type="number" step="0.1" min="5" max="150" placeholder="e.g. 33"></label>
    <label id="ownl">Your price, p/litre <input id="vown" type="number" step="0.1" min="10" max="400" placeholder="e.g. 85"></label>
  </span>
  <span id="ev" class="row" hidden>
    <label>Battery size, kWh <input id="vbat" type="number" step="0.1" min="5" max="300" placeholder="e.g. 30"></label>
    <label>Battery health, % <input id="vsoh" type="number" step="1" min="31" max="100" value="100"></label>
    <label>Miles per kWh <input id="vmpk" type="number" step="0.1" min="0.5" max="10" placeholder="e.g. 3.6"></label>
    <label>Home electricity, p/kWh <input id="vhome" type="number" step="0.1" min="0" max="200" placeholder="blank: Energy tab"></label>
    <label>Rapid charging <select id="vconn"><option value="">not set</option><option>CCS</option><option>CHAdeMO</option><option>Type 2</option></select></label>
  </span>
  </div>
  <div class="row"><button id="save" class="primary">Add vehicle</button>
  <button id="cancel" type="button">Cancel</button></div>
  <p id="vmsg" class="err"></p>
  <p id="hint" class="dim"></p>
</form>
</div>

<script>
const $ = id => document.getElementById(id);
const tok = $('t'), msg = $('msg'), vmsg = $('vmsg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
const GRADES = {
  petrol: [['E10', 'Unleaded (E10)'], ['E5', 'Super unleaded (E5)']],
  diesel: [['B7_STANDARD', 'Diesel (B7)'], ['B7_PREMIUM', 'Premium diesel'], ['B10', 'B10 biodiesel'], ['HVO', 'HVO']],
  lpg: [['LPG', 'LPG']],
};
const LABEL = Object.fromEntries(Object.values(GRADES).flat());
const HINT = {
  petrol: 'Prices come from Fuel Finder for every UK station.',
  diesel: 'Prices come from Fuel Finder for every UK station.',
  lpg: 'Fuel Finder publishes no LPG prices, so trip costs use your price per litre. A dual-fuel car can be added twice, once as LPG and once as petrol.',
  electric: 'Leave Home electricity blank to cost trips at the cheapest rate on the Energy tab (charging at the cheap time), or give your own. Public charging prices are not published.',
};
const FIELD = { battery_kwh: 'Battery size', soh_percent: 'Battery health', miles_per_kwh: 'Miles per kWh',
  home_pence_per_kwh: 'Home electricity', mpg: 'Real-world mpg', name: 'Name', fuel: 'Grade',
  fuel_pence_per_litre: 'Your price' };
let saved = [], editing = null, cheapest = null;  // cheapest: the Energy tab's cheapest rate, if it has rates
const slug = s => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40);
async function api(method, path, body) {
  const r = await fetch(path, { method, headers: { Authorization: 'Bearer ' + tok.value,
    'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) {
    let d = r.status; try { d = (await r.json()).detail; } catch (e) {}
    if (Array.isArray(d)) d = d.map(x => {  // say which box and why
      const f = (x.loc || []).filter(k => k !== 'body').pop();
      return (typeof f === 'string' ? (FIELD[f] || f.replace(/_/g, ' ')) + ': ' : '') + (x.msg || '').replace(/^Value error, /, '');
    }).join('; ');
    throw new Error(typeof d === 'string' ? d : JSON.stringify(d));
  }
  return r.status === 204 ? null : r.json();
}
function showType(keepGrade) {
  const t = $('vtype').value, ev = t === 'electric';
  $('liquid').hidden = ev; $('ev').hidden = !ev;
  if (!ev) {
    const was = $('vfuel').value;
    $('vfuel').replaceChildren(...GRADES[t].map(([v, l]) => new Option(l, v)));
    if (keepGrade && GRADES[t].some(([v]) => v === was)) $('vfuel').value = was;
    $('gradel').hidden = t === 'lpg';
    $('ownl').hidden = t !== 'lpg';
  }
  $('vmpg').required = !ev; $('vown').required = t === 'lpg';
  for (const id of ['vbat', 'vmpk']) $(id).required = ev;
  $('hint').textContent = HINT[t];
}
function describe(v) {
  if (v.type === 'electric') return 'Electric, ' + v.battery_kwh + ' kWh at ' + v.soh_percent + '% health, ' + v.miles_per_kwh + ' mi/kWh'
    + (v.home_pence_per_kwh ? ', home ' + v.home_pence_per_kwh + 'p/kWh'
       : cheapest ? ', home at the Energy tab cheapest rate, ' + cheapest.pence_per_kwh + 'p/kWh' : ', no home rate (Energy tab has none)')
    + (v.connector ? ', charges on ' + v.connector : '');
  return (LABEL[v.fuel] || v.fuel) + ', ' + v.mpg + ' mpg' + (v.fuel_pence_per_litre ? ', your price ' + v.fuel_pence_per_litre + 'p/litre' : '');
}
function button(text, onclick, cls) { const b = document.createElement('button'); b.type = 'button'; b.textContent = text; b.onclick = onclick; if (cls) b.className = cls; return b; }
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const openIds = new Set();
let checkData = null;  // null: tax and MOT switched off
async function load() {
  msg.textContent = '';
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  saved = await api('GET', '/v1/vehicles');
  try { cheapest = (await api('GET', '/v1/vehicles/home-rate')).cheapest; } catch (e) { cheapest = null; }
  $('vhome').placeholder = cheapest ? 'blank: ' + cheapest.pence_per_kwh + 'p (Energy tab)' : 'e.g. 15';
  try { checkData = await api('GET', '/v1/vehicle-checks'); } catch (e) { checkData = /switched off/i.test(e.message) ? null : { vehicles: [], keys: {}, error: e.message }; }
  $('checksnote').hidden = !checkData;
  draw();
}
function draw() {
  parkForm();
  const box = $('vehicles'); box.replaceChildren();
  const by = Object.fromEntries(((checkData || {}).vehicles || []).map(v => [v.id, v]));
  for (const v of saved) {
    const c = by[v.id], det = el('details', null, 'veh'); det.dataset.id = v.id; det.open = openIds.has(v.id);
    det.ontoggle = () => { if (det.open) openIds.add(v.id); else openIds.delete(v.id); };
    const sum = el('summary'); sum.append(el('span', v.name, 'vn'));
    if (c) sum.append(el('span', c.registration, 'vs'));
    const status = c ? (c.warnings.length ? c.warnings.join('; ') : [c.mot_due && 'MOT until ' + c.mot_due, c.tax_due && 'tax until ' + c.tax_due].filter(Boolean).join(' · ')) : '';
    if (status) sum.append(el('span', status, 'vs' + (c.warnings.length ? ' err' : '')));
    det.append(sum);
    const body = el('div', null, 'body'); body.append(el('div', describe(v)));
    if (checkData) {
      const row = el('div', null, 'row');
      const reg = el('input'); reg.maxLength = 12; reg.placeholder = 'Registration'; reg.value = c ? c.registration : ''; reg.size = 10;
      reg.setAttribute('aria-label', 'Registration of ' + v.name);
      row.append(el('span', 'Registration', 'dim'), reg, button('Save', () => run(() => api('PUT', '/v1/vehicle-checks/' + v.id + '/registration', { registration: reg.value }), $('cmsg'))));
      if (c) row.append(button('Check now', () => run(() => api('POST', '/v1/vehicle-checks/' + v.id + '/check'), $('cmsg'))));
      body.append(row);
      if (c) body.append(el('p', c.text, c.warnings.length ? 'err' : 'dim'));
      if (c && c.last_mot && c.last_mot.advisories.length) {
        const d = el('details'); d.append(el('summary', 'Last MOT advisories (' + c.last_mot.advisories.length + ')'));
        const ul = el('ul'); for (const a of c.last_mot.advisories) ul.append(el('li', a)); d.append(ul); body.append(d);
      }
    }
    const acts = el('div', null, 'acts');
    acts.append(button('Edit', () => edit(v, body)), button('Remove', () => {
      if (confirm('Remove ' + v.name + '? Its tax and MOT record goes too.')) run(() => api('DELETE', '/v1/vehicles/' + v.id));
    }));
    body.append(acts); det.append(body); box.append(det);
  }
  $('none').hidden = saved.length > 0;
  $('addbtn').textContent = saved.length ? 'Add another vehicle' : 'Add a vehicle';
  $('addbtn').hidden = false;
}
function parkForm() { $('formhome').append($('addveh')); }
function edit(v, where) {
  editing = v.id; $('formtitle').textContent = 'Change ' + v.name; $('save').textContent = 'Save changes';
  $('vname').value = v.name; $('vtype').value = v.type; showType(false);
  if (v.type === 'electric') {
    $('vbat').value = v.battery_kwh ?? ''; $('vsoh').value = v.soh_percent ?? 100; $('vmpk').value = v.miles_per_kwh ?? '';
    $('vhome').value = v.home_pence_per_kwh ?? ''; $('vconn').value = v.connector || '';
  } else { $('vfuel').value = v.fuel; $('vmpg').value = v.mpg ?? ''; $('vown').value = v.fuel_pence_per_litre ?? ''; }
  $('addbtn').hidden = false; where.append($('addveh')); $('vname').focus();
}
function adding() {
  reset(); $('addbtn').hidden = true; $('addpane').append($('addveh')); $('vname').focus();
}
function reset() {
  editing = null; $('addveh').reset(); $('formtitle').textContent = 'Add a vehicle'; $('save').textContent = 'Add vehicle';
  $('vmsg').textContent = ''; showType(false);
}
function closeForm() { reset(); parkForm(); $('addbtn').hidden = false; }
async function run(fn, out = msg) {
  msg.textContent = ''; vmsg.textContent = ''; $('cmsg').textContent = '';
  try { await fn(); await load(); } catch (e) { out.textContent = e.message; }
}
const num = id => { const v = $(id).value; return v === '' ? null : Number(v); };
$('vtype').onchange = () => showType(true);
$('cancel').onclick = closeForm;
$('addbtn').onclick = adding;
$('auth').onsubmit = e => { e.preventDefault(); run(() => Promise.resolve()); };
$('addveh').onsubmit = e => {
  e.preventDefault();
  const name = $('vname').value.trim(), type = $('vtype').value;
  const body = type === 'electric'
    ? { name, kind: 'electric', battery_kwh: num('vbat'), soh_percent: num('vsoh') || 100, miles_per_kwh: num('vmpk'),
        home_pence_per_kwh: num('vhome'), connector: $('vconn').value || null }
    : { name, kind: type, fuel: $('vfuel').value, mpg: num('vmpg'), fuel_pence_per_litre: type === 'lpg' ? num('vown') : null };
  const id = editing || slug(name);
  if (!id) { vmsg.textContent = 'Give the vehicle a name with at least one letter or digit.'; return; }
  if (!editing && saved.some(v => v.id === id) && !confirm('A vehicle called "' + name + '" is already saved. Replace it?')) return;
  run(async () => { await api('PUT', '/v1/vehicles/' + id, body); if (!editing) openIds.add(id); closeForm(); }, vmsg);
};
showType(false);
if (tok.value) run(() => Promise.resolve());
</script></main></body></html>"""

page_router = APIRouter()


@page_router.get("/vehicles", response_class=HTMLResponse, include_in_schema=False)
async def vehicles_page(request: Request) -> str:
    """The page holds no data itself; it asks for the token and calls /v1/vehicles."""
    return themed(PAGE, "vehicles", request)
