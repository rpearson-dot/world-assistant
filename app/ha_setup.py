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
from pydantic import BaseModel
from sqlalchemy import text

from app.auth import require_token
from app.config import get_settings
from app.localnet import is_local
from app.mqtt_config import from_env, load
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
    "packages/wa_energy_plan.yaml": ("Energy plan (battery charging)", (
        "Sets the battery's charge targets from expected use, Forecast.Solar and your battery health max, and charges "
        "from the grid in Free Electricity hours you joined. For a Deye/Sunsynk inverter with Cosy-shaped slots: set "
        "your inverter's entity name above first. Starts as a dry run.")),
    "dashboards/home-charging.yaml": ("Home charging", (
        "Battery, solar, prices, free hours and Saving Sessions, the charge plan and its tuning. "
        "Needs the energy plan package.")),
    "dashboards/fuel.yaml": ("Fuel", "Prices near each person, cheapest first."),
    "dashboards/trains-buses.yaml": ("Trains and buses", "Departure boards and the bus tracker."),
    "dashboards/roads.yaml": ("Roads", "Closures and speed limits near each person, and your routes."),
    "dashboards/events.yaml": ("Events", "What is on near home and your extra places."),
    "dashboards/pollen.yaml": ("Pollen", "The pollen map, your own forecast and the home history."),
    "dashboards/manage.yaml": ("Manage", "Add places and routes."),
    "dashboards/leaf.yaml": ("Leaf", "Nissan Leaf battery and charging (with OVMS)."),
}  # fmt: skip

# For dashboards kept as files (copied over SMB): Home Assistant's YAML-mode dashboards, so they update by themselves.
DASH_ICONS = {"fuel": "mdi:gas-station", "trains-buses": "mdi:bus-clock", "roads": "mdi:road-variant",
              "events": "mdi:calendar-star", "pollen": "mdi:flower-pollen", "manage": "mdi:map-marker-plus",
              "leaf": "mdi:car-electric", "home-charging": "mdi:home-battery"}  # fmt: skip


def lovelace_yaml(dashboards: list[dict[str, Any]], folder: str = "world_assistant/dashboards") -> str:
    lines = ["lovelace:", "  dashboards:"]
    for f in dashboards:
        stem = f["name"].rsplit(".", 1)[0]
        lines += [f"    wa-{stem}:", "      mode: yaml", f"      title: {f['title']}",
                  f"      icon: {DASH_ICONS.get(stem, 'mdi:view-dashboard')}", "      show_in_sidebar: true",
                  f"      filename: {folder}/{f['name']}"]  # fmt: skip
    return "\n".join(lines) + "\n"


# The inverter's entity name: the shipped files say office_inverter (sensor.office_inverter_battery_soc and so on);
# each copy and download says the name saved on the /ha page instead.
INVERTER_KEY = "ha_inverter"
DEFAULT_INVERTER = "office_inverter"
INVERTER_NAME = re.compile(r"^[a-z][a-z0-9_]{0,60}$")


def inverter_name(raw: str) -> str:
    """'office_inverter', or pasted as an entity id ('sensor.office_inverter_battery_soc'): the name, or ValueError."""
    name = raw.strip().lower()
    if "." in name:
        name = name.split(".", 1)[1]
        for tail in ("_battery_soc", "_battery_state_of_charge"):
            name = name.removesuffix(tail)
    if not INVERTER_NAME.match(name):
        raise ValueError("an entity name is lower case letters, digits and _, like office_inverter")
    return name


def render(body: str, inverter: str) -> str:
    return body if inverter == DEFAULT_INVERTER else body.replace(DEFAULT_INVERTER, inverter)


