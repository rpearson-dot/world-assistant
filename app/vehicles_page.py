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
/*BASE_CSS*/</style></head><body><main>
<!--NAV-->
<h1>Vehicles</h1>
<form id="auth" class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button class="primary">Load</button></form>
<p id="msg" class="err"></p>

<div class="card"><h2>Your vehicles</h2>
<table id="vehicles"><tbody></tbody></table>
<p id="none" class="dim" hidden>No vehicles yet. Add one below: the Fuel page then shows only the fuels you use.</p>
</div>

<div class="card"><h2 id="formtitle">Add a vehicle</h2>
<form id="addveh" class="row">
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
  </span>
  <button id="save" class="primary">Add vehicle</button>
  <button id="cancel" type="button" hidden>Cancel</button>
</form>
<p id="vmsg" class="err"></p>
<p id="hint" class="dim"></p>
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
       : cheapest ? ', home at the Energy tab cheapest rate, ' + cheapest.pence_per_kwh + 'p/kWh' : ', no home rate (Energy tab has none)');
  return (LABEL[v.fuel] || v.fuel) + ', ' + v.mpg + ' mpg' + (v.fuel_pence_per_litre ? ', your price ' + v.fuel_pence_per_litre + 'p/litre' : '');
}
function button(text, onclick) { const b = document.createElement('button'); b.type = 'button'; b.textContent = text; b.onclick = onclick; return b; }
async function load() {
  msg.textContent = '';
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  saved = await api('GET', '/v1/vehicles');
  try { cheapest = (await api('GET', '/v1/vehicles/home-rate')).cheapest; } catch (e) { cheapest = null; }
  $('vhome').placeholder = cheapest ? 'blank: ' + cheapest.pence_per_kwh + 'p (Energy tab)' : 'e.g. 15';
  const body = document.querySelector('#vehicles tbody'); body.replaceChildren();
  for (const v of saved) {
    const tr = body.insertRow();
    tr.insertCell().textContent = v.name;
    tr.insertCell().textContent = describe(v);
    const td = tr.insertCell();
    td.append(button('Edit', () => edit(v)), ' ', button('Delete', () => {
      if (confirm('Delete ' + v.name + '?')) run(() => api('DELETE', '/v1/vehicles/' + v.id));
    }));
  }
  $('none').hidden = saved.length > 0;
}
function edit(v) {
  editing = v.id; $('formtitle').textContent = 'Change ' + v.name; $('save').textContent = 'Save changes'; $('cancel').hidden = false;
  $('vname').value = v.name; $('vtype').value = v.type; showType(false);
  if (v.type === 'electric') {
    $('vbat').value = v.battery_kwh ?? ''; $('vsoh').value = v.soh_percent ?? 100; $('vmpk').value = v.miles_per_kwh ?? '';
    $('vhome').value = v.home_pence_per_kwh ?? '';
  } else { $('vfuel').value = v.fuel; $('vmpg').value = v.mpg ?? ''; $('vown').value = v.fuel_pence_per_litre ?? ''; }
  $('vname').focus();
}
function reset() {
  editing = null; $('addveh').reset(); $('formtitle').textContent = 'Add a vehicle'; $('save').textContent = 'Add vehicle';
  $('cancel').hidden = true; showType(false);
}
async function run(fn, out = msg) {
  msg.textContent = ''; vmsg.textContent = '';
  try { await fn(); await load(); } catch (e) { out.textContent = e.message; }
}
const num = id => { const v = $(id).value; return v === '' ? null : Number(v); };
$('vtype').onchange = () => showType(true);
$('cancel').onclick = reset;
$('auth').onsubmit = e => { e.preventDefault(); run(() => Promise.resolve()); };
$('addveh').onsubmit = e => {
  e.preventDefault();
  const name = $('vname').value.trim(), type = $('vtype').value;
  const body = type === 'electric'
    ? { name, kind: 'electric', battery_kwh: num('vbat'), soh_percent: num('vsoh') || 100, miles_per_kwh: num('vmpk'),
        home_pence_per_kwh: num('vhome') }
    : { name, kind: type, fuel: $('vfuel').value, mpg: num('vmpg'), fuel_pence_per_litre: type === 'lpg' ? num('vown') : null };
  const id = editing || slug(name);
  if (!id) { vmsg.textContent = 'Give the vehicle a name with at least one letter or digit.'; return; }
  if (!editing && saved.some(v => v.id === id) && !confirm('A vehicle called "' + name + '" is already saved. Replace it?')) return;
  run(async () => { await api('PUT', '/v1/vehicles/' + id, body); reset(); }, vmsg);
};
showType(false);
if (tok.value) run(() => Promise.resolve());
</script></main></body></html>"""

page_router = APIRouter()


@page_router.get("/vehicles", response_class=HTMLResponse, include_in_schema=False)
async def vehicles_page(request: Request) -> str:
    """The page holds no data itself; it asks for the token and calls /v1/vehicles."""
    return themed(PAGE, "vehicles", request)
