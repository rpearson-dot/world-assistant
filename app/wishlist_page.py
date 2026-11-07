"""The Wishlist page: the family's KitchenOwl "Wishlist" list, looked for in Aldi's Specialbuys and the Middle of
Lidl, with when each was last seen.

The page holds no data itself; it calls /v1/wishlist. Everything from KitchenOwl and the retailers is put on the page
as text.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant wishlist</title>
<style>
/*BASE_CSS*/
.wish { border-top: 1px solid var(--line, #ddd); padding: 10px 0; }
.wish:first-child { border-top: 0; }
.wish h3 { margin: 0 0 4px; font-size: 16px; display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.chip { font-size: 12px; padding: 1px 8px; border-radius: 9px; background: var(--chip); white-space: nowrap; font-weight: normal; }
.chip.coming, .chip.on { background: var(--good-bg); color: var(--good); }
.seen { margin: 4px 0 0; padding: 0; list-style: none; font-size: 14px; }
.seen li { margin: 3px 0; }
.wish .row { margin-top: 6px; }
details summary { cursor: pointer; }
form.add { display: grid; gap: 8px; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); align-items: end; }
</style></head><body><main>
<!--NAV-->
<h1>Wishlist</h1>
<form id="auth" class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button class="primary">Load</button></form>
<p id="msg" class="err"></p>

<div class="card"><h2>Add something to look out for</h2>
<p class="dim" id="listline">The wishes are a KitchenOwl list, so anyone can add one in the KitchenOwl app, or here.</p>
<form id="add" class="add">
  <label>What <input id="name" placeholder="Kamado BBQ" maxlength="120" required></label>
  <label>Must have these words <input id="must" placeholder="kamado (optional)" maxlength="80"></label>
  <label>Up to £ <input id="max" type="number" min="1" step="0.01" placeholder="optional"></label>
  <button class="primary">Add</button>
</form>
<p class="dim">Aldi's Specialbuys and the Middle of Lidl are searched on Wednesday and Saturday evenings, before each
week's new range goes on sale, or now with <b>Check now</b>. A product counts when its name has every word of the item
(bbq also finds barbecue and grill). In KitchenOwl, an item's description can say <b>under £300</b> or
<b>must: kamado</b>. Tick an item off in KitchenOwl once it is bought. Nothing is ever bought here.</p>
<p id="amsg" class="err"></p>
<details><summary class="dim">Which KitchenOwl list</summary>
<form id="listform" class="bar"><select id="listsel"></select><button>Use this list</button></form>
<p id="lmsg" class="err"></p></details>
</div>

<div class="card"><h2>Looking out for</h2>
<div class="row"><button id="check" type="button" class="primary">Check now</button><span id="last" class="dim"></span></div>
<p id="cmsg" class="err"></p>
<div id="list"><p class="dim">Loading...</p></div>
</div>

<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function button(text, onclick, cls) { const b = el('button', cls || null, text); b.type = 'button'; b.onclick = onclick; return b; }
const money = v => v == null ? '' : '£' + Number(v).toFixed(2);
const day = s => s ? new Date(s.length === 10 ? s + 'T12:00:00' : s).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }) : '';
const shop = r => ({ aldi: 'Aldi', lidl: 'Lidl' }[r] || r);
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

function sighting(s) {
  const li = el('li');
  const name = s.url ? Object.assign(el('a', null, s.name), { href: s.url, target: '_blank', rel: 'noopener noreferrer' }) : el('span', null, s.name);
  li.append(el('b', null, shop(s.retailer) + ': '), name);
  if (s.price != null) li.append(' ' + money(s.price));
  const when = s.status === 'coming' ? 'in store ' + day(s.on_sale) : s.status === 'on sale' ? 'on sale' + (s.on_sale ? ' since ' + day(s.on_sale) : '')
    : s.status === 'sold out' ? 'sold out' + (s.on_sale ? ' (on sale ' + day(s.on_sale) + ')' : '') : (s.on_sale ? 'on sale ' + day(s.on_sale) : 'listed');
  li.append(el('span', 'dim', ' · ' + when + ' · seen ' + day(s.last_seen)));
  return li;
}

function wish(it) {
  const d = el('div', 'wish'), h = el('h3', null, it.name);
  if (it.now.length) h.append(el('span', 'chip ' + (it.now.some(s => s.status === 'coming') ? 'coming' : 'on'),
    it.now.some(s => s.status === 'coming') ? 'coming soon' : 'on sale now'));
  if (it.max_price) h.append(el('span', 'chip', 'up to ' + money(it.max_price)));
  d.append(h);
  d.append(el('p', 'dim', it.times_seen ? 'Last seen ' + day(it.last_seen.last_seen) + ' at ' + shop(it.last_seen.retailer)
    + (it.last_on_sale ? '; last on sale ' + day(it.last_on_sale.on_sale) + (it.last_on_sale.price != null ? ' at ' + money(it.last_on_sale.price) : '') : '') + '.'
    : 'Not seen yet.'));
  if (it.now.length) { const ul = el('ul', 'seen'); it.now.forEach(s => ul.append(sighting(s))); d.append(ul); }
  if (it.times_seen) {
    const det = el('details'); det.append(el('summary', 'dim', 'Every time it was seen (' + it.times_seen + ')'));
    const ul = el('ul', 'seen'); det.append(ul);
    det.ontoggle = () => { if (det.open && !ul.childElementCount) guard(async () => {
      (await api('GET', '/v1/wishlist/' + encodeURIComponent(it.key) + '/seen')).seen.forEach(s => ul.append(sighting(s))); }, $('cmsg')); };
    d.append(det);
  }
  const row = el('div', 'row'), m = el('span', 'err');
  row.append(button('Check now', () => guard(async () => { show(await api('POST', '/v1/wishlist/check', { item: it.key })); }, m)),
    button('Tick off', () => { if (confirm('Tick ' + it.name + ' off the wishlist in KitchenOwl? What was seen is kept.'))
      guard(async () => { await api('DELETE', '/v1/wishlist/items/' + encodeURIComponent(it.key)); await load(); }, m); }), m);
  d.append(el('p', 'dim', 'Searches for "' + it.query + '"' + (it.must.length ? ', must have: ' + it.must.join(', ') : '')
    + (it.description ? ' · KitchenOwl note: ' + it.description : '')), row);
  return d;
}

function show(data) {
  const list = $('list'); list.replaceChildren();
  if (!data.items.length) list.append(el('p', 'dim', 'Nothing on the wishlist yet.'));
  data.items.forEach(it => list.append(wish(it)));
  $('listline').textContent = 'The wishes are the "' + data.list.name + '" list in KitchenOwl, so anyone can add one in the KitchenOwl app, or here.';
  const l = data.last_check;
  $('last').textContent = l ? ' Last checked ' + day(l.at) + (l.errors ? ' (' + Object.values(l.errors).join('; ') + ')' : '') : ' Not checked yet.';
}
async function load() {
  try { show(await api('GET', '/v1/wishlist')); } finally { await lists(); }
}
async function lists() {
  const st = await api('GET', '/v1/wishlist/settings'), sel = $('listsel'); sel.replaceChildren();
  const def = el('option', null, 'the list called "' + st.default_name + '"'); def.value = ''; sel.append(def);
  st.lists.forEach(x => { const o = el('option', null, x.name + ' (' + x.items + ')'); o.value = x.id; if (x.id === st.list_id) o.selected = true; sel.append(o); });
  if (st.problem) $('lmsg').textContent = st.problem;
}

$('auth').onsubmit = e => { e.preventDefault(); try { sessionStorage.setItem('wa_token', tok.value); } catch (x) {} guard(load, msg); };
$('add').onsubmit = e => { e.preventDefault(); guard(async () => {
  const rules = [];
  if ($('max').value) rules.push('under £' + Number($('max').value));
  if ($('must').value.trim()) rules.push('must: ' + $('must').value.trim());
  const r = await api('POST', '/v1/wishlist/items', { name: $('name').value.trim(), description: rules.join('; ') });
  $('name').value = $('must').value = $('max').value = '';
  show(r);
}, $('amsg')); };
$('check').onclick = () => guard(async () => { $('check').disabled = true;
  try { show(await api('POST', '/v1/wishlist/check')); } finally { $('check').disabled = false; } }, $('cmsg'));
$('listform').onsubmit = e => { e.preventDefault(); guard(async () => {
  await api('PUT', '/v1/wishlist/settings', { list_id: $('listsel').value ? Number($('listsel').value) : null }); await load();
}, $('lmsg')); };
guard(load, msg);
</script></main></body></html>"""

page_router = APIRouter()


@page_router.get("/wishlist", response_class=HTMLResponse, include_in_schema=False)
async def wishlist_page(request: Request) -> str:
    """The page holds no data itself; it asks for the token and calls /v1/wishlist."""
    return themed(PAGE, "wishlist", request)
