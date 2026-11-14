"""The tariffs page (/tariffs, a tab of Energy): the best tariff to move to, every tariff ranked on your own use at
today's rates (as used, and with your battery moving use into its cheap windows), exports apart, and tariffs typed
from other suppliers' quotes. The page holds no data and never builds HTML from it."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

page_router = APIRouter()

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant tariffs</title>
<style>
/*BASE_CSS*/
.wrap { overflow-x: auto; }
table.t { width: 100%; border-collapse: collapse; font-size: .9em; } table.t td, table.t th { padding: 5px 6px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
table.t td.n, table.t th.n { text-align: right; white-space: nowrap; } tr.mine td { font-weight: 650; } tr.best td:first-child::before { content: "★ "; color: var(--series-2); }
.day { display: flex; height: 10px; width: 144px; border-radius: 3px; overflow: hidden; margin-top: 3px; } .day i { flex: 1; background: var(--line); } .day i.c { background: var(--series-1); }
.cond { font-size: .85em; color: var(--dim); } .say { font-size: 1.15em; }
form.grid { display: grid; gap: 8px; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); } form.grid label { display: grid; gap: 3px; }
textarea { width: 100%; min-height: 70px; font: inherit; }
</style></head><body><main>
<!--NAV-->
<h1>Tariffs</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary" type="button">Load</button> <button id="again" type="button">Work it out again</button></div>
<p id="msg" class="err"></p>
<div class="card hero"><div class="say" id="say">Loading…</div><p class="dim small" id="basis"></p></div>
<div class="card"><h2>Import tariffs</h2><div class="wrap"><table class="t" id="imp"></table></div>
  <p class="dim small">A year at each tariff's rates today, from your own half-hours. <b>As used</b>: your half-hours as they
  were (which already follow your current tariff's cheap hours). <b>With battery</b>: your battery filling in every cheap
  window of that tariff (as many as it has each day, at its charge rate) and covering use outside them until empty. The
  bar is the day from midnight, its cheap half-hours coloured.</p></div>
<div class="card"><h2>Export tariffs</h2><div class="wrap"><table class="t" id="exp"></table></div>
  <p class="dim small">What your exported half-hours would earn in a year at each rate today. Flux and Intelligent Flux
  come with their own export only.</p></div>
<details class="card"><summary>Settings</summary>
<form class="grid" id="f">
  <label>Count readings from <input id="count_from" type="date"></label>
  <label>House battery usable (kWh) <input id="capacity_kwh" type="number" step="0.1" min="0"></label>
  <label>Battery charge rate (kW) <input id="charge_kw" type="number" step="0.1" min="0"></label>
  <label>Region letter (if not from your tariff) <input id="region" maxlength="1"></label>
</form>
<p class="dim small">Readings are never counted from more than a year back. Set the date the house last changed (solar
fitted, gas removed) so older use does not skew the answer.</p>
<p><button id="save" type="button">Save</button> <span id="saved" class="dim"></span></p></details>
<details class="card"><summary>Tariffs from other suppliers</summary>
<div id="manual"></div>
<h3>Add one from a quote</h3>
<form class="grid" id="mf">
  <label>Name <input id="m_name" placeholder="EDF GoElectric 35"></label>
  <label>Supplier <input id="m_supplier"></label>
  <label>Standing charge (p a day) <input id="m_standing" type="number" step="0.01" min="0"></label>
  <label>Export (p/kWh, if it comes with one) <input id="m_export" type="number" step="0.01" min="0"></label>
  <label>Exit fee (£) <input id="m_exit" type="number" step="1" min="0"></label>
  <label>Notes (what it needs) <input id="m_notes"></label>
</form>
<label>Unit rates, one band a line: from-to pence (the first line covers any hour no line does)
<textarea id="m_bands" placeholder="00:00-00:00 26.1&#10;00:30-05:30 8.5"></textarea></label>
<p><button id="add" type="button">Add</button> <span id="added" class="dim"></span></p></details>
<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
async function api(path, opts) {
  const r = await fetch(path, { ...(opts || {}), headers: { Authorization: 'Bearer ' + tok.value, 'Content-Type': 'application/json' } });
  if (!r.ok) { let d = r.status; try { d = (await r.json()).detail; } catch (e) {} throw new Error(typeof d === 'string' ? d : JSON.stringify(d)); }
  return r.json();
}
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const gbp = v => v == null ? '–' : (v < 0 ? '−£' : '£') + Math.abs(Math.round(v)).toLocaleString('en-GB');
let settings = null;
function dayBar(pat) {
  const d = el('div', null, 'day'); if (!pat) return d;
  const lo = Math.min(...pat), hi = Math.max(...pat);
  pat.forEach((v, i) => { const x = el('i', null, hi - lo >= 0.5 && v <= lo + 0.25 * (hi - lo) + 1e-9 ? 'c' : ''); x.title = String(Math.floor(i / 2)).padStart(2, '0') + ':' + (i % 2 ? '30' : '00') + ' ' + v + 'p'; d.append(x); });
  return d;
}
function row(r, cols, best) {
  const tr = el('tr', null, (r.current ? 'mine' : '') + (best ? ' best' : ''));
  const name = el('td'); name.append(el('div', r.name + (r.current ? ' (yours)' : '')), el('div', r.supplier || '', 'cond'));
  (r.conditions || []).forEach(c => name.append(el('div', c, 'cond')));
  if (r.exit_fee_p) name.append(el('div', 'Exit fee £' + (r.exit_fee_p / 100).toFixed(0), 'cond'));
  tr.append(name);
  cols.forEach(([f, cls]) => { const td = el('td', null, cls || ''); const v = f(r); if (v instanceof Node) td.append(v); else td.textContent = v; tr.append(td); });
  return tr;
}
function head(t, names) { const h = el('tr'); names.forEach(([n, c]) => h.append(el('th', n, c || ''))); t.append(h); }
function show(res) {
  const s = res.suggestion;
  if (res.error) { $('say').textContent = res.error; return; }
  if (!s) $('say').textContent = 'No tariff could be priced.';
  else if (s.already_best) $('say').textContent = 'You are on the cheapest tariff for your use: ' + s.import_name + (s.export_name ? ', with ' + s.export_name : '') + ', about ' + gbp(s.year_pounds) + ' a year.';
  else $('say').textContent = 'Move to ' + s.import_name + (s.export_name ? ' with ' + s.export_name : '') + ': about ' + gbp(s.year_pounds) + ' a year' +
    (s.saving_pounds != null ? ', ' + gbp(s.saving_pounds) + ' less than now (' + gbp(s.now_year_pounds) + ').' : '.');
  $('basis').textContent = 'Your readings ' + res.from + ' to ' + res.to + ' (' + res.days + ' days' + (res.full_year ? '' : '; less than a year, so the yearly figures are scaled up and lean towards the season these days were in') + '), region ' + res.region +
    ', at each tariff\\'s rates today. Agile and Tracker change daily and are priced at the last 30 days\\' average. ' +
    (res.battery ? 'Battery ' + res.battery.capacity_kwh + ' kWh at ' + res.battery.charge_kw + ' kW. ' : 'Set your battery to see costs with it shifting use. ') +
    'Worked out ' + new Date(res.computed_at).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) + '.' +
    (res.skipped && res.skipped.length ? ' Not priced: ' + res.skipped.map(x => x.name).join(', ') + '.' : '');
  const mine = res.import.find(r => r.current), t = $('imp'); t.replaceChildren();
  head(t, [['Tariff'], ['Cheap hours'], ['Standing', 'n'], ['Average', 'n'], ['As used', 'n'], ['With battery', 'n'], ['Against yours', 'n']]);
  res.import.forEach(r => t.append(row(r, [
    [r => { const w = r.windows || {}, d = el('div'); d.append(el('div', w.count ? w.hours + ' h in ' + w.count + (w.count > 1 ? ' windows' : ' window') + ', at most ' + w.longest_gap_hours + ' h apart' : 'one rate all day')); d.append(dayBar(r.pattern)); (w.windows || []).length && d.append(el('div', w.windows.join(', '), 'cond')); return d; }],
    [r => r.standing_p == null ? '–' : r.standing_p.toFixed(1) + 'p', 'n'], [r => r.average_p == null ? '–' : r.average_p.toFixed(1) + 'p', 'n'],
    [r => gbp(r.year_pounds), 'n'], [r => gbp(r.shifted_year_pounds), 'n'],
    [r => mine && !r.current ? (r.rank_year_pounds <= mine.rank_year_pounds ? gbp(mine.rank_year_pounds - r.rank_year_pounds) + ' less' : gbp(r.rank_year_pounds - mine.rank_year_pounds) + ' more') : '', 'n'],
  ], s && r.code === s.import)));
  const e = $('exp'); e.replaceChildren(); head(e, [['Tariff'], ['Rate', 'n'], ['A year', 'n']]);
  if (!res.export.length) e.append(el('tr')).append(el('td', 'No export readings yet.'));
  res.export.forEach(r => e.append(row(r, [[r => r.average_p == null ? '–' : r.average_p.toFixed(1) + 'p', 'n'], [r => gbp(-r.year_pounds), 'n']], s && r.code === s.export)));
}
function manualList() {
  const box = $('manual'); box.replaceChildren();
  if (!settings.manual.length) box.append(el('p', 'None yet.', 'dim'));
  settings.manual.forEach((m, i) => {
    const p = el('p'); p.append(el('b', m.name), el('span', ' ' + (m.supplier || '') + ' · ' + m.bands.map(b => b.from + '–' + b.to + ' ' + b.pence + 'p').join(', ') + ' · ' + m.standing_p + 'p a day' + (m.export_p != null ? ' · export ' + m.export_p + 'p' : '') + ' '));
    const b = el('button', 'Remove'); b.type = 'button'; b.onclick = () => { settings.manual.splice(i, 1); save().then(manualList); }; p.append(b); box.append(p);
  });
}
async function save() {
  for (const k of ['count_from', 'capacity_kwh', 'charge_kw', 'region']) { const v = $(k).value.trim(); settings[k] = v === '' ? (k === 'region' ? '' : null) : (k.endsWith('kwh') || k.endsWith('kw') ? Number(v) : v); }
  settings = await api('/v1/tariffs/settings', { method: 'PUT', body: JSON.stringify(settings) });
}
async function load() {
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  msg.textContent = '';
  try {
    const d = await api('/v1/tariffs'); settings = d.settings;
    ['count_from', 'capacity_kwh', 'charge_kw', 'region'].forEach(k => $(k).value = settings[k] ?? '');
    manualList();
    if (d.result) show(d.result); else $('say').textContent = 'Not worked out yet: press "Work it out again" (about half a minute).';
  } catch (e) { msg.textContent = e.message; }
}
$('again').onclick = async () => { $('say').textContent = 'Working it out (every Octopus tariff for your region)…';
  try { show(await api('/v1/tariffs/compare', { method: 'POST' })); } catch (e) { msg.textContent = e.message; } };
$('save').onclick = async () => { try { await save(); $('saved').textContent = 'Saved. Work it out again to see the change.'; } catch (e) { $('saved').textContent = e.message; } };
$('add').onclick = async () => {
  try {
    const bands = $('m_bands').value.split('\\n').map(l => l.trim()).filter(Boolean).map(l => {
      const m = l.match(/^(\\d\\d:\\d\\d)\\s*-\\s*(\\d\\d:\\d\\d)\\s+([\\d.]+)/); if (!m) throw new Error('not a band: ' + l); return { from: m[1], to: m[2], pence: Number(m[3]) }; });
    const name = $('m_name').value.trim(), id = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'tariff';
    const num = k => $(k).value.trim() === '' ? null : Number($(k).value);
    settings.manual.push({ id, name, supplier: $('m_supplier').value.trim(), standing_p: num('m_standing') || 0, bands,
      export_p: num('m_export'), exit_fee_pounds: num('m_exit') || 0, notes: $('m_notes').value.trim() });
    try { await save(); } catch (e) { settings.manual.pop(); throw e; }
    manualList(); $('added').textContent = 'Added. Work it out again to see it ranked.';
  } catch (e) { $('added').textContent = e.message; }
};
$('go').onclick = load;
load();
</script></main></body></html>"""


@page_router.get("/tariffs", response_class=HTMLResponse, include_in_schema=False)
async def tariffs_page(request: Request) -> str:
    return themed(PAGE, "tariffs", request)
