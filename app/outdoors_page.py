"""The Outdoors page: the family's KitchenOwl "Outdoors" list, priced every day at GO Outdoors and Decathlon.

The page holds no data itself; it calls /v1/outdoors. Everything from KitchenOwl and the shops is put on the page as
text.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant outdoors</title>
<style>
/*BASE_CSS*/
.thing { border-top: 1px solid var(--line, #ddd); padding: 10px 0; }
.thing:first-child { border-top: 0; }
.thing h3 { margin: 0 0 4px; font-size: 16px; display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.chip { font-size: 12px; padding: 1px 8px; border-radius: 9px; background: var(--chip); white-space: nowrap; font-weight: normal; }
.chip.down { background: var(--good-bg); color: var(--good); }
.prods { margin: 4px 0 0; padding: 0; list-style: none; font-size: 14px; }
.prods li { margin: 3px 0; }
form.add { display: grid; gap: 8px; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); align-items: end; }
</style></head><body><main>
<!--NAV-->
<h1>Outdoors</h1>
<form id="auth" class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button class="primary">Load</button></form>
<p id="msg" class="err"></p>

<div class="card"><h2>Add something</h2>
<p class="dim" id="listline">The things are a KitchenOwl list, so anyone can add one in the KitchenOwl app, or here.</p>
<form id="add" class="add">
  <label>What <input id="name" placeholder="Two-man tent" maxlength="120" required></label>
  <label>Product link <input id="link" placeholder="GO Outdoors or Decathlon (optional)" maxlength="300"></label>
  <label>Up to £ <input id="max" type="number" min="1" step="0.01" placeholder="optional"></label>
  <button class="primary">Add</button>
</form>
<p class="dim">Priced every morning, or now with <b>Check now</b>. With a link, that product is followed exactly (Decathlon
is only read this way); without one, GO Outdoors, Bikesy, Sigma Sports and Tradeinn are searched for the name and the three
cheapest matches at each are followed.
In KitchenOwl, put the link (and <b>under £300</b>, or <b>must: gore-tex</b>) in the item's description. A price that
falls shows as a drop for a week. Nothing is ever bought.</p>
<p id="amsg" class="err"></p>
<details><summary class="dim">Which KitchenOwl list</summary>
<form id="listform" class="bar"><select id="listsel"></select><button>Use this list</button></form>
<p id="lmsg" class="err"></p></details>
</div>

<div class="card"><h2>Following</h2>
<div class="row"><button id="check" type="button" class="primary">Check now</button><span id="last" class="dim"></span></div>
<p id="cmsg" class="err"></p>
<div id="list"><p class="dim">Loading...</p></div>
</div>

<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function button(text, onclick) { const b = el('button', null, text); b.type = 'button'; b.onclick = onclick; return b; }
const SYM = { GBP: '£', EUR: '€', USD: '$' };
const money = (v, cur) => v == null ? '' : (SYM[cur || 'GBP'] || (cur + ' ')) + Number(v).toFixed(2);
const day = s => s ? new Date(s).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' }) : '';
const shop = r => ({ gooutdoors: 'GO Outdoors', decathlon: 'Decathlon', bikesy: 'Bikesy', tradeinn: 'Tradeinn', sigma: 'Sigma Sports' }[r] || r);
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
async function api(method, path, body) {
  const r = await fetch(path, { method, headers: { Authorization: 'Bearer ' + tok.value, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined });
  if (r.status === 204) return null;
  let d = null; try { d = await r.json(); } catch (e) {}
  if (!r.ok) { let m = d && d.detail; if (Array.isArray(m)) m = m.map(x => x.msg).join('; '); throw new Error(m || ('HTTP ' + r.status)); }
  return d;
}
async function guard(fn, out) { out.textContent = ''; try { await fn(); } catch (e) { out.textContent = e.message; } }

function product(p) {
  const li = el('li');
  const name = p.url ? Object.assign(el('a', null, p.name), { href: p.url, target: '_blank', rel: 'noopener noreferrer' }) : el('span', null, p.name);
  const c = p.currency;
  li.append(el('b', null, (p.shop ? p.shop + ' (via ' + shop(p.retailer) + ')' : shop(p.retailer)) + ': '), name, ' ' + money(p.price, c));
  const bits = [];
  if (p.dropped && p.previous_price != null) bits.push('down from ' + money(p.previous_price, c) + ' on ' + day(p.dropped_at));
  if (p.was_price) bits.push('was ' + money(p.was_price, c));
  if (p.lowest_price != null && p.price != null && p.lowest_price < p.price) bits.push('lowest ' + money(p.lowest_price, c) + ' on ' + day(p.lowest_at));
  if (p.available === false) bits.push('out of stock');
  if (c && c !== 'GBP') bits.push('in ' + c + ', so not compared with the £ prices');
  if (bits.length) li.append(el('span', 'dim', ' · ' + bits.join(' · ')));
  if (p.error) li.append(el('span', 'err', ' · could not check it since ' + day(p.error_at) + ': ' + p.error + ' (the price shown is the last one read)'));
  return li;
}
function thing(it) {
  const d = el('div', 'thing'), h = el('h3', null, it.name);
  if (it.dropped.length) h.append(el('span', 'chip down', 'price drop'));
  if (it.max_price) h.append(el('span', 'chip' + (it.under.length ? ' down' : ''), (it.under.length ? 'under ' : 'up to ') + money(it.max_price)));
  d.append(h);
  if (it.products.length) { const ul = el('ul', 'prods'); it.products.forEach(p => ul.append(product(p))); d.append(ul); }
  const fav = (it.preferred || [])[0];
  if (fav && it.cheapest != null && fav.price > it.cheapest)
    d.append(el('p', 'dim', 'Preferred shop: ' + (fav.shop || shop(fav.retailer)) + ' has it at ' + money(fav.price) + ', '
      + money(fav.price - it.cheapest) + ' more than the cheapest.'));
  else d.append(el('p', 'dim', 'Not priced yet.'));
  const how = it.links.length ? 'Follows the linked product' + (it.links.length > 1 ? 's' : '') : 'Searches GO Outdoors, Bikesy, Sigma Sports and Tradeinn for "' + it.name + '"';
  d.append(el('p', 'dim', how + (it.must.length ? ', must have: ' + it.must.join(', ') : '')));
  const row = el('div', 'row'), m = el('span', 'err');
  row.append(button('Check now', () => guard(async () => { show(await api('POST', '/v1/outdoors/check', { item: it.key })); }, m)),
    button('Tick off', () => { if (confirm('Tick ' + it.name + ' off the Outdoors list in KitchenOwl? Its prices are kept.'))
      guard(async () => { await api('DELETE', '/v1/outdoors/items/' + encodeURIComponent(it.key)); await load(); }, m); }), m);
  d.append(row);
  return d;
}
function show(data) {
  $('listline').textContent = 'The things are the "' + data.list.name + '" list in KitchenOwl, so anyone can add one in the KitchenOwl app, or here.';
  const list = $('list'); list.replaceChildren();
  if (!data.items.length) list.append(el('p', 'dim', 'Nothing on the list yet.'));
  data.items.forEach(it => list.append(thing(it)));
  const l = data.last_check;
  const downs = l && l.sources ? Object.entries(l.sources).filter(([, v]) => v !== 'ok').map(([k, v]) => shop(k.split(':')[0]) + (k.includes(':') ? ' (' + k.split(':')[1] + ')' : '') + ' ' + v) : [];
  $('last').textContent = l ? ' Last checked ' + day(l.at) + (downs.length ? '. Not reached: ' + downs.join('; ') : (l.errors ? ' (some could not be read: ' + Object.values(l.errors).flat().join('; ') + ')' : '')) : ' Not checked yet.';
}
async function lists() {
  const st = await api('GET', '/v1/outdoors/settings'), sel = $('listsel'); sel.replaceChildren();
  const def = el('option', null, 'the list called "' + st.default_name + '"'); def.value = ''; sel.append(def);
  st.lists.forEach(x => { const o = el('option', null, x.name + ' (' + x.items + ')'); o.value = x.id; if (x.id === st.list_id) o.selected = true; sel.append(o); });
  if (st.problem) $('lmsg').textContent = st.problem;
}
async function load() { try { show(await api('GET', '/v1/outdoors')); } finally { await lists(); } }

$('auth').onsubmit = e => { e.preventDefault(); try { sessionStorage.setItem('wa_token', tok.value); } catch (x) {} guard(load, msg); };
$('add').onsubmit = e => { e.preventDefault(); guard(async () => {
  const bits = [];
  if ($('link').value.trim()) bits.push($('link').value.trim());
  if ($('max').value) bits.push('under £' + Number($('max').value));
  const r = await api('POST', '/v1/outdoors/items', { name: $('name').value.trim(), description: bits.join(' ') });
  $('name').value = $('link').value = $('max').value = '';
  show(r);
}, $('amsg')); };
$('check').onclick = () => guard(async () => { $('check').disabled = true;
  try { show(await api('POST', '/v1/outdoors/check')); } finally { $('check').disabled = false; } }, $('cmsg'));
$('listform').onsubmit = e => { e.preventDefault(); guard(async () => {
  await api('PUT', '/v1/outdoors/settings', { list_id: $('listsel').value ? Number($('listsel').value) : null }); await load();
}, $('lmsg')); };
guard(load, msg);
</script></main></body></html>"""

page_router = APIRouter()


@page_router.get("/outdoors", response_class=HTMLResponse, include_in_schema=False)
async def outdoors_page(request: Request) -> str:
    """The page holds no data itself; it asks for the token and calls /v1/outdoors."""
    return themed(PAGE, "outdoors", request)
