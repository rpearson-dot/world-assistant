"""Setting up Home Assistant from a page: the broker, the packages, secrets.yaml and the dashboards.

Everything comes from the running version, so what you copy always matches what this server publishes.
homeassistant/manifest.json says which release last changed each file, so the page can say what to copy
again after an update.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.auth import require_token
from app.config import get_settings
from app.localnet import is_local
from app.pagestyle import themed
from app.setup_checklist import home_assistant
from app.version import VERSION

router = APIRouter(prefix="/v1/ha", tags=["home assistant"], dependencies=[Depends(require_token)])
page_router = APIRouter()

HA_DIR = Path(__file__).resolve().parent.parent / "homeassistant"

# What each file is for. Files not listed here still appear, with their name.
ABOUT: dict[str, tuple[str, str]] = {
    "packages/world_assistant_events.yaml": ("Events", ("What is on near your home zone, and extra places to watch. "
                                             "Needed by the Events and Manage dashboards.")),
    "packages/world_assistant_pollen.yaml": ("Pollen at home", ("Your home zone's pollen as sensors with history, "
                                             "for the Pollen dashboard's graph.")),
    "packages/world_assistant_fuel.yaml": ("Fuel type", "The Petrol / Diesel dropdown on the Fuel dashboard."),
    "packages/world_assistant_manage.yaml": ("Manage places and routes", ("Add places and routes from Home Assistant. "
                                             "Needs the events package too.")),
    "packages/world_assistant_bus.yaml": ("Bus tracker times", "Change the bus tracker's times from the dashboard."),
    "dashboards/fuel.yaml": ("Fuel", "Prices near each person, cheapest first."),
    "dashboards/trains-buses.yaml": ("Trains and buses", "Departure boards and the bus tracker."),
    "dashboards/roads.yaml": ("Roads", "Closures and speed limits near each person, and your routes."),
    "dashboards/events.yaml": ("Events", "What is on near home and your extra places."),
    "dashboards/pollen.yaml": ("Pollen", "The pollen map, your own forecast and the home history."),
    "dashboards/manage.yaml": ("Manage", "Add places and routes."),
    "dashboards/leaf.yaml": ("Leaf", "Nissan Leaf battery and charging (with OVMS)."),
}  # fmt: skip

# The API path behind each secret the packages use.
SECRET_PATHS: dict[str, str] = {
    "wa_events_url": "/v1/events/near",
    "wa_locations_url": "/v1/ha/locations",
    "wa_locations_delete_url": "/v1/ha/locations/delete",
    "wa_routes_url": "/v1/ha/routes",
    "wa_routes_delete_url": "/v1/ha/routes/delete",
    "wa_bus_settings_url": "/v1/bus/settings",
}
SECRET = re.compile(r"!secret\s+([a-z0-9_]+)")


@cache
def manifest() -> dict[str, dict[str, str]]:
    try:
        return json.loads((HA_DIR / "manifest.json").read_text())["files"]
    except (OSError, ValueError, KeyError):
        return {}


def files() -> list[dict[str, Any]]:
    out = []
    for rel, meta in sorted(manifest().items()):
        path = HA_DIR / rel
        if not path.is_file():
            continue
        body = path.read_text()
        title, about = ABOUT.get(rel, (path.stem, ""))
        out.append({"path": rel, "kind": rel.split("/", 1)[0].rstrip("s"), "name": path.name, "title": title,
                    "about": about, "changed_in": meta.get("changed_in", ""),
                    "secrets": sorted(set(SECRET.findall(body)))})  # fmt: skip
    return out


@router.get("/setup")
async def setup(request: Request) -> dict[str, Any]:
    """The files for this version, the secrets their packages use, and how publishing to the broker is going."""
    s = get_settings()
    try:
        raw = await request.app.state.redis.get("wa:ha:last")
        last = json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 - the page must still show the files
        last = None
    mqtt = home_assistant(s, last)
    mqtt.update({"bundled": s.mqtt_host == "wa-mqtt", "user": s.mqtt_user, "publish_port": s.mqtt_publish_port})
    return {"version": VERSION, "local": is_local(request, request.method), "mqtt": mqtt, "files": files(),
            "secret_paths": SECRET_PATHS}  # fmt: skip


@router.get("/token")
async def token() -> dict[str, str]:
    """The API token, for Home Assistant's secrets.yaml. Only for someone who could already use the API."""
    return {"token": get_settings().api_token}


