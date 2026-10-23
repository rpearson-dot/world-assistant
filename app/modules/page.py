"""The Modules page: pick what World Assistant does, see what each needs, test a key.

The page holds no data; everything comes from /v1/modules. Choices apply within seconds, with no restart.
"""

from __future__ import annotations

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Modules</title>
<style>
/*BASE_CSS*/
  .mod { display: grid; grid-template-columns: auto 1fr auto; gap: 8px 12px; align-items: start; padding: 10px 0; border-top: 1px solid var(--line); }
  .mod:first-of-type { border-top: 0; }
  .mod h3 { margin: 0; font-size: 15px; } .mod p { margin: 2px 0 0; font-size: 13px; color: var(--dim); }
  .mod .why { color: var(--ink); } .tag { font-size: 11px; padding: 1px 7px; border-radius: 999px; border: 1px solid var(--line); color: var(--dim); }
  .tag.ok { color: #1a7f4b; border-color: #1a7f4b; } .tag.bad { color: #d92d20; border-color: #d92d20; } .tag.warn { color: #b54708; border-color: #b54708; }
  .actions { display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; }
  .switch { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; }
  .row2 { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0; }
  .choices { display: grid; gap: 6px; margin-top: 6px; } .choices div { font-size: 13px; }
  .choices b { display: inline-block; min-width: 9em; }
</style></head><body><main>
<!--NAV-->
<h1>Modules</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary">Load</button></div>
<p id="msg" class="err"></p>
<div id="ui" hidden>
  <div class="card"><h2>What should World Assistant do?</h2>
    <p class="dim" id="intro"></p>
    <div class="row2"><button id="essentials" type="button">Start with the essentials only</button>
    <button id="everything" type="button">Turn everything on</button></div></div>
  <div id="groups"></div>
  <div class="card"><h2>Where your keys are kept</h2><p id="secretsnow" class="dim"></p><div class="choices" id="choices"></div></div>
</div>
<script>
const $ = id => document.getElementById(id), tok = $('t');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
const hdr = j => Object.assign({ Authorization: 'Bearer ' + tok.value }, j ? { 'Content-Type': 'application/json' } : {});
async function api(path, opts) {
  const r = await fetch(path, Object.assign({ headers: hdr(opts && opts.body) }, opts || {}));
  let d = null; try { d = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error((d && d.detail) || r.status);
  return d;
}
const CATS = { transport: 'Getting around', place: 'Places and what is near them', energy: 'Energy and vehicles', data: 'Other data', service: 'Services' };
const STATE = { ready: ['ready', 'ok'], limited: ['works, better with a key', 'warn'], waiting: ['set up, no run yet', ''], off: ['poll switched off', 'warn'], missing: ['needs a key', 'bad'], failing: ['last run failed', 'bad'] };
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function render(d) {
  $('intro').textContent = d.chosen ? 'You have chosen which modules are on. Changes apply within seconds.'
    : 'Nothing has been chosen yet, so every module is on. Choose, or start with the essentials and add what you want.';
  const groups = $('groups'); groups.replaceChildren();
  for (const cat of Object.keys(CATS)) {
    const mods = d.modules.filter(m => m.category === cat && !m.core); if (!mods.length) continue;
    const card = el('div', 'card'); card.append(el('h2', null, CATS[cat]));
    for (const m of mods) {
      const row = el('div', 'mod'), sw = el('label', 'switch'), cb = document.createElement('input');
      cb.type = 'checkbox'; cb.checked = m.enabled; cb.onchange = () => flip(m.id, cb.checked, cb);
      sw.append(cb); row.append(sw);
      const body = el('div'), h = el('h3', null, m.name + ' '); const st = STATE[m.state] || [m.state, ''];
      h.append(el('span', 'tag ' + st[1], m.enabled ? st[0] : 'off'));
      if (m.suggested) h.append(' ', el('span', 'tag ok', 'suggested'));
      if (m.source !== 'builtin') h.append(' ', el('span', 'tag', 'from ' + m.source));
      body.append(h, el('p', null, m.unlocks));
      if (m.suggested) body.append(el('p', 'why', 'Suggested: ' + m.suggested + '.'));
      if (m.missing_keys.length && !m.key_optional) body.append(el('p', null, 'Needs ' + (m.any_key ? 'one of ' : '') + m.missing_keys.join(', ') + ' (Settings > Keys).'));
      if (m.services.length) body.append(el('p', null, 'Optional container: ' + m.services.join(', ') + '.'));
      if (m.note) body.append(el('p', null, m.note));
      if (m.error) body.append(el('p', 'err', m.error));
      row.append(body);
      const act = el('div', 'actions');
      if (m.testable) { const b = el('button', null, 'Test key'); b.type = 'button'; b.onclick = () => testKey(m.id, b); act.append(b); }
      if (m.link) { const a = el('a', null, 'Get a key'); a.href = m.link; a.target = '_blank'; a.rel = 'noopener'; act.append(a); }
      row.append(act); card.append(row);
    }
    groups.append(card);
  }
  $('secretsnow').textContent = 'Now: ' + d.secrets.backend + '. Changing it is a setting in .env and a restart; each choice below says how.';
  $('choices').replaceChildren(...d.secrets.choices.map(c => { const r = el('div'); r.append(el('b', null, c.name + (c.id === d.secrets.backend ? ' (in use)' : '')), c.how); return r; }));
}
async function load() {
  $('msg').textContent = '';
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  try { render(await api('/v1/modules')); $('ui').hidden = false; } catch (e) { $('msg').textContent = e.message; }
}
async function flip(id, on, cb) {
  try { render(await api('/v1/modules/' + id + (on ? '/enable' : '/disable'), { method: 'POST' })); }
  catch (e) { $('msg').textContent = e.message; cb.checked = !on; }
}
async function testKey(id, b) {
  b.disabled = true; b.textContent = 'Testing...';
  try { const r = await api('/v1/modules/' + id + '/test', { method: 'POST' }); b.textContent = (r.ok === true ? 'OK: ' : r.ok === false ? 'Failed: ' : '') + r.message; }
  catch (e) { b.textContent = e.message; }
  b.disabled = false;
}
$('essentials').onclick = async () => { try { render(await api('/v1/modules', { method: 'PUT', body: JSON.stringify({ mode: 'only', on: [] }) })); } catch (e) { $('msg').textContent = e.message; } };
$('everything').onclick = async () => { try { render(await api('/v1/modules', { method: 'DELETE' })); } catch (e) { $('msg').textContent = e.message; } };
$('go').onclick = load;
if (tok.value) load();
</script></main></body></html>"""
