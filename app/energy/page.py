"""The Energy page: what electricity costs now and over the next two days, and Octopus's sessions."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant energy</title>
<style>
/*BASE_CSS*/
  .band { font-size: 13px; font-weight: 700; padding: 2px 10px; border-radius: 999px; }
  .band.cheap { background: var(--good-bg); color: var(--good); } .band.standard { background: var(--chip); color: var(--dim); }
  .band.peak { background: var(--bad-bg); color: var(--bad); }
  .price { font-size: 40px; font-weight: 700; line-height: 1.1; } .price small { font-size: 16px; color: var(--dim); font-weight: 500; }
  svg.chart { width: 100%; height: auto; display: block; }
  svg.chart text { fill: var(--dim); font-size: 11px; } svg.chart .grid { stroke: var(--line); stroke-width: 1; }
  svg.chart .b-cheap { fill: var(--good); } svg.chart .b-standard { fill: var(--dim); opacity: .55; } svg.chart .b-peak { fill: var(--bad); }
  svg.chart .nowline { stroke: var(--accent); stroke-width: 2; stroke-dasharray: 4 3; }
  .key span { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin: 0 4px 0 12px; vertical-align: -1px; }
  .key span.c { background: var(--good); } .key span.s { background: var(--dim); opacity: .55; } .key span.p { background: var(--bad); }
  .tile { flex: 1 1 200px; background: var(--chip); border-radius: 12px; padding: 10px 12px; }
  .tile .k { font-size: 13px; color: var(--dim); } .tile .v { font-size: 22px; font-weight: 700; } .tile .m { font-size: 13px; color: var(--dim); }
  .live { background: var(--good-bg); } .live .v { color: var(--good); }
</style></head><body><main>
<!--NAV-->
<h1>Energy</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary" type="button">Load</button></div>
<p id="msg" class="err"></p>
<div id="body" hidden>
  <div class="card"><div class="hero">
    <div><div class="dim">Electricity now</div><div class="price"><span id="rate"></span> <small>p/kWh</small></div>
      <p><span id="band" class="band"></span> <span id="until" class="dim"></span></p></div>
    <div class="say" id="say"></div></div></div>
  <div class="card"><h2>Next 48 hours</h2><div id="chart"></div>
    <p class="dim key"><span class="c"></span>cheap<span class="s"></span>standard<span class="p"></span>peak
      <span style="background:none;width:auto"> · dashed line is now. Prices include VAT.</span></p></div>
  <div class="card"><h2>Coming up <span class="push"><label class="dim">cheapest block of
    <select id="hours"><option>1</option><option>2</option><option selected>3</option><option>4</option><option>6</option></select> hours</label></span></h2>
    <div class="next" id="tiles"></div></div>
  <div class="card"><h2>Octopus sessions</h2><div class="next" id="sessions"></div>
    <p class="dim" id="snote"></p></div>
  <details class="card"><summary>All upcoming rates</summary><div class="scroll"><table><thead><tr><th>From</th><th>To</th><th>p/kWh</th><th>Band</th></tr></thead><tbody id="rows"></tbody></table></div></details>
  <p class="dim" id="tariff"></p>
</div>
<p class="dim">Rates from Octopus Energy. World Assistant reports prices; Home Assistant decides what to do with them.</p>
<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
async function get(url) {
  const r = await fetch(url, { headers: { Authorization: 'Bearer ' + tok.value } });
  if (!r.ok) { let d = r.status; try { d = (await r.json()).detail; } catch (e) {} throw new Error(typeof d === 'string' ? d : url + ' returned ' + r.status); }
  return r.json();
}
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
const hm = s => new Date(s).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
const day = s => new Date(s).toLocaleDateString('en-GB', { weekday: 'short' });
const when = s => { const d = new Date(s), n = new Date(); return (d.toDateString() === n.toDateString() ? 'today' : day(s)) + ' ' + hm(s); };
const mins = m => m < 60 ? m + ' min' : (Math.floor(m / 60) + ' h' + (m % 60 ? ' ' + (m % 60) + ' min' : ''));
const NS = 'http:' + '//www.w3.org/2000/svg';  // an XML namespace name, not a request
function sv(tag, attrs, text) { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (text != null) e.textContent = text; return e; }
function tile(cls, k, v, m) { const t = el('div', 'tile ' + (cls || '')); t.append(el('div', 'k', k), el('div', 'v', v)); if (m) t.append(el('div', 'm', m)); return t; }

function draw(slots) {
  const box = $('chart'); box.replaceChildren();
  if (!slots.length) { box.append(el('p', 'dim', 'No rates yet.')); return; }
  const W = 720, H = 240, L = 34, R = 8, T = 10, B = 26;
  const t0 = new Date(slots[0].start).getTime(), t1 = new Date(slots[slots.length - 1].end).getTime();
  const ymax = Math.max(10, Math.ceil(Math.max(...slots.map(s => s.pence)) / 10) * 10), ymin = Math.min(0, ...slots.map(s => s.pence));
  const x = t => L + (t - t0) / (t1 - t0) * (W - L - R), y = v => T + (H - T - B) * (1 - (v - ymin) / (ymax - ymin));
  const svg = sv('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'chart', role: 'img' });
  for (let v = Math.ceil(ymin / 10) * 10; v <= ymax; v += 10) {
    svg.append(sv('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), class: 'grid' }));
    svg.append(sv('text', { x: L - 5, y: y(v) + 4, 'text-anchor': 'end' }, String(v)));
  }
  for (const s of slots) {
    const a = new Date(s.start).getTime(), b = new Date(s.end).getTime();
    const r = sv('rect', { x: x(a), y: Math.min(y(s.pence), y(0)), width: Math.max(1, x(b) - x(a) - 0.6), height: Math.abs(y(0) - y(s.pence)), class: 'b-' + s.band });
    r.append(sv('title', {}, when(s.start) + ' to ' + hm(s.end) + ': ' + s.pence.toFixed(2) + 'p (' + s.band + ')'));
    svg.append(r);
  }
  const first = new Date(t0); first.setMinutes(0, 0, 0);
  for (let t = first.getTime() + 3600000; t < t1; t += 3600000) {
    const d = new Date(t);
    if (d.getHours() % 6 === 0) svg.append(sv('text', { x: x(t), y: H - 8, 'text-anchor': 'middle' }, d.getHours() === 0 ? day(t) : hm(t)));
  }
  const now = Date.now();
  if (now > t0 && now < t1) svg.append(sv('line', { x1: x(now), x2: x(now), y1: T, y2: H - B, class: 'nowline' }));
  box.append(svg);
}

function windowTile(k, w, band) {
  if (!w) return tile('', k, 'none in the rates held');
  return tile('', k, hm(w.start) + ' to ' + hm(w.end), when(w.start) + ' · ' + w.pence.toFixed(2) + 'p average');
}

function sessionTiles(s) {
  const box = $('sessions'); box.replaceChildren();
  const sv_ = s.saving || {}, fr = s.free || {};
  const one = (label, blk, joinedMatters) => {
    if (blk.now) box.append(tile('live', label + ' now', 'On', 'until ' + hm(blk.current.end) + (joinedMatters ? (blk.now_joined ? ' · you joined' : ' · you have not joined') : '')));
    const n = blk.next;
    if (n) box.append(tile('', 'Next ' + label.toLowerCase(), when(n.start), mins(n.minutes) + ' long' + (n.octopoints_per_kwh ? ' · ' + n.octopoints_per_kwh + ' Octopoints/kWh' : '') + (joinedMatters ? (n.joined ? ' · joined' : ' · not joined') : '')));
    else if (!blk.now) box.append(tile('', label, 'none announced'));
  };
  one('Saving Session', sv_, true); one('Free electricity', fr, false);
  $('snote').textContent = 'Saving Sessions pay you Octopoints for using less electricity in the hour. Join them in the Octopus app. Free electricity sessions come from a community-run list.';
}

async function load() {
  msg.textContent = ''; try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  try {
    const [now, rates] = await Promise.all([get('/v1/energy/now?hours=' + $('hours').value), get('/v1/energy/rates?hours=48')]);
    $('body').hidden = false;
    if (!now.available) {
      $('rate').textContent = '-'; $('band').textContent = ''; $('until').textContent = '';
      $('say').textContent = 'No rates yet. Check that the Octopus account or tariff is set (docs/energy.md).';
      draw([]); $('tiles').replaceChildren(); sessionTiles(now.sessions || {}); return;
    }
    $('rate').textContent = now.now.pence.toFixed(2);
    $('band').textContent = now.now.band; $('band').className = 'band ' + now.now.band;
    $('until').textContent = 'until ' + hm(now.now.until);
    const n = now.next;
    $('say').textContent = n ? ('Next: ' + n.band + ' at ' + n.pence.toFixed(2) + 'p in ' + mins(n.minutes) + ', from ' + hm(n.at) + '.') : 'The rate stays the same for as long as rates are held.';
    draw(rates.slots);
    const tiles = $('tiles'); tiles.replaceChildren();
    tiles.append(windowTile(now.cheap_now ? 'Cheap rate (on now)' : 'Next cheap rate', now.next_cheap),
      windowTile('Next peak', now.next_peak),
      windowTile('Cheapest ' + $('hours').value + ' hours', now.cheapest_block),
      tile('', 'Average, next 24 h', now.average_pence.toFixed(2) + 'p'));
    sessionTiles(now.sessions || {});
    const rows = $('rows'); rows.replaceChildren();
    for (const s of rates.slots) { const tr = el('tr'); tr.append(el('td', null, when(s.start)), el('td', null, hm(s.end)), el('td', null, s.pence.toFixed(2)), el('td', null, s.band)); rows.append(tr); }
    $('tariff').textContent = 'Tariff ' + now.tariff + '. Rates held until ' + when(now.covers_until) + '.';
  } catch (e) { msg.textContent = e.message; }
}
$('go').onclick = load; $('hours').onchange = load;
if (tok.value) load();
setInterval(() => { if (tok.value && !document.hidden) load(); }, 60000);
</script></main></body></html>"""

page_router = APIRouter()


@page_router.get("/energy", response_class=HTMLResponse, include_in_schema=False)
async def energy_page() -> str:
    """The page holds no data; it asks for the token and calls /v1/energy."""
    return themed(PAGE, "energy")
