"""The Graph page: the knowledge graph as nodes and edges you can search, expand and query.

Drawn on a canvas with a small force layout written here (no library, nothing loaded from outside). Tap a node
for its details, double-tap (or Expand) to bring in what it connects to, drag to move, pinch or scroll to zoom.
"""

from __future__ import annotations

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Graph</title>
<style>
/*BASE_CSS*/
  .tools { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }
  .tools input[type=search] { flex: 1 1 180px; min-width: 0; } .tools select { flex: 1 1 200px; min-width: 0; }
  .stage { display: grid; grid-template-columns: 250px minmax(0, 1fr) 280px; gap: 12px; align-items: start; }
  .stage.nopane { grid-template-columns: minmax(0, 1fr) 280px; } .stage.nopane #pane { display: none; }
  main { max-width: min(1800px, 100%); }  /* the graph uses the window's width, not the reading width */
  #pane #vLabels, #pane #vTypes { display: grid; grid-template-columns: 1fr 1fr; gap: 2px 8px; }
  #pane { padding: 12px 14px; max-height: min(68vh, 680px); overflow-y: auto; position: relative; }
  #pane h2 { display: flex; align-items: center; margin: 0 0 4px; }
  #pane .view .grp:not(#vLabels):not(#vTypes) { flex-direction: column; align-items: stretch; gap: 4px; }
  #pane .view select, #pane .view input[type=range] { width: 100%; }
  .canvaswrap { position: relative; background: var(--card); border: 1px solid var(--line); border-radius: 14px; overflow: hidden;
    height: min(68vh, 680px); touch-action: none; }
  canvas { display: block; width: 100%; height: 100%; cursor: grab; }
  .hint { position: absolute; left: 10px; bottom: 8px; font-size: 12px; color: var(--dim); pointer-events: none; }
  .legend { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0 0; }
  .legend span { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; color: var(--dim); }
  .legend i { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
  #detail table td { font-size: 13px; vertical-align: top; word-break: break-word; } #detail table td:first-child { color: var(--dim); white-space: nowrap; }
  .view { display: grid; gap: 10px; margin-top: 8px; }
  .view .grp { display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: center; }
  .view label { display: inline-flex; align-items: center; gap: 5px; font-size: 13px; }
  .view input[type=color] { width: 22px; height: 22px; padding: 0; border: 0; background: none; }
  .view .k { font-size: 12px; color: var(--dim); min-width: 78px; }
  .found { display: grid; gap: 4px; margin-top: 6px; } .found button { text-align: left; }
  @media (max-width: 1100px) { .stage, .stage.nopane { grid-template-columns: 230px minmax(0, 1fr); }
    .stage.nopane { grid-template-columns: minmax(0, 1fr); } #detail { grid-column: 1 / -1; } }
  @media (max-width: 760px) { .stage, .stage.nopane { grid-template-columns: 1fr; } .canvaswrap { height: 58vh; }
    #pane { max-height: none; order: 3; } }
</style></head><body><main>
<!--NAV-->
<h1>Graph</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary">Load</button></div>
<p id="msg" class="err"></p>
<div id="off" class="card" hidden><h2>The knowledge graph is off</h2>
  <p>The graph view reads the optional Neo4j knowledge graph, which World Assistant fills from its own data.</p>
  <ol><li>Add <code>docker-compose.graph.yml</code> to <code>COMPOSE_FILE</code> in <code>.env</code> (or run <code>scripts/setup.sh</code>).</li>
  <li>Set <code>WA_GRAPH_ENABLED=true</code>; <code>scripts/init-secrets.sh</code> makes its password.</li>
  <li><code>docker compose up -d</code>. The graph is built twice an hour, or press Rebuild below.</li></ol>
  <p class="dim" id="offwhy"></p><button id="rebuild" type="button">Rebuild now</button></div>
<div id="ui">
<div class="tools">
  <input id="q" type="search" placeholder="Find a place, trip, road, station..." autocomplete="off">
  <select id="preset"><option value="">Ready-made questions...</option></select>
  <button id="overview" type="button">Overview</button><button id="clear" type="button">Clear</button>
  <button id="paneOpen" type="button">Customise</button>
</div>
<div id="found" class="found"></div>
<div class="stage" id="stage">
  <aside class="card" id="pane"><h2>Customise the view <button id="paneClose" class="link push" type="button" title="Hide this panel">Hide</button></h2>
  <div class="view">
    <div><div class="k">Show these things</div><div class="grp" id="vLabels"></div></div>
    <div><div class="k">Show these links</div><div class="grp" id="vTypes"></div></div>
    <div class="grp"><span class="k">Size nodes by</span>
      <select id="vSize"><option value="fixed">All the same</option><option value="degree">How connected they are</option></select>
      <span class="k">Names</span>
      <select id="vNames"><option value="zoom">When zoomed in</option><option value="always">Always</option><option value="never">Never</option></select>
      <label><input type="checkbox" id="vEdgeNames" checked> Link names</label></div>
    <div class="grp"><span class="k">Node size</span><input type="range" id="vScale" min="0.5" max="2" step="0.1" value="1">
      <span class="k">Spread</span><input type="range" id="vSpread" min="0.5" max="2.5" step="0.1" value="1"></div>
    <div class="grp"><button id="vReset" type="button">Reset the view</button><span class="dim">Saved in this browser.</span></div>
  </div></aside>
  <div><div class="canvaswrap"><canvas id="cv"></canvas><div class="hint">Tap: details. Double-tap: expand. Drag, pinch or scroll to move and zoom.</div></div>
    <div class="legend" id="legend"></div></div>
  <div class="card" id="detail"><h2>Details</h2><p class="dim">Tap a node to see what it is.</p></div>
</div>
<details class="card" style="margin-top:12px"><summary>Ask with Cypher</summary>
  <p class="dim">Read-only: anything that would change the graph is refused. Return nodes, relationships or paths to see them drawn,
  or values to see a table. Labels: <span id="labels"></span>.</p>
  <textarea id="cy" spellcheck="false">MATCH path = (p:Place)<-[:FROM|TO]-(t:Trip)-[:USES]->(c:Corridor)
RETURN path LIMIT 50</textarea>
  <div class="row" style="margin-top:8px"><button id="run" class="primary" type="button">Run</button><span id="cymsg" class="dim"></span></div>
  <div class="scroll"><table id="rows"></table></div></details>
</div>
<script>
const $ = id => document.getElementById(id), tok = $('t');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
const hdr = j => Object.assign({ Authorization: 'Bearer ' + tok.value }, j ? { 'Content-Type': 'application/json' } : {});
async function api(path, opts) {
  const r = await fetch(path, Object.assign({ headers: hdr(opts && opts.body) }, opts || {}));
  let d = null; try { d = await r.json(); } catch (e) {}
  if (!r.ok) { const err = new Error((d && d.detail) || r.status); err.status = r.status; throw err; }
  return d;
}
const COLORS = { Place: '#1a7f4b', Trip: '#1f6feb', Corridor: '#7c3aed', RoadEvent: '#d92d20', Station: '#dc6803', Location: '#0e9384',
  Vehicle: '#667085', BusTracker: '#4f46e5', BusStop: '#ca8a04', FloodWarning: '#0891b2',
  Area: '#b54708', Disruption: '#c11574', LocalEvent: '#2e90fa' , RailStation: '#0019a8', Entrance: '#6b7280', Line: '#e32017', StationClosure: '#b42318' , PowerCut: '#f04438', StreetWork: '#f79009', Substation: '#6941c6' };
const DEFAULT_VIEW = { hiddenLabels: [], hiddenTypes: [], colors: {}, size: 'fixed', names: 'zoom', edgeNames: true, scale: 1, spread: 1 };
let V = Object.assign({}, DEFAULT_VIEW);
try { Object.assign(V, JSON.parse(localStorage.getItem('wa_graph_view') || '{}')); } catch (e) {}
function saveView() { try { localStorage.setItem('wa_graph_view', JSON.stringify(V)); } catch (e) {} }
const color = l => V.colors[l] || COLORS[l] || '#98a2b3';
const shown = n => !V.hiddenLabels.includes(n.label);
const linkShown = e => !V.hiddenTypes.includes(e.type);

// --- the graph and its layout --------------------------------------------------------------------------
const cv = $('cv'), ctx = cv.getContext('2d');
let nodes = new Map(), edges = new Map(), sel = null, alpha = 0;
let view = { x: 0, y: 0, k: 1 }, dpr = window.devicePixelRatio || 1;
function size() { const r = cv.getBoundingClientRect(); cv.width = r.width * dpr; cv.height = r.height * dpr; draw(); }
window.addEventListener('resize', size);
function showPane(on) {  // the Customise panel beside the graph; hidden gives the graph the room
  $('stage').classList.toggle('nopane', !on); $('paneOpen').style.display = on ? 'none' : '';
  try { localStorage.setItem('wa_graph_pane', on ? '1' : '0'); } catch (e) {}
  requestAnimationFrame(size);
}
$('paneClose').onclick = () => showPane(false); $('paneOpen').onclick = () => showPane(true);
{ let on = true; try { on = localStorage.getItem('wa_graph_pane') !== '0'; } catch (e) {} showPane(on); }
function merge(g, around) {
  const c = around || { x: 0, y: 0 };
  for (const n of g.nodes) {
    if (!nodes.has(n.key)) { const a = Math.random() * 6.283, d = 40 + Math.random() * 60;
      nodes.set(n.key, Object.assign({ x: c.x + Math.cos(a) * d, y: c.y + Math.sin(a) * d, vx: 0, vy: 0 }, n)); }
  }
  for (const e of g.edges) if (nodes.has(e.from) && nodes.has(e.to)) edges.set(e.key, e);
  degrees(); alpha = 1; legend(); loop();
}
function degrees() {
  for (const n of nodes.values()) n.deg = 0;
  for (const e of edges.values()) { const a = nodes.get(e.from), b = nodes.get(e.to); if (a && b) { a.deg++; b.deg++; } }
}
function tick() {
  const list = [...nodes.values()].filter(shown), k = alpha, sp = V.spread;
  for (let i = 0; i < list.length; i++) for (let j = i + 1; j < list.length; j++) {
    const a = list[i], b = list[j]; let dx = b.x - a.x, dy = b.y - a.y, d2 = dx * dx + dy * dy || 0.01;
    if (d2 > 160000 * sp) continue;
    const f = 1800 * sp / d2 * k, d = Math.sqrt(d2); dx /= d; dy /= d;
    a.vx -= dx * f; a.vy -= dy * f; b.vx += dx * f; b.vy += dy * f;
  }
  for (const e of edges.values()) {
    const a = nodes.get(e.from), b = nodes.get(e.to); if (!a || !b || !shown(a) || !shown(b) || !linkShown(e)) continue;
    const dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx * dx + dy * dy) || 0.01, f = (d - 90 * sp) * 0.04 * k;
    a.vx += dx / d * f; a.vy += dy / d * f; b.vx -= dx / d * f; b.vy -= dy / d * f;
  }
  for (const n of list) {
    n.vx -= n.x * 0.004 * k; n.vy -= n.y * 0.004 * k;
    if (n === drag.node) { n.vx = n.vy = 0; continue; }
    n.vx *= 0.82; n.vy *= 0.82; n.x += n.vx; n.y += n.vy;
  }
  alpha *= 0.985;
}
let running = false;
function loop() {
  if (running) return; running = true;
  const step = () => { tick(); draw(); if (alpha > 0.02 || drag.node) requestAnimationFrame(step); else running = false; };
  requestAnimationFrame(step);
}
function toScreen(x, y) { return [(x * view.k + view.x) * dpr + cv.width / 2, (y * view.k + view.y) * dpr + cv.height / 2]; }
function toWorld(px, py) { return [((px * dpr - cv.width / 2) / dpr - view.x) / view.k, ((py * dpr - cv.height / 2) / dpr - view.y) / view.k]; }
function draw() {
  const css = getComputedStyle(document.body), ink = css.getPropertyValue('--ink').trim() || '#1c2330', dim = css.getPropertyValue('--dim').trim() || '#667085';
  ctx.clearRect(0, 0, cv.width, cv.height);
  ctx.lineWidth = 1.2 * dpr; ctx.font = (11 * dpr) + 'px system-ui, sans-serif';
  for (const e of edges.values()) {
    const a = nodes.get(e.from), b = nodes.get(e.to); if (!a || !b || !shown(a) || !shown(b) || !linkShown(e)) continue;
    const [x1, y1] = toScreen(a.x, a.y), [x2, y2] = toScreen(b.x, b.y);
    const hot = sel && (sel.key === a.key || sel.key === b.key);
    ctx.strokeStyle = hot ? dim : 'rgba(128,128,128,.35)'; ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    if (V.edgeNames && (view.k > 0.9 || hot)) { ctx.fillStyle = dim; ctx.textAlign = 'center'; ctx.fillText(e.type, (x1 + x2) / 2, (y1 + y2) / 2 - 3 * dpr); }
  }
  for (const n of nodes.values()) {
    if (!shown(n)) continue;
    const base = V.size === 'degree' ? 6 + Math.min(14, Math.sqrt(n.deg || 0) * 3) : 8;
    const [x, y] = toScreen(n.x, n.y), r = (n === sel ? base + 3 : base) * V.scale * dpr * Math.max(0.7, Math.min(1.4, view.k));
    ctx.beginPath(); ctx.arc(x, y, r, 0, 6.283); ctx.fillStyle = color(n.label); ctx.fill();
    if (n === sel) { ctx.lineWidth = 3 * dpr; ctx.strokeStyle = ink; ctx.stroke(); ctx.lineWidth = 1.2 * dpr; }
    if (n === sel || V.names === 'always' || (V.names === 'zoom' && view.k > 0.55)) {
      ctx.fillStyle = ink; ctx.textAlign = 'left'; const name = String(n.name || '');
      ctx.fillText(name.length > 28 ? name.slice(0, 27) + '...' : name, x + r + 4 * dpr, y + 4 * dpr);
    }
  }
}
function hit(px, py) {
  const [wx, wy] = toWorld(px, py); let best = null, bd = (16 / view.k) ** 2;
  for (const n of nodes.values()) { if (!shown(n)) continue; const d = (n.x - wx) ** 2 + (n.y - wy) ** 2; if (d < bd) { bd = d; best = n; } }
  return best;
}

// --- touch and mouse -----------------------------------------------------------------------------------
const pointers = new Map(), drag = { node: null, pan: false, moved: false, pinch: 0, lastTap: 0 };
cv.addEventListener('pointerdown', ev => {
  cv.setPointerCapture(ev.pointerId); const r = cv.getBoundingClientRect(), px = ev.clientX - r.left, py = ev.clientY - r.top;
  pointers.set(ev.pointerId, { x: px, y: py });
  if (pointers.size === 2) { const [a, b] = [...pointers.values()]; drag.pinch = Math.hypot(a.x - b.x, a.y - b.y); drag.node = null; return; }
  drag.node = hit(px, py); drag.pan = !drag.node; drag.moved = false; drag.sx = px; drag.sy = py;
  if (drag.node) { alpha = Math.max(alpha, 0.3); loop(); }
});
cv.addEventListener('pointermove', ev => {
  if (!pointers.has(ev.pointerId)) return;
  const r = cv.getBoundingClientRect(), px = ev.clientX - r.left, py = ev.clientY - r.top, prev = pointers.get(ev.pointerId);
  pointers.set(ev.pointerId, { x: px, y: py });
  if (pointers.size === 2) {
    const [a, b] = [...pointers.values()], d = Math.hypot(a.x - b.x, a.y - b.y);
    if (drag.pinch) zoomAt((a.x + b.x) / 2, (a.y + b.y) / 2, d / drag.pinch); drag.pinch = d; drag.moved = true; return;
  }
  if (Math.abs(px - drag.sx) + Math.abs(py - drag.sy) > 4) drag.moved = true;
  if (drag.node) { const [wx, wy] = toWorld(px, py); drag.node.x = wx; drag.node.y = wy; draw(); }
  else if (drag.pan) { view.x += px - prev.x; view.y += py - prev.y; draw(); }
});
function up(ev) {
  if (!pointers.has(ev.pointerId)) return;
  const r = cv.getBoundingClientRect(), px = ev.clientX - r.left, py = ev.clientY - r.top;
  pointers.delete(ev.pointerId); if (pointers.size) return; drag.pinch = 0;
  if (!drag.moved) {
    const n = hit(px, py), now = Date.now();
    if (n && sel === n && now - drag.lastTap < 350) expand(n); else select(n);
    drag.lastTap = now;
  }
  drag.node = null; drag.pan = false;
}
cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up);
cv.addEventListener('wheel', ev => { ev.preventDefault(); const r = cv.getBoundingClientRect(); zoomAt(ev.clientX - r.left, ev.clientY - r.top, Math.exp(-ev.deltaY * 0.0015)); }, { passive: false });
function zoomAt(px, py, f) {
  const [wx, wy] = toWorld(px, py); view.k = Math.max(0.2, Math.min(4, view.k * f));
  const [sx, sy] = toScreen(wx, wy); view.x -= (sx - px * dpr) / dpr; view.y -= (sy - py * dpr) / dpr; draw();
}

// --- details, search, presets, Cypher ------------------------------------------------------------------
function select(n) {
  sel = n; draw(); const d = $('detail'); d.replaceChildren();
  const h = document.createElement('h2'); h.textContent = 'Details'; d.append(h);
  if (!n) { const p = document.createElement('p'); p.className = 'dim'; p.textContent = 'Tap a node to see what it is.'; d.append(p); return; }
  const t = document.createElement('div'); t.innerHTML = '<span class="badge"></span> <b></b>';
  t.querySelector('.badge').textContent = n.label; t.querySelector('.badge').style.background = color(n.label); t.querySelector('.badge').style.color = '#fff';
  t.querySelector('b').textContent = n.name; d.append(t);
  const tbl = document.createElement('table');
  for (const [k, v] of Object.entries(n.props || {})) {
    if (v == null || v === '') continue; const tr = tbl.insertRow(); tr.insertCell().textContent = k;
    tr.insertCell().textContent = typeof v === 'object' ? JSON.stringify(v) : String(v);
  }
  const links = [...edges.values()].filter(e => e.from === n.key || e.to === n.key).length;
  const p = document.createElement('p'); p.className = 'dim'; p.textContent = links + ' connection(s) shown.';
  const row = document.createElement('div'); row.className = 'row';
  const ex = document.createElement('button'); ex.className = 'primary'; ex.type = 'button'; ex.textContent = 'Expand'; ex.onclick = () => expand(n);
  const hide = document.createElement('button'); hide.type = 'button'; hide.textContent = 'Hide'; hide.onclick = () => remove(n);
  row.append(ex, hide); d.append(tbl, p, row);
}
function remove(n) {
  nodes.delete(n.key); for (const [k, e] of edges) if (e.from === n.key || e.to === n.key) edges.delete(k);
  select(null); degrees(); legend();
}
async function show(promise, around) {
  $('msg').textContent = '';
  try { const g = await promise; merge(g, around); if (!g.nodes.length) $('msg').textContent = 'Nothing found.'; return g; }
  catch (e) {
    if (e.status === 503) { $('off').hidden = false; $('ui').hidden = true; $('offwhy').textContent = e.message; }
    else $('msg').textContent = e.message;
  }
}
async function expand(n) {
  await show(api('/v1/graph/expand?' + new URLSearchParams({ id: n.id, label: n.label })), n);
  select(nodes.get(n.key));
}
function legend() {
  const seen = new Set([...nodes.values()].filter(shown).map(n => n.label));
  $('legend').replaceChildren(...[...seen].sort().map(l => { const s = document.createElement('span'); s.innerHTML = '<i></i>';
    s.querySelector('i').style.background = color(l); s.append(l); return s; }));
}
// --- the view settings: what is shown, how it looks ---------------------------------------------------
const TYPES_LIST = ['FROM', 'TO', 'USES', 'AFFECTS', 'ALONG', 'NEAR', 'DRIVES', 'CALLS_AT', 'COVERS', 'CROSSES', 'IN'];
function toggle(list, v, on) { const i = list.indexOf(v); if (on && i >= 0) list.splice(i, 1); else if (!on && i < 0) list.push(v); }
function buildView(labels) {
  $('vLabels').replaceChildren(...labels.map(l => {
    const w = document.createElement('label'), c = document.createElement('input'), col = document.createElement('input');
    c.type = 'checkbox'; c.checked = !V.hiddenLabels.includes(l);
    c.onchange = () => { toggle(V.hiddenLabels, l, c.checked); saveView(); alpha = 0.5; legend(); loop(); };
    col.type = 'color'; col.value = color(l);
    col.oninput = () => { V.colors[l] = col.value; saveView(); legend(); draw(); };
    w.append(c, col, l); return w; }));
  $('vTypes').replaceChildren(...TYPES_LIST.map(t => {
    const w = document.createElement('label'), c = document.createElement('input');
    c.type = 'checkbox'; c.checked = !V.hiddenTypes.includes(t);
    c.onchange = () => { toggle(V.hiddenTypes, t, c.checked); saveView(); alpha = 0.5; loop(); };
    w.append(c, t); return w; }));
  $('vSize').value = V.size; $('vNames').value = V.names; $('vEdgeNames').checked = V.edgeNames;
  $('vScale').value = V.scale; $('vSpread').value = V.spread;
}
$('vSize').onchange = () => { V.size = $('vSize').value; saveView(); draw(); };
$('vNames').onchange = () => { V.names = $('vNames').value; saveView(); draw(); };
$('vEdgeNames').onchange = () => { V.edgeNames = $('vEdgeNames').checked; saveView(); draw(); };
$('vScale').oninput = () => { V.scale = +$('vScale').value; saveView(); draw(); };
$('vSpread').oninput = () => { V.spread = +$('vSpread').value; saveView(); alpha = 0.6; loop(); };
$('vReset').onclick = () => { V = Object.assign({}, DEFAULT_VIEW, { hiddenLabels: [], hiddenTypes: [], colors: {} }); saveView(); buildView(knownLabels); legend(); alpha = 0.6; loop(); };
let knownLabels = Object.keys(COLORS);

let searchTimer = 0;
$('q').oninput = () => {
  clearTimeout(searchTimer); const q = $('q').value.trim();
  if (!q) { $('found').replaceChildren(); return; }
  searchTimer = setTimeout(async () => {
    try {
      const g = await api('/v1/graph/search?q=' + encodeURIComponent(q));
      $('found').replaceChildren(...g.nodes.slice(0, 8).map(n => { const b = document.createElement('button'); b.type = 'button';
        b.textContent = n.label + ': ' + n.name; b.onclick = () => { merge({ nodes: [n], edges: [] }); expand(nodes.get(n.key)); $('found').replaceChildren(); $('q').value = ''; }; return b; }));
    } catch (e) { $('msg').textContent = e.message; }
  }, 250);
};
$('preset').onchange = () => { const v = $('preset').value; if (v) show(api('/v1/graph/query', { method: 'POST', body: JSON.stringify({ preset: v }) })); };
$('overview').onclick = () => show(api('/v1/graph/overview'));
$('clear').onclick = () => { nodes.clear(); edges.clear(); select(null); legend(); draw(); };
$('run').onclick = async () => {
  $('cymsg').textContent = 'Running...';
  try {
    const g = await api('/v1/graph/query', { method: 'POST', body: JSON.stringify({ cypher: $('cy').value }) });
    merge(g); const tbl = $('rows'); tbl.replaceChildren();
    if (g.columns.length) { const h = tbl.insertRow(); for (const c of g.columns) { const th = document.createElement('th'); th.textContent = c; h.append(th); } }
    for (const r of g.rows.slice(0, 100)) { const tr = tbl.insertRow(); for (const v of r) tr.insertCell().textContent = typeof v === 'object' ? JSON.stringify(v) : String(v); }
    $('cymsg').textContent = g.rows.length + ' row(s), ' + g.nodes.length + ' node(s)' + (g.truncated ? ' (cut short)' : '') + '.';
  } catch (e) { $('cymsg').textContent = e.message; if (e.status === 503) { $('off').hidden = false; $('ui').hidden = true; } }
};
$('rebuild').onclick = async () => {
  $('offwhy').textContent = 'Rebuilding...';
  try { const r = await api('/v1/graph/rebuild', { method: 'POST' }); $('offwhy').textContent = 'Built: ' + JSON.stringify(r); $('off').hidden = true; $('ui').hidden = false; load(); }
  catch (e) { $('offwhy').textContent = e.message; }
};
async function load() {
  $('msg').textContent = '';
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  size();
  try {
    const p = await api('/v1/graph/presets');
    $('preset').replaceChildren(new Option('Ready-made questions...', ''), ...p.presets.map(x => new Option(x.title, x.id)));
    $('labels').textContent = p.labels.join(', ');
    knownLabels = p.labels; buildView(knownLabels);
  } catch (e) { $('msg').textContent = e.message; return; }
  show(api('/v1/graph/overview'));
}
$('go').onclick = load;
buildView(knownLabels);
if (tok.value) load(); else size();
</script></main></body></html>"""