async def saved_inverter(engine: Any) -> str:
    try:
        async with engine.connect() as conn:
            v = (await conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": INVERTER_KEY})).scalar()
    except Exception:  # noqa: BLE001 - no database (or no table yet): the files as shipped
        return DEFAULT_INVERTER
    return v["name"] if isinstance(v, dict) and isinstance(v.get("name"), str) else DEFAULT_INVERTER


async def request_inverter(request: Request) -> str:
    engine = getattr(request.app.state, "engine", None)
    return await saved_inverter(engine) if engine is not None else DEFAULT_INVERTER


class InverterIn(BaseModel):
    name: str


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
                    "about": about, "changed_in": meta.get("changed_in", ""), "inverter": DEFAULT_INVERTER in body,
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
    conf, _ = await load(request.app.state.engine) if hasattr(request.app.state, "engine") else (from_env(s), "env")
    mqtt = home_assistant(s, last, conf)
    mqtt.update({"bundled": conf.bundled, "user": conf.user, "publish_port": s.mqtt_publish_port})
    listed = files()
    return {"version": VERSION, "local": is_local(request, request.method), "mqtt": mqtt, "files": listed,
            "inverter": await request_inverter(request), "inverter_default": DEFAULT_INVERTER,
            "secret_paths": SECRET_PATHS, "lovelace_yaml": lovelace_yaml([f for f in listed if f["kind"] == "dashboard"])}  # fmt: skip


@router.get("/token")
async def token() -> dict[str, str]:
    """The API token, for Home Assistant's secrets.yaml. Only for someone who could already use the API."""
    return {"token": get_settings().api_token}


@router.put("/inverter")
async def put_inverter(body: InverterIn, request: Request) -> dict[str, str]:
    """The inverter's entity name, used in every copy and download of the files that name it (the energy plan)."""
    try:
        name = inverter_name(body.name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    async with request.app.state.engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
            {"k": INVERTER_KEY, "v": json.dumps({"name": name})})  # fmt: skip
    return {"inverter": name}


@router.get("/files/{kind}/{name}", response_class=PlainTextResponse)
async def file(kind: str, name: str, request: Request) -> PlainTextResponse:
    rel = f"{kind}/{name}"
    if rel not in manifest() or not (HA_DIR / rel).is_file():  # only the files we ship: no path tricks
        raise HTTPException(404, "no such file")
    body = render((HA_DIR / rel).read_text(), await request_inverter(request))
    return PlainTextResponse(body, media_type="text/yaml; charset=utf-8",
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
  <div class="row"><label>Inverter entity name (for the energy plan)
    <input id="inv" size="24" autocomplete="off" placeholder="office_inverter"></label>
    <button id="invsave" type="button">Save</button><span id="invmsg" class="dim"></span></div>
  <p class="dim">The part between <code>sensor.</code> and <code>_battery_soc</code> in your inverter's battery sensor (you can
  paste the whole entity id). The energy plan and Home charging files are written with it.</p>
  <p class="dim" id="ver"></p></div>
<details class="card" id="smbcard"><summary>Copy straight into Home Assistant (Samba share)</summary>
  <p class="dim">With the <b>Samba share</b> add-on in Home Assistant, World Assistant can put the files in its config folder
  for you: the ticked packages into <code>packages/</code>, the dashboards into <code>world_assistant/dashboards/</code>
  and, if you like, the missing lines into <code>secrets.yaml</code> (backed up first; existing lines are never changed).
  Files that have not changed are left alone.</p>
  <div class="row">
    <label>Share<input id="smbaddr" size="30" placeholder="\\\\homeassistant.local\\config" autocomplete="off"></label>
    <label>User<input id="smbuser" size="14" autocomplete="off"></label>
    <label>Password<input id="smbpw" type="password" size="14" autocomplete="new-password"></label></div>
  <div class="row" style="margin-top:8px"><button id="smbtest" type="button">Test</button>
    <button id="smbsave" type="button">Save</button><button id="smbforget" type="button">Forget</button>
    <span id="smbmsg" class="dim"></span></div>
  <div class="checks" style="margin-top:10px">
    <label><input id="smbpk" type="checkbox" checked> The packages ticked below</label>
    <label><input id="smbdb" type="checkbox" checked> The dashboards</label>
    <label><input id="smbsec" type="checkbox"> Add missing lines to secrets.yaml (includes the API token)</label></div>
  <div class="row"><button id="smbcopy" class="primary" type="button">Copy to Home Assistant</button><span id="smbcopymsg" class="dim"></span></div>
  <ul id="smbresult" class="dim"></ul>
  <p class="dim">Then Developer tools &gt; YAML &gt; Check configuration, and restart. To have the copied dashboards update
  themselves on every copy, add this to <code>configuration.yaml</code> once (merge it with any <code>lovelace:</code> you
  already have), instead of pasting them into the raw editor:</p>
  <pre class="yaml" id="lovelace"></pre><button id="copylove" type="button">Copy</button>
</details>
<ol class="steps">
<li class="card"><h2>Connect the broker</h2><p id="mq" class="dim">Loading...</p></li>
<li class="card"><h2>Add the packages</h2>
  <p class="dim">In <code>configuration.yaml</code>, turn packages on (once):</p>
  <pre class="yaml">homeassistant:
  packages: !include_dir_named packages</pre>
  <p class="dim">Then put the ones you want in <code>/config/packages/</code>, with the same file names: download them,
  or Copy and paste each into a new file there (in Studio Code Server or the File editor). Each is optional.</p>
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
// navigator.clipboard only exists in a secure context (HTTPS or localhost); elsewhere fall back to a hidden textarea.
function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  const ta = document.createElement('textarea'); ta.value = text; ta.setAttribute('readonly', '');
  ta.style.position = 'fixed'; ta.style.opacity = '0'; document.body.appendChild(ta); ta.select();
  const ok = document.execCommand('copy'); ta.remove();
  return ok ? Promise.resolve() : Promise.reject(new Error('This browser would not copy: use Download'));
}
const texts = {};  // every file's text, fetched with the page, so Copy happens inside the click itself
async function fetchFile(f) {
  if (texts[f.path] != null) return texts[f.path];
  const r = await fetch('/v1/ha/files/' + f.path, { headers: hdr() });
  if (!r.ok) throw new Error(f.name + ': ' + r.status);
  return (texts[f.path] = await r.text());
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
  // Copy as well as Download: for pasting into the raw dashboard editor, or into a new file in Studio Code Server
  const cp = document.createElement('button'); cp.type = 'button'; cp.textContent = 'Copy';
  cp.onclick = async () => {
    try { await copyText(texts[f.path] != null ? texts[f.path] : await fetchFile(f)); cp.textContent = 'Copied'; }
    catch (e) { $('msg').textContent = e.message; }
  };
  act.appendChild(cp);
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
  data.files.forEach(f => fetchFile(f).catch(() => {}));
  $('lovelace').textContent = data.lovelace_yaml;
  $('inv').value = data.inverter;
  smbCall('', 'GET').then(smbShow).catch(() => {});
}
$('invsave').onclick = async () => {
  $('invmsg').textContent = 'Saving...';
  try {
    const r = await fetch('/v1/ha/inverter', { method: 'PUT', headers: Object.assign(hdr(), { 'Content-Type': 'application/json' }),
      body: JSON.stringify({ name: $('inv').value }) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || r.status);
    $('inv').value = d.inverter;
    if (data) data.files.filter(f => f.inverter).forEach(f => { delete texts[f.path]; fetchFile(f).catch(() => {}); });
    $('invmsg').textContent = 'Saved: the energy plan and Home charging files now use ' + d.inverter + '. Copy them again.';
  } catch (e) { $('invmsg').textContent = e.message; }
};
// --- copying over SMB ---------------------------------------------------------------------------------
function smbBody() { return { address: $('smbaddr').value.trim(), user: $('smbuser').value.trim(), password: $('smbpw').value || null }; }
async function smbCall(path, method, body) {
  const r = await fetch('/v1/ha/smb' + path, { method, headers: Object.assign(hdr(), body ? { 'Content-Type': 'application/json' } : {}),
    body: body ? JSON.stringify(body) : undefined });
  let d = null; try { d = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error((d && d.detail) || r.status);
  return d;
}
function smbShow(d) {
  $('smbaddr').value = d.address; $('smbuser').value = d.user; $('smbpw').value = '';
  $('smbpw').placeholder = d.password_set ? 'saved: leave empty' : '';
  if (d.last) {
    $('smbcopymsg').textContent = 'Last copied version ' + d.last.version + ' on ' + new Date(d.last.at).toLocaleString() + '.';
    if (!$('since').value) { $('since').value = d.last.version; }
  }
  if (d.address) $('smbcard').open = true;
}
$('smbtest').onclick = async () => {
  $('smbmsg').textContent = 'Connecting...';
  try { const r = await smbCall('/test', 'POST', smbBody());
    $('smbmsg').textContent = r.ok ? 'Connected.' + (r.configuration_yaml ? ' configuration.yaml is there.' : ' No configuration.yaml here: is this the config share?')
      + (r.packages_folder ? '' : ' (packages/ will be created.)') : 'Could not connect: ' + r.error; }
  catch (e) { $('smbmsg').textContent = e.message; }
};
$('smbsave').onclick = async () => { try { smbShow(await smbCall('', 'PUT', smbBody())); $('smbmsg').textContent = 'Saved.'; } catch (e) { $('smbmsg').textContent = e.message; } };
$('smbforget').onclick = async () => { try { smbShow(await smbCall('', 'DELETE')); $('smbmsg').textContent = 'Forgotten.'; } catch (e) { $('smbmsg').textContent = e.message; } };
$('smbcopy').onclick = async () => {
  $('smbcopymsg').textContent = 'Copying...'; $('smbresult').replaceChildren();
  const packages = $('smbpk').checked ? [...document.querySelectorAll('#pk input[type=checkbox]')].filter(b => b.checked).map(b => b.dataset.path) : [];
  try {
    const r = await smbCall('/copy', 'POST', { packages, dashboards: $('smbdb').checked, secrets: $('smbsec').checked,
      base_url: $('base').value.trim().replace(/\\/+$/, '') });
    $('smbresult').replaceChildren(...r.files.map(f => { const li = document.createElement('li'); li.textContent = f.path + ': ' + f.result; return li; }));
    if (r.secrets_differ && r.secrets_differ.length) { const li = document.createElement('li'); li.className = 'err';
      li.textContent = 'Already in secrets.yaml with a different value (left alone, check them): ' + r.secrets_differ.join(', '); $('smbresult').append(li); }
    $('smbcopymsg').textContent = r.ok ? 'Done. Check the configuration and restart Home Assistant.' : 'Stopped: ' + r.error;
    if (r.ok) { $('since').value = data.version; try { localStorage.setItem('wa_ha_since', data.version); } catch (e) {} load(); }
  } catch (e) { $('smbcopymsg').textContent = e.message; }
};
$('copylove').onclick = () => copyText($('lovelace').textContent).then(() => $('copylove').textContent = 'Copied');

$('go').onclick = load;
$('base').oninput = secrets;
$('since').oninput = () => { try { localStorage.setItem('wa_ha_since', $('since').value.trim()); } catch (e) {} if (data) load(); };
$('copysec').onclick = () => copyText($('sec').textContent).then(() => $('secmsg').textContent = 'Copied.', e => $('secmsg').textContent = e.message);
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
