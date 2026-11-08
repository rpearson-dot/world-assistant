"""The Garage: the bike garage as a game's tuning screen. One bike at a time, its parts marked where they are on its
drawing and tinted green to amber to red by their wear; the spares shelf, a bin and a shop counter always on screen.
Tap a part for its details and what could replace it (spares that fit, things to buy); jobs go onto a service list
that is done in one go.

The page holds no data itself; it calls /v1/bikes (the bikes page has the detail and the forms).
"""

from __future__ import annotations

GARAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant garage</title>
<style>
/*BASE_CSS*/
body { padding-bottom: 150px; }
.gtop { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.gtop h1 { margin: 0; }
.bikes { display: flex; gap: 10px; overflow-x: auto; padding: 8px 2px; scroll-snap-type: x mandatory; }
.bt { flex: 0 0 140px; scroll-snap-align: start; border: 2px solid var(--line); border-radius: var(--radius); background: var(--card);
  padding: 6px; cursor: pointer; text-align: center; color: var(--ink); font: inherit; }
.bt svg { width: 100%; height: 60px; } .bt b { display: block; font-size: 14px; }
.bt.on { border-color: var(--accent); box-shadow: 0 0 0 3px var(--chip); }
.bt .due { color: var(--bad); font-size: 12px; }
.stage { position: relative; border-radius: var(--radius); padding: 14px 10px 8px; margin-top: 6px; overflow: hidden;
  background: radial-gradient(ellipse at 50% 92%, var(--chip) 0 28%, transparent 60%), radial-gradient(ellipse at 50% 0%, var(--card), var(--bg) 75%);
  border: var(--frame) solid var(--line); }
.stage h2 { margin: 0; display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
.rig { position: relative; margin: 6px auto 0; max-width: 820px; color: var(--ink); }
.rig .art svg { width: 100%; height: auto; display: block; }
.spot { position: absolute; transform: translate(-50%, -50%); width: 38px; height: 38px; border-radius: 50%; border: 3px solid var(--c, var(--good));
  background: var(--card); color: var(--ink); display: grid; place-items: center; cursor: pointer; padding: 0; box-shadow: var(--shadow); touch-action: none; }
.spot svg { width: 20px; height: 20px; } .spot:hover, .spot.over { transform: translate(-50%, -50%) scale(1.15); }
.spot .n { position: absolute; top: -6px; right: -6px; background: var(--ink); color: var(--card); font-size: 10px; border-radius: 8px; padding: 0 4px; }
.spot.due { animation: pulse 1.6s infinite; }
@media (max-width: 560px) { .spot { width: 28px; height: 28px; border-width: 2px; } .spot svg { width: 15px; height: 15px; }
  .bin, .counter { width: 58px; } .bin .big, .counter .big { font-size: 22px; } .tile { width: 88px; } }
@keyframes pulse { 0%, 100% { box-shadow: 0 0 0 0 var(--bad-bg); } 50% { box-shadow: 0 0 0 9px var(--bad-bg); } }
@media (prefers-reduced-motion: reduce) { .spot.due { animation: none; } .fly { transition: none !important; } }
.odo { font-variant-numeric: tabular-nums; font-family: ui-monospace, monospace; font-size: 13px; background: var(--ink); color: var(--card);
  border-radius: 4px; padding: 2px 8px; letter-spacing: .06em; }
.legend { display: flex; gap: 10px; font-size: 12px; color: var(--dim); align-items: center; }
.legend i { display: inline-block; width: 60px; height: 8px; border-radius: 4px; background: linear-gradient(90deg, var(--good), var(--warn), var(--bad)); }
.tile { position: relative; flex: 0 0 auto; width: 104px; min-height: 92px; border-radius: var(--radius-sm); border: 1px solid var(--line); background: var(--card);
  color: var(--ink); padding: 6px 4px 4px; text-align: center; cursor: pointer; overflow: hidden; touch-action: none; user-select: none; font: inherit; }
.tile::before { content: ""; position: absolute; inset: 0; opacity: .38; pointer-events: none;
  background: linear-gradient(to top, var(--c, var(--good)) 0 calc(var(--w, 0) * 100%), transparent calc(var(--w, 0) * 100%)); }
.tile svg { width: 36px; height: 36px; position: relative; } .tile b { display: block; font-size: 11px; line-height: 1.2; position: relative; }
.tile .mk { display: block; font-size: 10px; color: var(--dim); position: relative; line-height: 1.15; }
.tile .q { position: absolute; top: 3px; right: 5px; font-size: 11px; font-weight: 700; }
.tile .pc { position: relative; font-size: 11px; }
.dock { position: fixed; left: 0; right: 0; bottom: 0; z-index: 20; background: var(--card); border-top: var(--frame) solid var(--line); box-shadow: var(--shadow);
  display: grid; grid-template-columns: 1fr auto auto; gap: 8px; padding: 8px 10px; align-items: center; }
.shelf { display: flex; gap: 6px; overflow-x: auto; min-height: 96px; align-items: stretch; }
.shelf .empty { color: var(--dim); font-size: 13px; align-self: center; }
.bin, .counter { width: 84px; height: 96px; border-radius: var(--radius-sm); border: 2px dashed var(--line); display: grid; place-items: center; text-align: center;
  font-size: 12px; color: var(--dim); background: var(--bg); cursor: pointer; position: relative; font: inherit; }
.bin .big, .counter .big { font-size: 30px; line-height: 1; } .bin.over { border-color: var(--bad); color: var(--bad); }
.counter .n { position: absolute; top: 4px; right: 6px; background: var(--accent); color: var(--card); border-radius: 9px; font-size: 11px; padding: 0 5px; }
.layout { display: grid; grid-template-columns: 1fr; gap: 12px; }
@media (min-width: 980px) { .layout { grid-template-columns: 1fr 300px; } }
.jobs { list-style: none; margin: 0; padding: 0; } .jobs li { display: flex; gap: 8px; align-items: center; padding: 6px 0; border-top: 1px solid var(--line); font-size: 14px; }
.jobs li span:nth-child(2) { flex: 1; } .jobs .x { padding: 0 8px; }
.gmodal { position: fixed; inset: 0; z-index: 40; display: grid; place-items: center; background: var(--overlay); padding: 12px; }
.gmodal[hidden] { display: none; }
.gsheet { background: var(--card); color: var(--ink); border-radius: var(--radius); width: min(560px, 100%); max-height: 88vh; overflow: auto; padding: 14px; box-shadow: var(--shadow); }
.gsheet h3 { margin: 0 0 6px; display: flex; justify-content: space-between; gap: 8px; }
.hero { display: grid; grid-template-columns: 110px 1fr; gap: 12px; align-items: center; }
.hero .tile { width: 110px; min-height: 110px; cursor: default; } .hero .tile svg { width: 56px; height: 56px; }
.wbar { height: 10px; border-radius: 5px; background: var(--chip); overflow: hidden; margin: 4px 0; }
.wbar i { display: block; height: 100%; background: var(--c, var(--good)); }
.opts { list-style: none; padding: 0; margin: 4px 0 10px; } .opts li { display: flex; gap: 8px; align-items: center; padding: 6px 0; border-top: 1px solid var(--line); }
.opts li > div { flex: 1; } .opts .dim { font-size: 12px; }
.fly { position: fixed; z-index: 60; pointer-events: none; transition: transform .55s cubic-bezier(.5, -0.3, .7, 1), opacity .55s; }
.ghost { position: fixed; z-index: 60; pointer-events: none; opacity: .85; transform: translate(-50%, -50%) rotate(-6deg); }
.gtoast { position: fixed; left: 50%; bottom: 120px; transform: translateX(-50%); z-index: 50; background: var(--ink); color: var(--card); padding: 8px 14px;
  border-radius: var(--pill); font-size: 14px; max-width: 90vw; }
.gtoast[hidden] { display: none; }
</style></head><body><main>
<!--NAV-->
<div class="gtop"><h1>Garage</h1><a class="small" href="/bikes">Bikes page (details and forms)</a>
  <label class="small">Rider <select id="rider"></select></label></div>
<form id="auth" class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button class="primary">Load</button></form>
<p id="msg" class="err"></p>

<div class="bikes" id="bikes"></div>
<div class="layout">
  <section class="stage" id="stage" hidden>
    <h2><span id="bname"></span><span class="odo" id="bodo"></span></h2>
    <div class="legend"><span>New</span><i></i><span>Worn out</span><span id="bdue"></span></div>
    <div class="rig" id="rig"><div class="art" id="art"></div></div>
    <p class="dim small">Tap a marker for its parts. Drag a spare from the shelf onto a marker to fit it, or a part to the bin.</p>
  </section>
  <section class="card" id="jobcard">
    <h2>Service list</h2>
    <p class="dim small" id="jobhint">Jobs you add (replace, service, bin, buy) wait here, then go in one go.</p>
    <ul class="jobs" id="jobs"></ul>
    <div class="row"><button class="primary" id="submit" type="button" disabled>Do them all</button>
    <button type="button" class="small" id="clear">Clear</button></div>
    <p id="jmsg" class="small"></p>
  </section>
</div>

<div class="dock" id="dock" hidden>
  <div class="shelf ws-drop" id="shelf" data-drop="shelf"></div>
  <button class="bin" id="bin" type="button" data-drop="bin" title="Drag a worn part here"><div><div class="big">🗑</div>Bin</div></button>
  <button class="counter" id="counter" type="button"><div><div class="big">🛒</div>Shop</div><span class="n" id="basketn" hidden></span></button>
</div>

<div class="gmodal" id="gmodal" hidden><div class="gsheet" id="gsheet"></div></div>
<div class="gtoast" id="gtoast" hidden></div>
<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function button(text, onclick, cls) { const b = el('button', cls || 'small', text); b.type = 'button'; b.onclick = onclick; return b; }
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
async function api(method, path, body) {
  const r = await fetch(path, { method, headers: { Authorization: 'Bearer ' + tok.value, 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  if (r.status === 204) return null;
  let d = null; try { d = await r.json(); } catch (e) {}
  if (!r.ok) { let m = d && d.detail; if (Array.isArray(m)) m = m.map(x => x.msg).join('; '); throw new Error(m || ('HTTP ' + r.status)); }
  return d;
}
async function guard(fn, out) { (out || msg).textContent = ''; try { await fn(); } catch (e) { (out || msg).textContent = e.message; } }
const svgEl = (html, cls) => { const s = el('span', cls); s.innerHTML = html; return s; };
const fmt = n => Math.round(n).toLocaleString('en-GB');
const rider = () => $('rider').value, R = () => '?rider=' + encodeURIComponent(rider());
let cat = null, g = null, cur = null;
let JOBS = []; try { JOBS = JSON.parse(localStorage.getItem('wa_garage_jobs') || '[]') || []; } catch (e) { JOBS = []; }
const saveJobs = () => { try { localStorage.setItem('wa_garage_jobs', JSON.stringify(JOBS)); } catch (e) {} };

// the wear colour: green when new, amber halfway, red when due
function wearCol(sh) {
  if (sh == null) return 'var(--dim)';
  const x = Math.max(0, Math.min(1, sh));
  return x < 0.5 ? 'color-mix(in srgb, var(--warn) ' + Math.round(x * 200) + '%, var(--good))' : 'color-mix(in srgb, var(--bad) ' + Math.round((x - 0.5) * 200) + '%, var(--warn))';
}
const share = p => (p.wear && p.wear.state !== 'untracked') ? p.wear.share : null;
const icon = kind => cat.icons[(cat.kinds[kind] || {}).icon] || cat.icons.part;
function tile(p, opts) {  // a part as a tile, its wear as a fill rising from green to red
  const t = el('button', 'tile'); t.type = 'button'; const sh = share(p);
  t.style.setProperty('--w', sh == null ? 0 : Math.min(1, sh)); t.style.setProperty('--c', wearCol(sh));
  t.append(svgEl(icon(p.kind)), el('b', null, p.label || p.name));
  const mk = [p.brand, p.model].filter(Boolean).join(' '); if (mk && !p.label) t.append(el('span', 'mk', mk));
  if (sh != null) t.append(el('span', 'pc', Math.round(sh * 100) + '%'));
  if ((p.quantity || 1) > 1) t.append(el('span', 'q', '×' + p.quantity));
  t.dataset.part = p.id; t.dataset.kind = p.kind;
  if (!(opts && opts.still)) { t.onclick = () => (opts && opts.click ? opts.click() : showPart(p)); drag(t, p); }
  return t;
}
function toast(text) { const t = $('gtoast'); t.textContent = text; t.hidden = false; clearTimeout(toast.h); toast.h = setTimeout(() => { t.hidden = true; }, 3200); }
function fly(fromEl, toEl, after) {  // a part thrown across the screen: into the bin, onto the counter, onto the bike
  if (!fromEl || !toEl) { if (after) after(); return; }
  const a = fromEl.getBoundingClientRect(), b = toEl.getBoundingClientRect(), c = fromEl.cloneNode(true);
  c.className += ' fly'; Object.assign(c.style, { left: a.left + 'px', top: a.top + 'px', width: a.width + 'px', height: a.height + 'px', margin: 0 });
  document.body.append(c);
  requestAnimationFrame(() => { c.style.transform = 'translate(' + (b.left + b.width / 2 - a.left - a.width / 2) + 'px,' + (b.top + b.height / 2 - a.top - a.height / 2) + 'px) scale(.35) rotate(25deg)'; c.style.opacity = '.2'; });
  setTimeout(() => { c.remove(); if (after) after(); }, 600);
}

// --- bikes and the stage
function allParts(b) { return b.parts.concat(...b.sets.map(s => s.parts.map(p => Object.assign({}, p, { wheel: s.label })))); }
function drawBikes() {
  const box = $('bikes'); box.replaceChildren();
  for (const b of g.bikes) {
    const d = allParts(b).filter(p => p.wear && p.wear.state === 'due').length;
    const t = el('button', 'bt' + (cur && cur.id === b.id ? ' on' : '')); t.type = 'button';
    t.append(svgEl(b.art), el('b', null, b.label), el('span', 'dim small', fmt(b.km || 0) + ' km'));
    if (d) t.append(el('div', 'due', d + ' due'));
    t.onclick = () => { cur = b; try { localStorage.setItem('wa_garage_bike', b.id); } catch (e) {} draw(); };
    box.append(t);
  }
}
function drawStage() {
  const b = cur; $('stage').hidden = !b; if (!b) return;
  $('bname').textContent = b.label; $('bodo').textContent = fmt(b.km || 0) + ' km';
  const parts = allParts(b), due = parts.filter(p => p.wear && p.wear.state === 'due');
  $('bdue').textContent = due.length ? due.length + ' due' : 'nothing due';
  $('art').innerHTML = b.art;
  $('rig').querySelectorAll('.spot').forEach(s => s.remove());
  const by = {}; for (const p of parts) (by[p.spot || 'frame'] = by[p.spot || 'frame'] || []).push(p);
  for (const [spot, ps] of Object.entries(by)) {
    const at = b.spots[spot] || b.spots.frame; if (!at) continue;
    const worst = ps.reduce((m, p) => Math.max(m, share(p) ?? -1), -1);
    const top = ps.slice().sort((a, c) => (share(c) ?? -1) - (share(a) ?? -1))[0];
    const s = el('button', 'spot' + (ps.some(p => p.wear && p.wear.state === 'due') ? ' due' : '')); s.type = 'button';
    s.style.left = (at[0] * 100) + '%'; s.style.top = (at[1] * 100) + '%'; s.style.setProperty('--c', wearCol(worst < 0 ? null : worst));
    s.title = ps.map(p => p.name).join(', '); s.append(svgEl(icon(top.kind)));
    if (ps.length > 1) s.append(el('span', 'n', ps.length));
    s.dataset.drop = 'spot'; s.dataset.spot = spot;
    s.onclick = () => (ps.length === 1 ? showPart(ps[0]) : showSpot(spot, ps));
    if (ps.length === 1) drag(s, ps[0]);  // one part here: it can be dragged to the bin
    $('rig').append(s);
  }
}
function drawShelf() {
  const sh = $('shelf'); sh.replaceChildren();
  if (!g.shelf.length) sh.append(el('span', 'empty', 'The spares shelf is empty: buy some at the shop counter.'));
  for (const p of g.shelf) sh.append(tile(p));
  const n = JOBS.filter(j => j.op === 'buy').length; $('basketn').hidden = !n; $('basketn').textContent = n;
}
function drawJobs() {
  const ul = $('jobs'); ul.replaceChildren();
  for (const [i, j] of JOBS.entries()) {
    const li = el('li'); li.append(el('span', null, { replace: '🔧', service: '🧽', bin: '🗑', buy: '🛒' }[j.op]), el('span', null, j.say));
    li.append(button('✕', () => { JOBS.splice(i, 1); saveJobs(); draw(); }, 'small x')); ul.append(li);
  }
  $('submit').disabled = !JOBS.length; $('jobhint').hidden = !!JOBS.length;
}
function draw() { drawBikes(); drawStage(); drawShelf(); drawJobs(); }
function addJob(j, fromEl, toEl) {
  if (JOBS.some(x => x.op === j.op && x.part_id && x.part_id === j.part_id && x.op !== 'buy')) { toast('Already on the service list'); return; }
  const go = () => { JOBS.push(j); saveJobs(); draw(); toast('Added: ' + j.say); };
  fly(fromEl, toEl || $('jobcard'), go);
}

// --- the part card
function close() { $('gmodal').hidden = true; }
$('gmodal').onclick = e => { if (e.target === $('gmodal')) close(); };
document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
function sheet(title) { const s = $('gsheet'); s.replaceChildren(); const h = el('h3', null, title); h.append(button('Close', close)); s.append(h); $('gmodal').hidden = false; return s; }
function showSpot(spot, ps) {
  const s = sheet(cur.label + ': ' + spot.replace(/_/g, ' '));
  const row = el('div', 'shelf'); for (const p of ps) row.append(tile(p, { click: () => showPart(p) })); s.append(row);
}
const ACTION = { lube: ['serviced', 'Lube it'], tyre_check: ['checked', 'Pumped'], sealant: ['topped_up', 'Top up'], sensor: ['serviced', 'New battery'] };
function serviceOf(p) {
  if (ACTION[p.kind]) return ACTION[p.kind];
  const a = (cat.kinds[p.kind] || {}).action || 'replaced';
  return a === 'replaced' ? null : [a, { serviced: 'Service it', checked: 'Checked', charged: 'Charged', topped_up: 'Top up', bled: 'Bled' }[a] || a];
}
function wearLine(p) {
  const w = p.wear || {}, m = w.measures || {}, out = [];
  if (m.km) out.push(fmt(m.km.used) + ' of ' + fmt(m.km.life) + ' km');
  if (m.hours) out.push(Math.round(m.hours.used) + ' of ' + Math.round(m.hours.life) + ' h');
  if (m.days) out.push(Math.round(m.days.used) + ' of ' + Math.round(m.days.life) + ' days');
  return out.join(' · ');
}
async function showPart(p) {
  const onBike = cur && allParts(cur).some(x => x.id === p.id);
  const s = sheet(p.name), hero = el('div', 'hero'), info = el('div'), sh = share(p);
  hero.append(tile(p, { still: true }), info);
  if (p.describe) info.append(el('div', 'small', p.describe));
  if (p.wheel) info.append(el('div', 'dim small', 'On the ' + p.wheel));
  if (sh != null) { const bar = el('div', 'wbar'), i = el('i'); i.style.width = Math.min(100, sh * 100) + '%'; bar.style.setProperty('--c', wearCol(sh)); bar.append(i);
    info.append(el('div', null, Math.round(sh * 100) + '% worn' + (p.wear.state === 'due' ? ': due' : p.wear.state === 'coming' ? ': coming up' : '')), bar, el('div', 'dim small', wearLine(p))); }
  else info.append(el('div', 'dim small', 'Not counted: kept for its make and model'));
  if (p.stretch) info.append(el('div', 'small', 'Chain checker: ' + p.stretch.latest + '% (worn out at ' + p.stretch.limit + '%)'));
  if (p.reported) info.append(el('div', 'small', 'Garmin: battery ' + p.reported.status + (p.reported.volts ? ' (' + p.reported.volts + ' V)' : '')));
  if (p.measure) info.append(el('div', 'dim small', 'Check: ' + p.measure));
  const last = (p.history || [])[0]; if (last) info.append(el('div', 'dim small', 'Last: ' + last.action.replace('_', ' ') + ' ' + new Date(last.at).toLocaleDateString('en-GB') + (last.note ? ' (' + last.note + ')' : '')));
  s.append(hero);
  const acts = el('div', 'row'), more = el('div'); s.append(acts, more);
  const me = s.querySelector('.tile');
  if (onBike) {
    acts.append(button('Replace', () => replaceOptions(p, more), 'primary'));
    const sv = serviceOf(p); if (sv) acts.append(button(sv[1], () => { addJob({ op: 'service', part_id: p.id, action: sv[0], say: sv[1] + ': ' + p.name + ' on ' + cur.label }, me); close(); }));
    acts.append(button('Bin it (no replacement)', () => { addJob({ op: 'bin', part_id: p.id, say: 'Bin ' + p.name + ' from ' + cur.label }, me, $('bin')); close(); }));
  } else {  // a spare: onto the bike in place of one like it, or binned
    const same = cur ? allParts(cur).filter(x => x.kind === p.kind) : [];
    for (const x of same) acts.append(button('Fit in place of ' + x.name + (x.wheel ? ' (' + x.wheel + ')' : ''), () => { addJob({ op: 'replace', part_id: x.id, spare_id: p.id, say: 'Fit ' + (p.label || p.name) + ' to ' + cur.label + ' for ' + x.name }, me); close(); }, 'primary'));
    if (!same.length) acts.append(el('span', 'dim small', cur ? cur.label + ' has no ' + p.name.toLowerCase() + ' to replace.' : ''));
    acts.append(button('Bin it', () => { addJob({ op: 'bin', part_id: p.id, say: 'Bin the spare ' + (p.label || p.name) }, me, $('bin')); close(); }));
    acts.append(button('Buy more', () => { addJob({ op: 'buy', text: p.label || [p.brand, p.model].filter(Boolean).join(' ') || p.name, say: 'Buy ' + (p.label || p.name) }, me, $('counter')); close(); }));
  }
}
async function replaceOptions(p, box) {  // the dynamic list: what fits, from the shelf and from the shop
  box.replaceChildren(el('p', 'dim small', 'Finding what fits…'));
  let o; try { o = await api('GET', '/v1/bikes/parts/' + p.id + '/options' + R()); } catch (e) { box.replaceChildren(el('p', 'err', e.message)); return; }
  box.replaceChildren();
  const me = $('gsheet').querySelector('.tile');
  box.append(el('h4', null, 'From your shelf'));
  const ul = el('ul', 'opts');
  if (!o.spares.length) ul.append(el('li', 'dim small', 'No spare ' + p.name.toLowerCase() + ' on the shelf.'));
  for (const sp of o.spares) {
    const li = el('li'), d = el('div'); d.append(el('b', null, sp.name + (sp.quantity > 1 ? ' ×' + sp.quantity : '')));
    if (sp.describe) d.append(el('div', 'dim', sp.describe));
    for (const x of sp.problems) d.append(el('div', 'err small', x)); for (const x of sp.warnings) d.append(el('div', 'small', '⚠ ' + x));
    li.append(d); if (!sp.problems.length) li.append(button('Fit this', () => { addJob({ op: 'replace', part_id: p.id, spare_id: sp.id, say: 'Replace ' + p.name + ' on ' + cur.label + ' with the spare ' + sp.name }, me); close(); }, 'primary small'));
    ul.append(li);
  }
  box.append(ul, el('h4', null, 'From the shop'));
  const ul2 = el('ul', 'opts');
  for (const b of o.buy) {
    const li = el('li'), d = el('div'); d.append(el('b', null, b.text), el('div', 'dim', b.why)); li.append(d);
    li.append(button('Buy', () => { addJob({ op: 'buy', text: b.text, note: 'for ' + cur.label, say: 'Buy ' + b.text }, me, $('counter')); }, 'small'));
    li.append(button('Fit new', () => { addJob({ op: 'replace', part_id: p.id, brand: b.brand, model: b.model, details: b.details, say: 'Replace ' + p.name + ' on ' + cur.label + ' with a new ' + b.text }, me); close(); }, 'primary small'));
    ul2.append(li);
  }
  box.append(ul2);
}

// --- the shop counter
$('counter').onclick = () => guard(async () => {
  const s = sheet('Shop counter');
  const basket = JOBS.filter(j => j.op === 'buy');
  s.append(el('p', 'dim small', 'What you buy goes onto the Outdoors shopping list when the service list is done. Nothing is ordered or paid for.'));
  s.append(el('h4', null, 'In the basket'));
  const ul = el('ul', 'opts'); if (!basket.length) ul.append(el('li', 'dim small', 'Nothing yet.'));
  for (const j of basket) { const li = el('li'); li.append(el('div', null, j.text)); li.append(button('✕', () => { JOBS.splice(JOBS.indexOf(j), 1); saveJobs(); draw(); $('counter').onclick(); })); ul.append(li); }
  s.append(ul, el('h4', null, 'You might need'));
  const sug = el('ul', 'opts'); s.append(sug);
  for (const x of g.spares_low || []) { const li = el('li'), d = el('div'); d.append(el('b', null, x.buy), el('div', 'dim', 'spares: ' + x.have + ' of ' + x.want)); li.append(d,
    button('Buy', () => { addJob({ op: 'buy', text: x.buy, note: 'spare', say: 'Buy ' + x.buy }); $('counter').onclick(); })); sug.append(li); }
  const due = g.due.filter(d => (d.state === 'due' || d.share >= 0.75) && !d.spare && (cat.kinds[d.kind] || {}).bought).slice(0, 8);  // with a spare on the shelf: fit that
  const found = await Promise.all(due.map(d => api('GET', '/v1/bikes/parts/' + d.part_id + '/options' + R()).catch(() => null)));
  for (const [i, o] of found.entries()) { if (!o || !o.buy.length) continue; const b = o.buy[0], li = el('li'), d = el('div');
    d.append(el('b', null, b.text), el('div', 'dim', due[i].name + (o.bike ? ' on ' + o.bike.label : '') + ': ' + Math.round(due[i].share * 100) + '% worn')); li.append(d,
    button('Buy', () => { addJob({ op: 'buy', text: b.text, note: o.bike ? 'for ' + o.bike.label : null, say: 'Buy ' + b.text }); $('counter').onclick(); })); sug.append(li); }
  if (!sug.children.length) sug.append(el('li', 'dim small', 'Nothing due or running low.'));
});

// --- drag: a spare onto a marker, anything into the bin
function drag(t, p) {
  t.addEventListener('pointerdown', e => {
    if (e.button) return; const x0 = e.clientX, y0 = e.clientY; let ghost = null, over = null;
    const move = ev => {
      if (!ghost && Math.hypot(ev.clientX - x0, ev.clientY - y0) < 8) return;
      if (!ghost) { ghost = t.cloneNode(true); ghost.className += ' ghost'; document.body.append(ghost); }
      ghost.style.left = ev.clientX + 'px'; ghost.style.top = ev.clientY + 'px';
      const under = document.elementFromPoint(ev.clientX, ev.clientY), target = under && under.closest('[data-drop]');
      if (over && over !== target) over.classList.remove('over'); over = target; if (over) over.classList.add('over');
    };
    const up = () => {
      document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up);
      if (!ghost) return; ghost.remove(); if (over) over.classList.remove('over');
      t.dataset.dragged = '1'; setTimeout(() => { delete t.dataset.dragged; }, 50);
      if (!over) return;
      const onBike = cur && allParts(cur).some(x => x.id === p.id);
      if (over.dataset.drop === 'bin') addJob({ op: 'bin', part_id: p.id, say: (onBike ? 'Bin ' + p.name + ' from ' + cur.label : 'Bin the spare ' + (p.label || p.name)) }, t, $('bin'));
      else if (over.dataset.drop === 'spot' && !onBike) {
        const there = allParts(cur).filter(x => x.kind === p.kind && (x.spot === over.dataset.spot));
        if (!there.length) { toast('That marker has no ' + p.name.toLowerCase() + ' to replace'); return; }
        const x = there[0]; addJob({ op: 'replace', part_id: x.id, spare_id: p.id, say: 'Fit ' + (p.label || p.name) + ' to ' + cur.label + ' for ' + x.name }, t, over);
      }
    };
    document.addEventListener('pointermove', move); document.addEventListener('pointerup', up);
  });
  t.addEventListener('click', e => { if (t.dataset.dragged) { e.stopImmediatePropagation(); e.preventDefault(); } }, true);
}

// --- doing the list
$('clear').onclick = () => { JOBS = []; saveJobs(); draw(); };
$('submit').onclick = () => guard(async () => {
  $('submit').disabled = true; $('jmsg').textContent = 'Working…';
  const r = await api('POST', '/v1/bikes/jobs', { rider: rider(), jobs: JOBS.map(({ say, ...j }) => j) });
  const bad = r.results.map((x, i) => [x, JOBS[i]]).filter(([x]) => !x.ok);
  $('jmsg').textContent = r.done + ' done' + (bad.length ? '; not done: ' + bad.map(([x, j]) => j.say + ' (' + x.said + ')').join('; ') : '.');
  JOBS = bad.map(([, j]) => j); saveJobs(); await load(); toast(r.done + ' jobs done');
}, $('jmsg'));

async function load() {
  g = await api('GET', '/v1/bikes' + R());
  let keep = null; try { keep = Number(localStorage.getItem('wa_garage_bike')); } catch (e) {}
  cur = g.bikes.find(b => b.id === (cur ? cur.id : keep)) || g.bikes[0] || null;
  $('dock').hidden = false; draw();
}
async function start() {
  cat = cat || await api('GET', '/v1/bikes/catalogue');
  const rs = (await api('GET', '/v1/bikes/riders')).riders, sel = $('rider');
  let keep = null; try { keep = localStorage.getItem('wa_bike_rider'); } catch (e) {}
  sel.replaceChildren(...rs.map(r => new Option(r.name, r.id))); if (keep && rs.some(r => r.id === keep)) sel.value = keep;
  $('auth').hidden = true; if (rider()) await load(); else msg.textContent = 'No riders yet: add one on the Bikes page.';
}
$('rider').onchange = () => { try { localStorage.setItem('wa_bike_rider', rider()); } catch (e) {} cur = null; guard(load); };
$('auth').onsubmit = e => { e.preventDefault(); try { sessionStorage.setItem('wa_token', tok.value); } catch (x) {} guard(start); };
if (tok.value) guard(start);
</script></main></body></html>"""
