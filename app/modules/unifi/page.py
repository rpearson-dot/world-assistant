"""The Ubiquiti page (/unifi, In the home → Networking → Ubiquiti): the UniFi gateway's firewall picture — which
devices reach the most outside hosts, what they talk to, and the firewall rule you could add. Advisory only: World
Assistant never changes your firewall. Holds no data; it reads /v1/network and builds nothing from it but counts and
the suggestions /v1/network/.../firewall returns."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pagestyle import themed

page_router = APIRouter()

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant Ubiquiti</title>
<style>
/*BASE_CSS*/
.wrap { overflow-x: auto; }
table.t { width: 100%; border-collapse: collapse; font-size: .9em; } table.t td, table.t th { padding: 5px 6px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
table.t td.n, table.t th.n { text-align: right; } table.t tr.dev { cursor: pointer; } table.t tr.dev:hover td { background: var(--chip); }
.chip { display: inline-block; background: var(--chip); border-radius: 999px; padding: 1px 8px; margin: 1px; font-size: .85em; } .chip.loud { border: 1px solid var(--bad); color: var(--bad); font-weight: 600; }
.why { color: var(--dim); font-size: .85em; }
.sg { border: 1px solid var(--line); border-radius: 10px; padding: 8px 10px; margin: 6px 0; }
.sg code { background: var(--chip); border-radius: 6px; padding: 1px 6px; }
.hint { border: 1px solid var(--accent); border-radius: 10px; padding: 10px 12px; margin: 8px 0; }
.ev { font-size: .85em; border-bottom: 1px solid var(--line); padding: 3px 0; } .ev b { font-weight: 600; } .ev.block b { color: var(--bad); }
</style></head><body><main>
<!--NAV-->
<h1>Ubiquiti</h1>
<p class="dim">From the UniFi gateway's log: which devices reach the most outside hosts and the firewall rule to add. Advisory only — World Assistant never changes your firewall.</p>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary" type="button">Load</button></div>
<p id="msg" class="err"></p>
<div class="card"><h2>Devices by firewall activity</h2><p class="dim small" id="say"></p><div class="wrap"><table class="t" id="devs"></table></div></div>
<div class="card" id="one" hidden>
  <h2 id="oname"></h2><p class="dim small" id="oinfo"></p>
  <p id="sum" class="small"></p>
  <div id="hint"></div>
  <h3>Tracker domains it resolves</h3><p class="dim small" id="trksay"></p><div id="trk"></div>
  <h3>Suggested firewall blocks</h3><p class="dim small" id="ssay"></p><div id="sugg"></div>
  <h3>Recent firewall and joins (24 hours)</h3><div id="evs"></div></div>
<details class="card"><summary>Settings</summary>
  <p class="dim small">The UniFi gateway sends its log to this server on <b id="port"></b>: Settings → CyberSecure → Traffic Logging,
  and Settings → Control Plane → Integrations → Activity Logging (Syslog) → SIEM Server, with this server's address and that port.
  Turn on Syslog Logging for the firewall rules you want to see (in the Policy Engine). Nothing goes to Home Assistant.</p>
</details>
<script>
const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
async function api(path) {
  const r = await fetch(path, { headers: { Authorization: 'Bearer ' + tok.value } });
  if (!r.ok) { let d = r.status; try { d = (await r.json()).detail; } catch (e) {} throw new Error(typeof d === 'string' ? d : JSON.stringify(d)); }
  return r.json();
}
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const when = s => s ? new Date(s).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '–';
let loud = 0;
function deviceRow(d) {
  const tr = el('tr', null, 'dev'); tr.onclick = () => open(d.mac);
  const n = el('td'); n.append(el('div', d.label)); n.append(el('div', d.mac + (d.ip ? ' · ' + d.ip : ''), 'why'));
  if (loud && d.firewall_blocks >= loud) n.append(el('span', 'talks a lot', 'chip loud'));
  tr.append(n, el('td', String(d.firewall_blocks), 'n'), el('td', String(d.lookups), 'n'));
  return tr;
}
async function load() {
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  msg.textContent = '';
  try {
    try { const st = await api('/v1/network/settings'); $('port').textContent = 'UDP ' + st.syslog_port; } catch (e) {}
    const d = await api('/v1/network/devices'), t = $('devs'); t.replaceChildren();
    const devs = [...d.devices].sort((a, b) => (b.firewall_blocks - a.firewall_blocks) || (b.lookups - a.lookups));
    const top = devs.slice(0, 3).reduce((m, x) => Math.max(m, x.firewall_blocks), 0); loud = top >= 50 ? Math.max(50, top * 0.6) : 0;
    const h = el('tr'); [['Device'], ['Firewall blocks today', 'n'], ['Lookups today', 'n']].forEach(([x, c]) => h.append(el('th', x, c))); t.append(h);
    devs.forEach(x => t.append(deviceRow(x)));
    $('say').textContent = devs.length ? devs.length + ' devices. Choose one to see what it reaches and the rule to add.' :
      'No devices yet: point the UniFi gateway\'s log at this server (Settings, below).';
  } catch (e) { msg.textContent = e.message; }
}
async function open(mac) {
  $('one').hidden = false;
  const [det, fw] = await Promise.all([api('/v1/network/devices/' + encodeURIComponent(mac)), api('/v1/network/devices/' + encodeURIComponent(mac) + '/firewall')]);
  $('oname').textContent = det.device.label; $('oinfo').textContent = mac + (det.device.ip ? ' · ' + det.device.ip : '') + (det.device.connection ? ' · ' + det.device.connection : '');
  const s = fw.summary;
  $('sum').textContent = s.distinct_dests + ' outside hosts over ' + fw.days + ' days · about ' + s.per_day + ' connections a day · '
    + (s.ports.length ? 'ports ' + s.ports.slice(0, 12).join(', ') : 'no ports logged') + ' · ' + s.blocks_already + ' already blocked'
    + (s.chatty ? ' · talks a lot' : '');
  const hint = $('hint'); hint.replaceChildren();
  if (fw.rule_hint) { const b = el('div', null, 'hint'); b.append(el('b', 'Suggested rule: '), document.createTextNode(fw.rule_hint)); hint.append(b); }
  const trk = $('trk'); trk.replaceChildren();
  $('trksay').textContent = fw.tracker_domains.length ? 'It looks up these tracker-like domains (block them under Pi-hole):' : 'None of the domains it resolves look like trackers.';
  fw.tracker_domains.forEach(x => trk.append(el('span', x, 'chip')));
  const sg = $('sugg'); sg.replaceChildren();
  $('ssay').textContent = fw.suggestions.length ? 'Busiest outside connections. Add a block for any of these in UniFi:' : 'Nothing stands out: its outside connections look like what it needs.';
  fw.suggestions.forEach(x => { const b = el('div', null, 'sg'); const r = el('div'); r.append(el('code', x.rule)); b.append(r);
    const meta = [x.count + ' connections'].concat(x.why || []); b.append(el('div', meta.join(' · '), 'why'));
    const cp = el('button', 'Copy rule'); cp.type = 'button'; cp.onclick = async () => { try { await navigator.clipboard.writeText(x.rule); cp.textContent = 'Copied'; } catch (e) {} }; b.append(cp); sg.append(b); });
  const ev = $('evs'); ev.replaceChildren();
  det.events.filter(x => x.kind === 'firewall' || x.action === 'join' || x.action === 'leave' || x.action === 'lease').slice(0, 200).forEach(x => {
    const r = el('div', null, 'ev' + (x.action === 'block' ? ' block' : '')); r.append(el('span', when(x.at) + ' '), el('b', x.kind + ' ' + (x.action || '') + ' '),
      document.createTextNode((x.dest || '') + (x.port ? ':' + x.port : '') + (x.proto ? ' ' + x.proto : '') + (x.detail && x.detail.rule ? ' [' + x.detail.rule + ']' : ''))); ev.append(r); });
}
$('go').onclick = load;
load();
</script></main></body></html>"""


@page_router.get("/unifi", response_class=HTMLResponse, include_in_schema=False)
async def unifi_page(request: Request) -> str:
    return themed(PAGE, "unifi", request)