@router.get("/files/{kind}/{name}", response_class=PlainTextResponse)
async def file(kind: str, name: str) -> PlainTextResponse:
    rel = f"{kind}/{name}"
    if rel not in manifest() or not (HA_DIR / rel).is_file():  # only the files we ship: no path tricks
        raise HTTPException(404, "no such file")
    return PlainTextResponse((HA_DIR / rel).read_text(), media_type="text/yaml; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})  # fmt: skip


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Home Assistant</title>
<style>/*BASE_CSS*/
  pre.yaml { background: var(--chip); border-radius: 10px; padding: 10px 12px; overflow-x: auto; font-size: 13px; }
  ol.steps > li { margin-bottom: 18px; } ol.steps h2 { margin: 0 0 6px; font-size: 17px; }
  td .badge { white-space: nowrap; }
</style></head><body><main>
<!--NAV-->
<h1>Home Assistant</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary">Load</button></div>
<p id="msg" class="err"></p>
<div class="card"><div class="row">
  <label>Address Home Assistant uses for this server <input id="base" size="32"></label>
  <label>Files last copied from version <input id="since" size="8" placeholder="e.g. 0.12.0"></label></div>
  <p class="dim" id="ver"></p></div>
<ol class="steps">
<li class="card"><h2>Connect the broker</h2><p id="mq" class="dim">Loading...</p></li>
<li class="card"><h2>Add the packages</h2>
  <p class="dim">In <code>configuration.yaml</code>, turn packages on (once):</p>
  <pre class="yaml">homeassistant:
  packages: !include_dir_named packages</pre>
  <p class="dim">Then put the ones you want in <code>/config/packages/</code>. Each is optional.</p>
  <div class="scroll"><table id="pk"></table></div></li>
<li class="card"><h2>Add these lines to <code>/config/secrets.yaml</code></h2>
  <p class="dim">For the packages ticked above. Home Assistant will not start if a package names a secret that is
  missing, so add every line shown.</p>
  <pre class="yaml" id="sec"></pre>
  <div class="row"><button id="copysec" type="button">Copy</button>
  <button id="showtok" type="button">Put the token in</button>
  <span id="secmsg" class="dim"></span></div></li>
<li class="card"><h2>Check and restart</h2>
  <p class="dim">Developer tools &gt; YAML &gt; Check configuration, then Restart.</p></li>
<li class="card"><h2>Add the dashboards</h2>
  <p class="dim">Settings &gt; Dashboards &gt; Add dashboard &gt; New dashboard from scratch. Open it, then the
  pencil &gt; three dots &gt; Raw configuration editor, replace everything with the copied file, and Save.</p>
  <div class="scroll"><table id="db"></table></div></li>
</ol>
<script>
const $ = id => document.getElementById(id), tok = $('t');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
const hdr = () => ({ Authorization: 'Bearer ' + tok.value });
let data = null, token = '';
try { $('since').value = localStorage.getItem('wa_ha_since') || ''; } catch (e) {}
$('base').value = location.origin;
function newer(a, b) {  // is version a later than b?
  const p = v => (v || '0').split('.').map(n => parseInt(n, 10) || 0);
  const x = p(a), y = p(b);
  for (let i = 0; i < 3; i++) { if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0); }
  return false;
}
function badge(f) {
  const since = $('since').value.trim(), b = document.createElement('span');
  if (since && newer(f.changed_in, since)) { b.className = 'badge warn'; b.textContent = 'Changed in ' + f.changed_in + ': copy again'; }
  else { b.className = 'badge'; b.textContent = 'Last changed in ' + f.changed_in; }
  return b;
}
async function fetchFile(f) {
  const r = await fetch('/v1/ha/files/' + f.path, { headers: hdr() });
  if (!r.ok) throw new Error(f.name + ': ' + r.status);
  return r.text();
}
async function download(f) {
  const url = URL.createObjectURL(new Blob([await fetchFile(f)], { type: 'text/yaml' })), a = document.createElement('a');
  a.href = url; a.download = f.name; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
}
function row(f, pick) {
  const tr = document.createElement('tr');
  if (pick) {
    const c = tr.insertCell(), box = document.createElement('input');
    box.type = 'checkbox'; box.checked = true; box.style.width = 'auto'; box.dataset.path = f.path; box.onchange = secrets;
    c.appendChild(box);
  }
  const n = tr.insertCell(); n.innerHTML = '<b></b><br><span class="dim"></span>';
  n.querySelector('b').textContent = f.title; n.querySelector('span').textContent = f.name + (f.about ? ': ' + f.about : '');
  tr.insertCell().appendChild(badge(f));
  const act = tr.insertCell(), dl = document.createElement('button');
  dl.type = 'button'; dl.textContent = 'Download'; dl.onclick = () => download(f).catch(e => $('msg').textContent = e.message);
  act.appendChild(dl);
  if (!pick) {
    const cp = document.createElement('button'); cp.type = 'button'; cp.textContent = 'Copy';
    cp.onclick = async () => { try { await navigator.clipboard.writeText(await fetchFile(f)); cp.textContent = 'Copied'; }
      catch (e) { $('msg').textContent = e.message; } };
    act.appendChild(cp);
  }
  return tr;
}
function secrets() {
  if (!data) return;
  const base = $('base').value.trim().replace(/\\/+$/, ''), picked = new Set();
  document.querySelectorAll('#pk input[type=checkbox]').forEach(b => { if (b.checked) picked.add(b.dataset.path); });
  const names = new Set();
  data.files.filter(f => picked.has(f.path)).forEach(f => f.secrets.forEach(s => names.add(s)));
  const lines = [...names].filter(n => n !== 'wa_authorization').sort().map(n =>
    n + ': "' + base + (data.secret_paths[n] || '/') + '"');
  if (names.has('wa_authorization')) lines.push('wa_authorization: "Bearer ' + (token || '<contents of api_token.txt>') + '"');
  $('sec').textContent = lines.length ? lines.join('\\n') : '# none of the ticked packages needs a secret';
}
function broker(m) {
  const where = location.hostname;
  if (m.state === 'off') return 'Not publishing: no broker is set. Run scripts/setup.sh on the server, or set WA_MQTT_HOST '
    + 'to the broker Home Assistant uses (or add the bundled one, docker-compose.mqtt.yml).';
  let s = m.bundled
    ? 'This server runs the broker. In Home Assistant: Settings > Devices & services > Add integration > MQTT, broker '
      + where + ', port ' + m.publish_port + ', user ' + (m.user || 'wa') + ', password from mqtt_password.txt in the secrets folder.'
    : 'Publishing to ' + m.broker + '. Home Assistant\\'s MQTT integration must use the same broker.';
  if (m.state === 'ready') s += ' Last published ' + new Date(m.last_ok).toLocaleString() + ' (' + m.note + ').';
  else if (m.state === 'failing') s += ' The last attempt failed: ' + m.error;
  else s += ' Waiting for the first publish (every few minutes).';
  return s + ' Sensors appear by themselves; there is nothing else to install.';
}
async function load() {
  $('msg').textContent = '';
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  const r = await fetch('/v1/ha/setup', { headers: hdr() });
  if (!r.ok) { $('msg').textContent = 'Request failed: ' + r.status; return; }
  data = await r.json();
  $('ver').textContent = 'These files are from version ' + data.version + '. Copy again any marked as changed since your last copy.';
  $('mq').textContent = broker(data.mqtt);
  $('pk').replaceChildren(...data.files.filter(f => f.kind === 'package').map(f => row(f, true)));
  $('db').replaceChildren(...data.files.filter(f => f.kind === 'dashboard').map(f => row(f, false)));
  secrets();
}
$('go').onclick = load;
$('base').oninput = secrets;
$('since').oninput = () => { try { localStorage.setItem('wa_ha_since', $('since').value.trim()); } catch (e) {} if (data) load(); };
$('copysec').onclick = () => navigator.clipboard.writeText($('sec').textContent).then(() => $('secmsg').textContent = 'Copied.');
$('showtok').onclick = async () => {
  const r = await fetch('/v1/ha/token', { headers: hdr() });
  if (!r.ok) { $('secmsg').textContent = 'Could not get the token: ' + r.status; return; }
  token = (await r.json()).token; secrets(); $('secmsg').textContent = 'Token added. Keep secrets.yaml private.';
};
if (tok.value) load();
</script></main></body></html>"""


@page_router.get("/ha", response_class=HTMLResponse, include_in_schema=False)
async def ha_page(request: Request) -> str:
    """The page holds no data; it calls /v1/ha/setup."""
    return themed(PAGE, "ha", request)
