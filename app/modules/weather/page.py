"""The weather page (/weather, a tab of Environment): now, the next 48 hours, each day ahead with its heating
degree-hours, and the last month as it turned out. The page holds no data and never builds HTML from it."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

page_router = APIRouter()

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant weather</title>
<style>
/*BASE_CSS*/
.days { display: grid; gap: 10px; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); }
.day b { display: block; font-size: 1.05em; } .day .t { font-size: 1.4em; font-weight: 650; }
.hrs { display: flex; gap: 2px; align-items: flex-end; height: 120px; overflow-x: auto; padding-top: 18px; }
.hrs div { flex: 0 0 18px; background: var(--series-1); border-radius: 3px 3px 0 0; position: relative; }
.hrs div.cold { background: var(--series-3); }
.hrs div span { position: absolute; top: -16px; left: -2px; font-size: 10px; color: var(--dim); }
table.hist { width: 100%; border-collapse: collapse; font-size: .9em; } table.hist td, table.hist th { padding: 4px 6px; border-bottom: 1px solid var(--line); text-align: left; }
</style></head><body><main>
<!--NAV-->
<h1>Weather</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary" type="button">Load</button></div>
<p id="msg" class="err"></p>
<div class="card hero"><div class="say" id="say">Loading…</div><div class="dim" id="when"></div></div>
<div class="card"><h2>The next 48 hours</h2><div class="hrs" id="hrs" role="img" aria-label="Temperature each hour"></div>
  <p class="dim small">Each bar an hour; blue below 15.5 °C, where a home needs heating.</p></div>
<div class="card"><h2>Days ahead</h2><div class="days" id="days"></div>
  <p class="dim small">Heating degree-hours: how far below 15.5 °C, added up hour by hour. Twice the number is about twice the heating.</p></div>
<details class="card"><summary>The last 30 days, as they turned out</summary><table class="hist" id="hist"></table></details>
<p class="dim small" id="credit"></p>
<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
async function api(path) {
  const r = await fetch(path, { headers: { Authorization: 'Bearer ' + tok.value } });
  if (!r.ok) { let d = r.status; try { d = (await r.json()).detail; } catch (e) {} throw new Error(d); }
  return r.json();
}
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const c = v => v == null ? '–' : Math.round(v) + '°';
const dayName = d => new Date(d + 'T12:00:00').toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
async function load() {
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  msg.textContent = '';
  try {
    const w = await api('/v1/weather');
    $('say').textContent = c(w.now.temp_c) + ' and ' + (w.now.words || '') + ' (feels like ' + c(w.now.apparent_c) + ').';
    $('when').textContent = 'Forecast fetched ' + (w.fetched_at ? new Date(w.fetched_at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : '–');
    const temps = w.hours.map(h => h.temp_c).filter(v => v != null), lo = Math.min(...temps, 0), hi = Math.max(...temps, 1);
    $('hrs').replaceChildren(...w.hours.map(h => {
      const d = el('div', null, h.temp_c != null && h.temp_c < 15.5 ? 'cold' : '');
      d.style.height = Math.max(4, ((h.temp_c ?? lo) - lo) / (hi - lo) * 90) + 'px';
      d.title = new Date(h.at).toLocaleString('en-GB', { weekday: 'short', hour: '2-digit', minute: '2-digit' }) + ': ' + c(h.temp_c) + ((h.precip_mm || 0) > 0 ? ', ' + h.precip_mm + ' mm rain' : '');
      if (new Date(h.at).getHours() % 6 === 0) d.append(el('span', new Date(h.at).toLocaleTimeString('en-GB', { hour: '2-digit' })));
      return d;
    }));
    $('days').replaceChildren(...w.days.map(d => {
      const box = el('div', null, 'day card');
      box.append(el('b', dayName(d.day)), el('div', c(d.low_c) + ' to ' + c(d.high_c), 't'), el('div', d.mostly || '', 'dim'),
        el('div', (d.rain_mm ? d.rain_mm + ' mm rain · ' : '') + d.sun_kwh_m2 + ' kWh/m² sun'),
        el('div', d.degree_hours + ' degree-hours (16:00–19:00: ' + d.evening_degree_hours + ', ' + c(d.evening_c) + ')', 'dim'));
      return box;
    }));
    $('credit').textContent = w.credit;
    const h = await api('/v1/weather/history?days=30'), t = $('hist'); t.replaceChildren();
    const hd = el('tr'); ['Day', 'Low', 'High', 'Rain', 'Degree-hours', '16:00–19:00'].forEach(x => hd.append(el('th', x))); t.append(hd);
    for (const d of h.days.slice().reverse()) { if (!d.hours) continue; const tr = el('tr');
      [dayName(d.day), c(d.low_c), c(d.high_c), d.rain_mm + ' mm', d.degree_hours, d.evening_degree_hours + ' (' + c(d.evening_c) + ')'].forEach(x => tr.append(el('td', String(x)))); t.append(tr); }
  } catch (e) { msg.textContent = e.message; }
}
$('go').onclick = load;
load();
</script></main></body></html>"""


@page_router.get("/weather", response_class=HTMLResponse, include_in_schema=False)
async def weather_page(request: Request) -> str:
    return themed(PAGE, "weather", request)
