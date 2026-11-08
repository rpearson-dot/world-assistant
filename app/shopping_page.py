"""The Shopping page: the family's KitchenOwl list, priced at Sainsbury's and Ocado, and the retailer logins.

The page holds no data itself; it calls /v1/shopping and, to save the KitchenOwl token and the retailer logins,
/v1/keys. Everything from the retailers and KitchenOwl is put on the page as text, never as HTML.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant shopping</title>
<style>
/*BASE_CSS*/
.tot { display: flex; gap: 12px; flex-wrap: wrap; }
.tot .card { flex: 1 1 200px; margin: 0; }
.why { font-size: 12px; padding: 1px 7px; border-radius: 9px; background: var(--chip); white-space: nowrap; }
.why.usual { background: var(--good-bg); color: var(--good); }
.why.missing { background: var(--bad-bg); color: var(--bad); }
.cands { margin: 6px 0 0; padding: 0; list-style: none; }
.cands li { margin: 3px 0; }
td.p { white-space: nowrap; text-align: right; }
.prod { font-size: 13px; }
.slots li { margin: 2px 0; }
.shot img { max-width: 100%; border: 1px solid var(--line); border-radius: var(--radius-sm); display: block; margin: 6px 0; }
.shot { margin: 10px 0 0; }
.warn { font-weight: 600; }
.tot .big { font-size: 24px; font-weight: 650; margin: 2px 0 6px; }
label.inline { display: inline-flex; align-items: center; gap: 6px; font-weight: normal; }
label.inline input { width: auto; margin: 0; }
.scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
#items { min-width: 560px; }
</style></head><body><main>
<!--NAV-->
<h1>Shopping</h1>
<form id="auth" class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button class="primary">Load</button></form>
<p id="msg" class="err"></p>

<div class="card" id="pricecard"><h2>Price this list</h2>
<p class="dim" id="listline">Loading the list...</p>
<div class="row"><button id="price" class="primary">Price this list</button>
<label class="inline"><input type="checkbox" id="fresh"> fresh prices (otherwise searches up to 6 hours old are reused)</label>
<button id="clear" type="button">Clear the list</button></div>
<p id="pmsg" class="err"></p>
<div id="result" hidden>
  <p id="pricedat" class="dim"></p>
  <div class="tot" id="totals"></div>
  <p id="verdict"></p>
  <div class="scroll"><table id="items"><thead><tr><th>Item</th><th>Qty</th></tr></thead><tbody></tbody></table></div>
  <p class="dim">The badge says how each product was picked: <span class="why usual">your usual</span> is one you chose before;
  <span class="why">best match</span> is the retailer's own top result; <span class="why missing">usual not found</span> means
  your usual did not turn up, so the best match stands in. <b>Change</b> to choose: it is remembered for that item.</p>
</div>
</div>

<div class="card"><h2>Ordered? Tick it off</h2>
<p class="dim">Looks at your recent orders and finds what on the list they cover, by the product you chose or the same name,
so you can take those off the list. Sainsbury's shows upcoming orders too; Ocado only shows an order once it has been
delivered. Both need the retailer's login.</p>
<div class="row" id="orderbtns"></div>
<div id="orders"></div>
<p id="omsg" class="err"></p>
</div>

<div class="card"><h2>Delivery slots</h2>
<p class="dim">Read only: book on the retailer's site or app. Sainsbury's takes 10 to 20 seconds (it needs a browser) and a login.</p>
<div class="row" id="slotbtns"></div>
<div id="slots"></div>
</div>

<div class="card"><h2>The list</h2>
<p class="dim" id="koline"></p>
<form id="tokform" class="row"><label>KitchenOwl long-lived token <input id="kotok" type="password" autocomplete="off"
  placeholder="KitchenOwl: account settings, Sessions, Create long-lived token"></label>
<button class="primary">Save token</button></form>
<form id="listform" class="row" hidden>
  <label>List <select id="listsel"></select></label>
  <span id="retailers"></span>
  <button class="primary">Save</button>
</form>
<p id="lmsg" class="err"></p>
</div>

<div class="card"><h2>Retailer logins</h2>
<p class="dim" id="smline"></p>
<div id="logins"></div>
<p id="gmsg" class="err"></p>
</div>

<script>
const $ = id => document.getElementById(id);
const tok = $('t'), msg = $('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
const LABEL = { sainsburys: "Sainsbury's", ocado: 'Ocado' };
const WHY = { usual: ['your usual', 'usual'], best: ['best match', ''], usual_missing: ['usual not found', 'missing'], none: ['none found', 'missing'] };
let status = null, last = null;
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function button(text, onclick, cls) { const b = el('button', cls || null, text); b.type = 'button'; b.onclick = onclick; return b; }
const money = v => v == null ? '' : '£' + Number(v).toFixed(2);
async function api(method, path, body) {
  const r = await fetch(path, { method, headers: { Authorization: 'Bearer ' + tok.value, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined });
  let d = null; try { d = await r.json(); } catch (e) {}
  if (!r.ok) { let m = d && d.detail; if (Array.isArray(m)) m = m.map(x => x.msg).join('; '); throw new Error(m || ('HTTP ' + r.status)); }
  return d;
}
async function guard(fn, out) { out.textContent = ''; try { await fn(); } catch (e) { out.textContent = e.message; } }

function product(p) {
  if (!p) return el('span', 'dim', 'not found');
  const s = el('span', 'prod', p.name + (p.size ? ' (' + p.size + ')' : ''));
  if (p.unit_price && p.unit_price.price != null) s.append(el('span', 'dim', ' ' + money(p.unit_price.price) + '/' + p.unit_price.measure));
  return s;
}

function renderTotals(d) {
  const t = d.totals, box = $('totals'); box.replaceChildren();
  for (const r of d.retailers) {
    const x = t.by_retailer[r], c = el('div', 'card' + (t.cheaper === r ? ' best' : ''));
    c.append(el('h2', null, x.label), el('p', 'big', money(x.total)),
      el('p', 'dim', x.items + ' items priced' + (x.missing ? ', ' + x.missing + ' not found' : '')));
    if (d.retailers.length > 1) c.append(el('p', 'dim', money(x.like_for_like) + ' for the ' + t.compared_items + ' items both have'));
    if (x.items) {
      const out = el('div', 'sent');
      c.append(button('Send to ' + x.label + ' basket', () => sendBasket(r, x, out), 'primary'), out);
    }
    box.append(c);
  }
  $('verdict').textContent = t.cheaper
    ? LABEL[t.cheaper] + ' is cheaper by ' + money(t.saving) + ' on the ' + t.compared_items + ' items both have.'
    : (d.retailers.length > 1 ? (t.compared_items ? 'They come to the same.' : 'No item was found at both, so there is nothing to compare.') : '');
}

// Put this retailer's chosen products in its basket, each up to the list's quantity (topped up, never doubled).
async function sendBasket(r, x, out) {
  if (!confirm('Put the ' + x.items + ' priced items (' + money(x.total) + ') in your ' + x.label + ' basket?\\n\\n'
    + 'Anything already in it is topped up to the quantity on the list, never doubled. Nothing is ordered: you check out on '
    + x.label + "'s site or app.")) return;
  out.replaceChildren(el('p', 'dim', 'Adding to the ' + x.label + ' basket, one item at a time...'));
  try {
    const d = await api('POST', '/v1/shopping/basket/' + r);
    const n = s => d.results.filter(i => i.status === s);
    const parts = [[n('added'), 'added'], [n('raised'), 'topped up'], [n('already'), 'already there'], [n('failed'), 'failed']]
      .filter(([l]) => l.length).map(([l, w]) => l.length + ' ' + w);
    out.replaceChildren(el('p', null, parts.join(', ') + '.'));
    for (const f of n('failed')) out.append(el('p', 'err', (f.item || f.name) + ': ' + f.error));
    if (d.basket) out.append(el('p', 'dim', 'The ' + x.label + ' basket now has ' + d.basket.count + ' items, ' + money(d.basket.total)
      + '. Check out on their site or app.'));
  } catch (e) { out.replaceChildren(el('p', 'err', e.message)); }
}

function renderItems(d) {
  const head = $('items').tHead.rows[0];
  while (head.cells.length > 2) head.deleteCell(2);
  for (const r of d.retailers) head.append(el('th', null, LABEL[r]), el('th', 'p', ''));
  const body = $('items').tBodies[0]; body.replaceChildren();
  for (const it of d.items) {
    const tr = body.insertRow();
    const name = tr.insertCell(); name.append(el('b', null, it.name));
    if (it.description) name.append(el('div', 'dim', it.description));
    tr.insertCell().textContent = it.quantity;
    for (const r of d.retailers) {
      const x = it.retailers[r], cell = tr.insertCell();
      cell.append(product(x.choice), ' ');
      const w = WHY[x.why] || [x.why, '']; cell.append(el('span', 'why ' + w[1], w[0]), ' ');
      if (x.error) cell.append(el('div', 'err', x.error));
      cell.append(button('Change', () => choose(cell, it, r, x)));
      tr.insertCell().className = 'p';
      tr.cells[tr.cells.length - 1].textContent = x.choice ? money(x.line) : '';
    }
  }
}

function choose(cell, it, r, x) {
  if (cell.querySelector('.cands')) { cell.querySelector('.cands').remove(); return; }
  const ul = el('ul', 'cands');
  for (const p of x.candidates) {
    const li = el('li'); li.append(product(p), ' ', el('b', null, money(p.price)), ' ');
    li.append(button('Use this', () => guard(async () => {
      await api('PUT', '/v1/shopping/match', { item_key: it.key, retailer: r, product: { id: p.id, name: p.name, price: p.price, size: p.size, unit_price: p.unit_price } });
      await price(false);
    }, $('pmsg'))));
    ul.append(li);
  }
  if (!x.candidates.length) ul.append(el('li', 'dim', 'Nothing found. Rename the item in KitchenOwl to something the shop would call it.'));
  if (x.usual) { const li = el('li'); li.append(button('Forget my usual: back to the best match', () => guard(async () => {
    await api('DELETE', '/v1/shopping/match', { item_key: it.key, retailer: r }); await price(false);
  }, $('pmsg')))); ul.append(li); }
  cell.append(ul);
}

function show(d) {
  last = d; $('result').hidden = false;
  $('pricedat').textContent = 'Priced ' + new Date(d.priced_at).toLocaleString('en-GB');
  renderTotals(d); renderItems(d);
}

async function price(fresh) {
  $('price').disabled = true; $('pmsg').textContent = '';
  $('pricedat').textContent = 'Searching each item at each shop: a long list takes a minute or two the first time...';
  try { show(await api('POST', '/v1/shopping/price', { refresh: !!fresh })); }
  catch (e) { $('pmsg').textContent = e.message; }
  finally { $('price').disabled = false; }
}

let listCount = 0;
async function loadList() {
  try {
    const l = await api('GET', '/v1/shopping/list');
    listCount = l.count; $('clear').disabled = !l.count;
    $('listline').textContent = l.count + ' item' + (l.count === 1 ? '' : 's') + ' on the list: '
      + l.items.slice(0, 12).map(i => i.name).join(', ') + (l.count > 12 ? ', ...' : '');
    $('price').disabled = !l.count;
  } catch (e) { $('listline').textContent = e.message; $('price').disabled = true; }
}

async function loadLists() {
  const st = status.settings, sel = $('listsel'); sel.replaceChildren();
  try {
    for (const h of await api('GET', '/v1/shopping/lists')) for (const l of h.lists) {
      const o = new Option(h.name + ': ' + l.name + ' (' + l.items + ')', h.id + ':' + l.id);
      if (st.household_id === h.id && st.list_id === l.id) o.selected = true;
      sel.append(o);
    }
    $('listform').hidden = false;
  } catch (e) { $('listform').hidden = true; }
  const box = $('retailers'); box.replaceChildren();
  for (const r of ['sainsburys', 'ocado']) {
    const c = el('input'); c.type = 'checkbox'; c.value = r; c.checked = st.retailers.includes(r);
    const lab = el('label', 'inline'); lab.append(c, LABEL[r]); box.append(lab, ' ');
  }
}

function slotList(d) {
  const ul = el('ul', 'slots'), free = d.slots.filter(s => s.available);
  for (const s of free.slice(0, 12)) ul.append(el('li', null, [s.date, s.start + (s.end ? '-' + s.end : ''), s.price != null ? money(s.price) : ''].filter(Boolean).join(' ')));
  if (!free.length) ul.append(el('li', 'dim', 'No free slots in what they show.'));
  return ul;
}

function renderSlots() {
  const box = $('slotbtns'); box.replaceChildren();
  for (const r of ['sainsburys', 'ocado']) box.append(button(LABEL[r] + ' slots', () => {
    const out = $('slots'); out.replaceChildren(el('p', 'dim', 'Asking ' + LABEL[r] + '...'));
    api('GET', '/v1/shopping/slots?retailer=' + r).then(d => {
      out.replaceChildren(el('h3', null, LABEL[r] + (d.cached ? ' (from ' + new Date(d.fetched_at).toLocaleTimeString('en-GB') + ')' : '')), slotList(d));
    }).catch(e => out.replaceChildren(el('p', 'err', e.message)));
  }));
}

const when = iso => iso ? new Date(iso).toLocaleString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : 'no slot';

function renderOrders() {
  const box = $('orderbtns'); box.replaceChildren();
  for (const r of ['sainsburys', 'ocado']) box.append(button(LABEL[r] + ' orders', () => showOrders(r)));
}

async function showOrders(r) {
  const out = $('orders'); $('omsg').textContent = '';
  out.replaceChildren(el('p', 'dim', 'Reading your ' + LABEL[r] + ' orders...'));
  let d;
  try { d = await api('GET', '/v1/shopping/orders/' + r); } catch (e) { out.replaceChildren(); $('omsg').textContent = e.message; return; }
  out.replaceChildren(el('h3', null, LABEL[r]));
  if (!d.orders.length) { out.append(el('p', 'dim', 'No recent orders' + (r === 'ocado' ? ' delivered yet.' : '.'))); return; }
  for (const o of d.orders.slice(0, 5)) {
    const c = el('div', 'card');
    c.append(el('p', null, when(o.slot_start) + ' \u00b7 ' + (o.status || '').toLowerCase().replace(/_/g, ' ') + ' \u00b7 '
      + money(o.total) + ' \u00b7 ' + o.items.length + (o.items.length === 1 ? ' line' : ' lines')));
    if (!o.on_list.length) { c.append(el('p', 'dim', 'Nothing on the list is in this order.')); out.append(c); continue; }
    const f = el('form');
    for (const it of o.on_list) {
      const cb = el('input'); cb.type = 'checkbox'; cb.checked = true; cb.value = it.key;
      const lab = el('label', 'inline'); lab.append(cb, it.name + (it.how === 'name' ? ' (same name)' : ''));
      f.append(lab, el('br'));
    }
    const msg = el('p');
    f.append(el('button', 'primary', 'Tick these off the list'), msg);
    f.onsubmit = e => { e.preventDefault(); guard(async () => {
      const keys = [...f.querySelectorAll('input:checked')].map(x => x.value);
      if (!keys.length) throw new Error('Nothing is ticked.');
      const res = await api('POST', '/v1/shopping/tick', { item_keys: keys });
      msg.textContent = 'Took ' + res.removed + ' item' + (res.removed === 1 ? '' : 's') + ' off the list.';
      await loadList();
    }, msg); };
    c.append(el('p', null, o.on_list.length + ' on the list:'), f);
    out.append(c);
  }
}

function renderLogins() {
  const box = $('logins'), out = $('gmsg'); box.replaceChildren();
  const sm = status.supermarket;
  $('smline').textContent = sm.ok ? 'The supermarket service is running. Prices need no login; slots do.'
    : (sm.problem || 'The supermarket service is not reachable.');
  for (const r of ['sainsburys', 'ocado']) {
    const c = el('div', 'card'), p = sm.providers && sm.providers[r], s = p && p.session;
    c.append(el('h3', null, LABEL[r]));
    let line = status.credentials[r] ? 'Email and password saved. ' : 'No email and password saved yet. ';
    if (s && s.saved) line += r === 'sainsburys' ? (s.expired ? 'The login has expired.' : 'Logged in until ' + new Date(s.expires_at).toLocaleDateString('en-GB') + '.')
      : 'Logged in (saved ' + new Date(s.saved_at).toLocaleDateString('en-GB') + ').';
    else line += 'Not logged in.';
    c.append(el('p', null, line));
    const f = el('form', 'row'), em = el('input'), pw = el('input');
    em.type = 'email'; em.placeholder = 'email'; em.autocomplete = 'off'; pw.type = 'password'; pw.placeholder = 'password'; pw.autocomplete = 'new-password';
    f.append(em, pw, el('button', 'primary', 'Save'));
    f.onsubmit = e => { e.preventDefault(); guard(async () => {
      if (!em.value || !pw.value) throw new Error('Give both the email and the password.');
      await api('PUT', '/v1/settings/keys/' + r + '_email', { value: em.value }); await api('PUT', '/v1/settings/keys/' + r + '_password', { value: pw.value });
      await refresh();
    }, out); };
    c.append(f);
    const acts = el('div', 'row');
    acts.append(button('Log in', async () => {
      out.textContent = 'Logging in to ' + LABEL[r] + ' (up to a minute)...';
      try {
        const d = await api('POST', '/v1/shopping/login/' + r + '/start');
        if (d.status === 'verify_needed' || d.status === 'code_needed') { out.textContent = ''; verify(c, r, d); }
        else { out.textContent = 'Logged in to ' + LABEL[r] + '.'; await refresh(); }
      } catch (e) { out.textContent = e.message; showShot(c, r); }
    }));
    acts.append(button('Log out', () => guard(async () => { await api('POST', '/v1/shopping/logout/' + r); await refresh(); }, out)));
    acts.append(button('Paste cookies instead', () => cookieBox(c, r)));
    acts.append(button('Last screenshot', () => showShot(c, r, true)));
    c.append(acts);
    if (sm.login && (sm.login.provider || (sm.login.waiting_for_code && 'sainsburys')) === r) {
      verify(c, r, { kind: sm.login.kind || 'sms', message: LABEL[r] + ' is waiting for you to confirm the login (until '
        + new Date(sm.login.until).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) + ').' });
    }
    box.append(c);
  }
}

// A login the retailer wants confirmed: a code (texted or emailed) to type in, or a link in an email, then Continue.
function verify(card, r, d) {
  card.querySelector('.verify')?.remove();
  const v = el('div', 'verify'), note = el('p', 'warn', d.message || 'Confirm the login.'), msg = el('p', 'err');
  v.append(note);
  const done = async () => { $('gmsg').textContent = 'Logged in to ' + LABEL[r] + '.'; await refresh(); };
  if (d.kind === 'email_link') {
    v.append(button("I've clicked the link: Continue", () => guard(async () => {
      await api('POST', '/v1/shopping/login/' + r + '/continue'); await done();
    }, msg), 'primary'));
  } else {
    const f = el('form', 'row'), i = el('input');
    i.maxLength = 10; i.autocomplete = 'one-time-code';
    i.placeholder = d.kind === 'sms' ? 'code from the text' : 'code from the email';
    if (d.kind === 'sms') i.inputMode = 'numeric';
    f.append(i, el('button', 'primary', 'Send code'));
    f.onsubmit = e => { e.preventDefault(); guard(async () => {
      await api('POST', '/v1/shopping/login/' + r + '/code', { code: i.value.trim() }); await done();
    }, msg); };
    v.append(f);
    setTimeout(() => i.focus(), 0);
  }
  v.append(msg);
  card.append(v);
  showShot(card, r);
}

// The newest screenshot of the retailer's sign-in page, under its card (fetched with the token, so not a plain <img src>).
async function showShot(card, r, sayIfNone) {
  card.querySelector('.shot')?.remove();
  const r_ = await fetch('/v1/shopping/login/' + r + '/screenshot', { headers: { Authorization: 'Bearer ' + tok.value } }).catch(() => null);
  if (!r_ || !r_.ok) { if (sayIfNone) $('gmsg').textContent = 'No screenshot kept for ' + LABEL[r] + ' yet.'; return; }
  const name = r_.headers.get('X-Screenshot-Name') || '';
  const m = name.match(/(\\d{4}-\\d{2}-\\d{2})T(\\d{2})-(\\d{2})/);
  const fig = el('figure', 'shot'), img = el('img');
  img.src = URL.createObjectURL(await r_.blob()); img.alt = 'What ' + LABEL[r] + "'s sign-in page showed";
  fig.append(el('figcaption', 'dim', 'What ' + LABEL[r] + ' showed' + (m ? ' at ' + m[2] + ':' + m[3] + ' on ' + m[1].split('-').reverse().join('/') : '') + ' (UTC)'), img,
    button('Hide', () => fig.remove()));
  card.append(fig);
}

function cookieBox(card, r) {
  if (card.querySelector('textarea')) return;
  const f = el('form'), ta = el('textarea'); ta.rows = 5; ta.style.width = '100%';
  ta.placeholder = 'Log in to ' + LABEL[r] + ' in your own browser, export its cookies as JSON (a cookie export extension), paste them here.';
  f.append(ta, el('button', 'primary', 'Use these cookies'));
  f.onsubmit = e => { e.preventDefault(); guard(async () => {
    let c; try { c = JSON.parse(ta.value); } catch (x) { throw new Error('That is not JSON.'); }
    await api('POST', '/v1/shopping/session/' + r, { cookies: c }); await refresh();
  }, $('gmsg')); };
  card.append(f);
}

async function refresh() {
  status = await api('GET', '/v1/shopping/status');
  const ko = status.kitchenowl;
  $('koline').textContent = !ko.installed ? 'KitchenOwl is not installed: add docker-compose.kitchenowl.yml (see docs/shopping.md).'
    : ko.ok ? 'KitchenOwl is connected. The family uses the KitchenOwl app' + (ko.public_url ? ' at ' + ko.public_url : '') + ' (over Tailscale).'
    : ko.problem;
  $('tokform').hidden = ko.ok;
  await loadLists();
  renderLogins(); renderSlots(); renderOrders();
  await loadList();
  try { show(await api('GET', '/v1/shopping/price')); } catch (e) { $('result').hidden = true; }
}

$('auth').onsubmit = e => { e.preventDefault(); try { sessionStorage.setItem('wa_token', tok.value); } catch (x) {} guard(refresh, msg); };
$('price').onclick = () => price($('fresh').checked);
$('clear').onclick = () => {
  if (!confirm('Take all ' + listCount + ' items off the KitchenOwl list?\\n\\nThey stay in KitchenOwl\\'s recent items, so they are quick to add back.')) return;
  guard(async () => {
    const r = await api('POST', '/v1/shopping/clear', { confirm: true });
    $('pmsg').textContent = 'Cleared ' + r.removed + ' item' + (r.removed === 1 ? '' : 's') + ' off the list.';
    await loadList();
  }, $('pmsg'));
};
$('tokform').onsubmit = e => { e.preventDefault(); guard(async () => {
  await api('PUT', '/v1/settings/keys/kitchenowl_token', { value: $('kotok').value }); $('kotok').value = ''; await refresh();
}, $('lmsg')); };
$('listform').onsubmit = e => { e.preventDefault(); guard(async () => {
  const [h, l] = $('listsel').value.split(':').map(Number);
  const retailers = [...$('retailers').querySelectorAll('input:checked')].map(c => c.value);
  await api('PUT', '/v1/shopping/settings', { household_id: h, list_id: l, retailers }); await refresh();
}, $('lmsg')); };
guard(refresh, msg);
</script></main></body></html>"""

page_router = APIRouter()


@page_router.get("/shopping", response_class=HTMLResponse, include_in_schema=False)
async def shopping_page(request: Request) -> str:
    """The page holds no data itself; it asks for the token and calls /v1/shopping."""
    return themed(PAGE, "shopping", request)
