"""A single-page view of the bus tracker, with guided setup. The token is typed in the browser, as
on /fuel and /trains. No external resources, and the page never builds HTML from data."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

page_router = APIRouter()

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant buses</title>
<style>
/*BASE_CSS*/</style></head><body><main>
<!--NAV-->
<h1>Buses</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<select id="tr" aria-label="Tracker"></select><button id="go" class="primary" type="button">Load</button></div>
<p id="msg" class="err"></p>

<section id="view" class="hidden">
  <div class="card hero"><div class="say" id="summary"></div><div><span id="state" class="pill"></span>
    <div class="dim" id="status"></div></div></div>
  <div class="card"><h2>Next buses</h2><div id="next" class="next"></div></div>
  <div class="grid">
    <div class="card"><h2>Reached the target stop today</h2><table id="hits"></table><p id="pending" class="dim"></p></div>
    <div class="card"><h2>Leg times today</h2><table id="cond"></table></div>
  </div>
  <div class="grid">
    <div class="card"><h2>How reliable each bus is</h2><table id="rel"></table></div>
    <div class="card"><h2>Recent events</h2><table id="an"></table></div>
  </div>
</section>

<div class="card" id="setupcard">
  <h2>Set up a tracker</h2>
  <div class="form">
    <div class="row"><label style="flex:1 1 120px">Bus route <input id="line" value="213" maxlength="20"></label>
      <button id="load" type="button" style="align-self:end">Find the stops</button></div>
    <div id="steps" class="form hidden">
      <label>Where do you get on?<select id="board"></select></label>
      <label>Where do you need to get to?<select id="target"></select>
        <span class="hint">Choose the boarding stop for the direction you travel (the name says where that bus is heading). Only stops after it, in that direction, are listed.</span></label>
      <label>Any other stops to time on the way? <span class="hint">Optional. Busy stops where delays build up.</span>
        <div class="row"><select id="watch"></select><button id="addwatch" type="button">Add</button></div>
        <div id="watched" class="chips"></div></label>
      <div class="row">
        <label>Earliest bus I would catch <input id="earliest" type="time" value="07:00"></label>
        <label>I must arrive by <input id="deadline" type="time" value="08:10"></label>
        <label>Tracker name <input id="name" value="main" maxlength="40"></label>
      </div>
      <label style="flex-direction:row;align-items:center;gap:8px"><input id="back" type="checkbox" style="width:auto">
        Also track the way back <span class="hint">the other side of each stop, from where you got off to where you got on</span></label>
      <div class="row hidden" id="backtimes">
        <label>Earliest bus back <input id="bearliest" type="time" value="15:00"></label>
        <label>I must be back by <input id="bdeadline" type="time" value="17:30"></label>
      </div>
      <div class="row"><button id="save" class="primary" type="button">Start tracking</button></div>
      <p id="setupmsg" class="dim"></p>
    </div>
  </div>
  <details style="margin-top:12px"><summary>Advanced: paste settings as JSON</summary>
    <p class="dim">For full control. See docs/bus.md. Saved on your server only.</p>
    <div class="row"><input id="tid" placeholder="Tracker name, e.g. main"></div>
    <textarea id="cfg" spellcheck="false" placeholder='{"line": "213", "stops": [...], "target": {...}}'></textarea>
    <div class="row"><button id="savejson" type="button">Save</button><button id="del" type="button">Delete tracker</button></div>
  </details>
</div>

<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
async function api(method, path, body) {
  const r = await fetch(path, { method, headers: { Authorization: 'Bearer ' + tok.value, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) { let d = r.status; try { d = (await r.json()).detail; } catch (e) {} throw new Error(typeof d === 'string' ? d : JSON.stringify(d)); }
  return r.status === 204 ? null : r.json();
}
const hhmm = iso => iso ? new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : '';
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
function table(id, head, rows, empty) {
  const t = $(id); t.replaceChildren();
  if (!rows.length) { const c = t.insertRow().insertCell(); c.textContent = empty; c.className = 'dim'; return; }
  const h = t.createTHead().insertRow(); head.forEach(x => h.appendChild(el('th', x)));
  for (const row of rows) { const r = t.insertRow(); for (const v of row) { const c = r.insertCell();
    if (v instanceof Node) c.appendChild(v); else c.textContent = v == null ? '' : v; } }
}
const badge = (text, kind) => el('span', text, 'badge ' + kind);
function meter(missRate) {
  const hit = Math.round((1 - missRate) * 100), m = el('div', null, 'meter' + (hit < 50 ? ' bad' : hit < 80 ? ' low' : ''));
  const s = el('span'); s.style.width = hit + '%'; m.appendChild(s); m.title = hit + '% of days it made it'; return m;
}
async function trackers() {
  const list = await api('GET', '/v1/bus/trackers'), keep = $('tr').value;
  $('tr').replaceChildren(...list.map(x => new Option(x.id + (x.enabled ? '' : ' (off)'), x.id)));
  if (keep && list.some(x => x.id === keep)) $('tr').value = keep;
  return list;
}
async function load() {
  msg.textContent = '';
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  try {
    const list = await trackers();
    $('view').classList.toggle('hidden', !list.length);
    if (!list.length) return;
    const base = '/v1/bus/trackers/' + $('tr').value;
    const [live, target, rel, cond, an] = await Promise.all([api('GET', base + '/live'),
      api('GET', base + '/target').catch(() => null), api('GET', base + '/reliability'),
      api('GET', base + '/conditions'), api('GET', base + '/anomalies?limit=15')]);
    $('summary').textContent = target ? target.summary : 'Tracker saved. No target set.';
    const on = target && target.tracking_now;
    $('state').textContent = on ? 'Tracking now' : 'Waiting for the next window'; $('state').className = 'pill' + (on ? ' on' : '');
    $('status').textContent = target ? 'Counting buses from ' + target.earliest + ', arriving by ' + target.deadline + '. ' + live.active.length + ' bus(es) followed.' : '';
    const next = $('next'); next.replaceChildren();
    for (const [key, rows] of Object.entries(live.next)) {
      if (!rows.length) continue;
      const box = el('div', null, 'stop'); box.appendChild(el('div', rows[0].label, 'name'));
      box.appendChild(el('div', rows[0].minutes + ' min', 'mins'));
      if (rows.length > 1) box.appendChild(el('div', 'then ' + rows.slice(1).map(r => r.minutes + ' min').join(', '), 'more'));
      next.appendChild(box);
    }
    if (!next.children.length) next.appendChild(el('p', 'No buses predicted at your stops right now.', 'dim'));
    table('hits', ['Arrived', 'Left start', 'Timetable', 'Late / early', ''],
      target ? target.hits.map(h => [hhmm(h.arrive_at), hhmm(h.board_at), h.slot || '',
        h.delay_min == null ? '' : badge((h.delay_min > 0 ? '+' : '') + h.delay_min + ' min', h.delay_min >= 5 ? 'bad' : h.delay_min >= 2 ? 'warn' : 'good'),
        h.via_return ? el('span', 'ended here', 'dim') : '']) : [], 'None confirmed yet.');
    $('pending').textContent = target && target.pending.length ? 'Scheduled but not yet seen: ' + target.pending.join(', ') : '';
    table('cond', ['Leg', 'Usual', 'Last 30 min', ''], cond.map(c => [c.from + ' to ' + c.to,
      c.day_avg_min == null ? '' : c.day_avg_min + ' min', c.recent_avg_min == null ? '' : c.recent_avg_min + ' min',
      c.delta_min == null ? '' : badge((c.delta_min > 0 ? '+' : '') + c.delta_min + ' min', c.delta_min >= 3 ? 'bad' : c.delta_min >= 1.5 ? 'warn' : 'good')]),
      'No leg times yet today.');
    table('rel', ['Bus', 'Days', 'Made it'], rel.slots.map(s => [s.slot, s.days_scheduled, s.miss_rate == null ? '' : meter(s.miss_rate)]),
      'History builds up over the days the tracker runs.');
    table('an', ['When', 'Bus', 'What happened'], an.map(a => [hhmm(a.at), a.vehicle, anomalyText(a)]), 'Nothing recorded.');
  } catch (e) { msg.textContent = e.message; }
}
const way = t => t ? ' towards ' + t : '';
const gps = l => l ? ' GPS: ' + (l.destination ? 'destination ' + l.destination : 'seen') + (l.bearing != null ? ', bearing ' + Math.round(l.bearing) + '°' : '') + (l.recorded_at ? ' at ' + hhmm(l.recorded_at) : '') + '.' : '';
function anomalyText(a) { return anomalyBase(a) + gps((a.detail || {}).live); }
function anomalyBase(a) {
  const d = a.detail || {}, at = d.stop ? ' at ' + d.stop : '';
  if (a.type === 'early_termination')
    return 'Ended early' + at + ', heading' + way(d.towards) + ' (' + (d.kind || 'ended') + ', ' + d.stops_before_end + ' stops short of the end)'
      + (d.resumed_stop ? '; next seen at ' + d.resumed_stop + (d.resumed_at ? ' ' + hhmm(new Date(d.resumed_at * 1000).toISOString()) : '') : '');
  if (a.type === 'turnaround')
    return 'Turned round at ' + (d.place || '').replaceAll('_', ' ') + (d.from_towards ? ': was heading' + way(d.from_towards) + ', now' + way(d.to_towards) : '')
      + ' (' + d.gap_s + ' s between sides)';
  if (a.type === 'direction_change')
    return 'Changed direction' + at + (d.from_towards ? ': was heading' + way(d.from_towards) + ', now' + way(d.towards) : '');
  if (a.type === 'trip_start') return 'Started its route' + at + (d.towards ? ', heading' + way(d.towards) : '');
  if (a.type === 'reverse_movement' && d.verdict === 'prediction_shift')
    return 'Predictions shifted' + (d.from_stop ? ' (' + d.from_stop + ' then ' + (d.stop || 'an earlier stop') + ')' : '') + ': the GPS shows it still heading' + way(d.live && d.live.towards) + ', so the bus did not go back';
  if (a.type === 'reverse_movement')
    return 'Reported at an earlier stop than before' + (d.from_stop ? ' (' + d.from_stop + ' then ' + (d.stop || 'an earlier stop') + ')' : '')
      + ', same direction: usually the live predictions shifting, not the bus';
  return a.type;
}
/* ---- guided setup ---- */
let stops = null; const watched = new Map();
const label = (d, s) => s.name + ' (towards ' + stops[d].towards + ')';
function fillBoard() {
  const b = $('board'); b.replaceChildren();
  for (const d of Object.keys(stops)) { const g = document.createElement('optgroup'); g.label = 'Towards ' + stops[d].towards;
    for (const s of stops[d].stops.slice(0, -1)) { const o = new Option(label(d, s), s.id); o.dataset.dir = d; g.appendChild(o); } b.appendChild(g); }
  fillTarget();
}
function fillTarget() {
  const o = $('board').selectedOptions[0]; if (!o) return;
  const d = o.dataset.dir, pos = stops[d].stops.findIndex(s => s.id === o.value), after = stops[d].stops.slice(pos + 1);
  $('target').replaceChildren(...after.map(s => new Option(s.name, s.id)));
  $('watch').replaceChildren(...after.slice(0, -1).map(s => new Option(s.name, s.id)));
  watched.clear(); drawChips();
}
function drawChips() {
  const c = $('watched'); c.replaceChildren();
  for (const [sid, name] of watched) { const chip = el('span', name, 'chip'); const x = el('button', '×', 'link');
    x.type = 'button'; x.onclick = () => { watched.delete(sid); drawChips(); }; chip.appendChild(x); c.appendChild(chip); }
}
$('load').onclick = async () => {
  $('setupmsg').textContent = ''; msg.textContent = '';
  try { stops = await api('GET', '/v1/bus/lines/' + encodeURIComponent($('line').value.trim()) + '/stops'); fillBoard(); $('steps').classList.remove('hidden'); }
  catch (e) { msg.textContent = e.message; }
};
$('board').onchange = fillTarget;
$('back').onchange = () => $('backtimes').classList.toggle('hidden', !$('back').checked);
$('addwatch').onclick = () => { const o = $('watch').selectedOptions[0]; if (o) { watched.set(o.value, o.textContent); drawChips(); } };
$('save').onclick = async () => {
  try {
    const r = await api('POST', '/v1/bus/setup', { line: $('line').value.trim(), board: $('board').value, target: $('target').value,
      watch: [...watched.keys()], earliest: $('earliest').value, deadline: $('deadline').value, name: $('name').value.trim() || 'main',
      ...($('back').checked ? { return_earliest: $('bearliest').value, return_deadline: $('bdeadline').value } : {}) });
    $('setupmsg').className = 'ok'; $('setupmsg').textContent = 'Tracking ' + r.stops_tracked.join(', ') + '.' +
      (r.return_side_found ? ' The opposite-side stop at your destination was found automatically.' : '') +
      (r.return_tracker ? ' The way back (' + r.return_tracker.board + ' to ' + r.return_tracker.target + ', ' + r.return_tracker.earliest + ' to ' + r.return_tracker.deadline + ') is tracked as "' + r.return_tracker.id + '".' : '');
    load();
  } catch (e) { $('setupmsg').className = 'err'; $('setupmsg').textContent = e.message; }
};
$('savejson').onclick = async () => {
  try { await api('PUT', '/v1/bus/trackers/' + $('tid').value.trim(), { config: JSON.parse($('cfg').value), enabled: true });
    $('cfg').value = ''; load(); } catch (e) { msg.textContent = e.message; }
};
$('del').onclick = async () => { try { await api('DELETE', '/v1/bus/trackers/' + $('tid').value.trim()); load(); } catch (e) { msg.textContent = e.message; } };
$('go').onclick = load; $('tr').onchange = load;
if (tok.value) load();
setInterval(() => { if (tok.value && !document.hidden) load(); }, 30000);
</script></main></body></html>"""


@page_router.get("/bus", response_class=HTMLResponse, include_in_schema=False)
async def bus_page() -> str:
    return themed(PAGE, "bus")
