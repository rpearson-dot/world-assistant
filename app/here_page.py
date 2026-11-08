"""The Here page: what World Assistant knows about wherever you are, from the browser's location or a postcode.

The page holds no data itself; it calls /v1/here. The browser only gives a location to a secure (https) page, so this
works at the Tailscale address, not the plain address on the home network; the page says so when that is why, and a
postcode can be given instead (always, and pointed to whenever the location cannot be had).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant here</title>
<style>
/*BASE_CSS*/
.parts { display: grid; gap: 10px; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); }
.parts .card { margin: 0; }
.parts h3 { margin: 0 0 4px; font-size: 15px; }
.parts .card.quiet { opacity: .7; }
.bars { display: flex; align-items: flex-end; gap: 3px; height: 42px; margin: 6px 0 2px; } .bars i { flex: 1; background: var(--accent); border-radius: 2px 2px 0 0; }
.small { font-size: 13px; }
.area { font-size: 18px; font-weight: 600; margin: 4px 0; }
form.save, form.pc { display: flex; gap: 8px; flex-wrap: wrap; align-items: end; }
form.pc { margin-top: 10px; }
form.pc input { width: 14em; }
.w3w { margin: 0 0 4px; } .w3w a { color: var(--w3w); font-weight: 600; text-decoration: none; }
</style></head><body><main>
<!--NAV-->
<h1>Here</h1>
<form id="auth" class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button class="primary">Load</button></form>
<p id="msg" class="err"></p>

<div class="card">
<div class="row"><button id="go" type="button" class="primary">Use my location</button>
<button id="again" type="button" hidden>Look again</button></div>
<form id="pc" class="pc"><label>or a postcode or ///three.words <input id="pcode" maxlength="80" placeholder="KT17 1AA" autocomplete="postal-code"></label>
<button>Look up</button></form>
<p id="where" class="dim">Everything World Assistant knows about where you are now, without saving it as a place.</p>
<p id="area" class="area"></p>
<p id="atplace" class="dim" hidden></p>
<p id="w3w" class="w3w" hidden></p>
<p id="hmsg" class="err"></p>
</div>
<div id="parts" class="parts"></div>
<div class="card" id="savecard" hidden><h2>Keep this place</h2>
<form id="save" class="save"><label>Name <input id="pname" maxlength="80" required></label><button>Save as a place</button></form>
<p id="smsg" class="dim">Saved places get Home Assistant sensors, trips and the map too.</p></div>

<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
async function api(method, path, body) {
  const r = await fetch(path, { method, headers: { Authorization: 'Bearer ' + tok.value, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined });
  let d = null; try { d = await r.json(); } catch (e) {}
  if (!r.ok) { let m = d && d.detail; if (Array.isArray(m)) m = m.map(x => x.msg).join('; '); throw new Error(m || ('HTTP ' + r.status)); }
  return d;
}
let point = null;
function locate() {
  return new Promise((ok, no) => {
    if (!window.isSecureContext) return no(new Error('Browsers only share your location with a secure page: open World Assistant at its https (Tailscale) address.'));
    if (!navigator.geolocation) return no(new Error('This browser cannot share its location.'));
    navigator.geolocation.getCurrentPosition(p => ok(p.coords), e => no(new Error(e.code === 1
      ? 'Location is not allowed for this page: allow it in the browser\\'s site settings.' : 'Could not get a location (' + e.message + ').')),
      { enableHighAccuracy: true, timeout: 20000, maximumAge: 60000 });
  });
}
function crimeExtras(c, d) {  // each month's count as small bars, the police team's page, and the credit
  const top = Math.max(...d.trend.map(t => t.total), 1), bars = el('div', 'bars');
  for (const t of d.trend) { const b = el('i'); b.style.height = Math.max(2, Math.round(40 * t.total / top)) + 'px';
    b.title = new Date(t.month + '-01').toLocaleDateString('en-GB', { month: 'short', year: 'numeric' }) + ': ' + t.total; bars.append(b); }
  c.append(bars, el('div', 'dim small', 'Each month, ' + new Date(d.trend[0].month + '-01').toLocaleDateString('en-GB', { month: 'short', year: 'numeric' }) +
    ' to ' + new Date(d.month + '-01').toLocaleDateString('en-GB', { month: 'short', year: 'numeric' })));
  const t = d.neighbourhood;
  if (t && t.url) { const a = el('a', 'small', t.name + ' police team'); a.href = t.url; a.target = '_blank'; a.rel = 'noopener'; c.append(a); }
  c.append(el('div', 'dim small', d.attribution));
}
function show(d) {
  point = { lat: d.lat, lon: d.lon };
  const a = d.area;
  $('area').textContent = (d.place ? d.place.name + ' · ' : '') + (a ? a.postcode + (a.ward ? ', ' + a.ward : '') + (a.district ? ', ' + a.district : '') : d.lat + ', ' + d.lon);
  const pl = $('atplace'); pl.replaceChildren(); pl.hidden = !d.place;
  if (d.place) { pl.append(document.createTextNode('This is ' + d.place.name + ', one of your saved places: ')); const m = el('a', null, 'on the map'); m.href = '/map';
    const ps = el('a', null, 'Places'); ps.href = '/places'; pl.append(m, document.createTextNode(', '), ps, document.createTextNode('.')); }
  $('where').textContent = 'At ' + d.lat + ', ' + d.lon + '.';
  const w = d.what3words, wb = $('w3w'); wb.replaceChildren(); wb.hidden = !w;
  if (w && w.words) wb.append(Object.assign(el('a', null, '///' + w.words), { href: w.map, target: '_blank', rel: 'noopener noreferrer' }),
    el('span', 'dim', w.nearest ? ' near ' + w.nearest : ''));
  else if (w && w.error) wb.append(el('span', 'dim', 'what3words: ' + w.error));
  const box = $('parts'); box.replaceChildren();
  d.parts.filter(p => !p.off && !p.blank).forEach(p => {
    const c = el('div', 'card' + (p.ok ? '' : ' quiet')); c.append(el('h3', null, p.title), el('p', p.ok ? null : 'dim', p.text)); box.append(c);
    if (p.part === 'crime' && p.ok && (p.data.trend || []).length > 1) crimeExtras(c, p.data);
  });
  const off = d.parts.filter(p => p.off).map(p => p.title);
  if (off.length) box.append(el('p', 'dim', 'Switched off: ' + off.join(', ') + '.'));
  $('savecard').hidden = !!d.place; $('again').hidden = false;  // already saved: nothing to keep
}
let last = null;  // how the last look was asked: again repeats it
async function look() {
  last = look;
  $('hmsg').textContent = ''; $('go').disabled = true; $('where').textContent = 'Finding you...';
  let c;
  try { c = await locate(); }
  catch (e) {  // no location: say why, and offer the postcode
    $('hmsg').textContent = e.message + ' Enter a postcode instead.'; $('where').textContent = ''; $('go').disabled = false;
    $('pcode').focus(); return;
  }
  try {
    const p = { lat: Math.round(c.latitude * 1e5) / 1e5, lon: Math.round(c.longitude * 1e5) / 1e5 };
    $('where').textContent = 'Asking every source about ' + p.lat + ', ' + p.lon + ' (accurate to ' + Math.round(c.accuracy) + ' m)...';
    show(await api('GET', '/v1/here?lat=' + p.lat + '&lon=' + p.lon));
  } catch (e) { $('hmsg').textContent = e.message; $('where').textContent = ''; } finally { $('go').disabled = false; }
}
async function lookPostcode() {
  const pc = $('pcode').value.trim();
  if (!pc) { $('pcode').focus(); return; }
  last = lookPostcode;
  const bare = pc.split('///').join(''), words = bare.split('.').length === 3 && !/\\s/.test(bare);  // three words: what3words
  $('hmsg').textContent = ''; $('where').textContent = 'Asking every source about ' + (words ? pc : pc.toUpperCase()) + '...';
  try { show(await api('GET', '/v1/here?' + (words ? 'words=' : 'postcode=') + encodeURIComponent(pc))); }
  catch (e) { $('hmsg').textContent = e.message; $('where').textContent = ''; }
}
$('go').onclick = look; $('again').onclick = () => (last || look)();
$('pc').onsubmit = e => { e.preventDefault(); lookPostcode(); };
$('auth').onsubmit = e => { e.preventDefault(); try { sessionStorage.setItem('wa_token', tok.value); } catch (x) {} msg.textContent = ''; };
$('save').onsubmit = e => { e.preventDefault(); if (!point) return;
  api('POST', '/v1/here/save', { ...point, name: $('pname').value.trim() })
    .then(r => { $('smsg').textContent = 'Saved as ' + (r.name || $('pname').value) + '.'; $('pname').value = ''; })
    .catch(x => { $('smsg').textContent = x.message; }); };
</script></main></body></html>"""

page_router = APIRouter()


@page_router.get("/here", response_class=HTMLResponse, include_in_schema=False)
async def here_page(request: Request) -> str:
    """The page holds no data itself; it asks the browser for a location and calls /v1/here."""
    return themed(PAGE, "here", request)
