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

from app import ha_names as N
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
        "from the grid in Free Electricity hours you joined, for any tariff, on a Deye/Sunsynk-style inverter: check "
        "the inverter and Forecast.Solar names above first. Starts as a dry run.")),
    "packages/wa_ev_plan.yaml": ("Car charging plan", (
        "Charges the car only while the rate is cheap or in Free Electricity hours you joined, and stops at your "
        "target, through an OCPP charger and the car's charge level (OVMS or any integration). Set your charger and car "
        "entities on the Home charging dashboard. Starts as a dry run.")),
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


# The names the files are written with (app.ha_names): saved on the /ha page or found by "Find my setup".
NAMES_KEY = "ha_names"
LEGACY_INVERTER_KEY = "ha_inverter"  # 0.19.1-0.20: the inverter's name alone
DEFAULT_INVERTER = N.DEFAULTS["inverter"]


def inverter_name(raw: str) -> str:
    """'office_inverter', or pasted as an entity id ('sensor.office_inverter_battery_soc'): the name, or ValueError."""
    return N.clean("inverter", raw)


def render(body: str, names: dict[str, str] | str) -> str:
    """A shipped file written with these names (a plain string: the inverter's name alone, as before 0.20.1)."""
    return N.render(body, {"inverter": names} if isinstance(names, str) else names)


async def _setting(engine: Any, key: str) -> Any:
    async with engine.connect() as conn:
        return (await conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": key})).scalar()


async def saved_names(engine: Any) -> dict[str, str]:
    """Every name, saved or default. The inverter's name saved by an earlier version still counts."""
    try:
        saved = await _setting(engine, NAMES_KEY)
        legacy = await _setting(engine, LEGACY_INVERTER_KEY)
    except Exception:  # noqa: BLE001 - no database (or no table yet): the files as shipped
        return dict(N.DEFAULTS)
    names = dict(saved) if isinstance(saved, dict) else {}
    if "inverter" not in names and isinstance(legacy, dict) and isinstance(legacy.get("name"), str):
        names["inverter"] = legacy["name"]
    return N.merged(names)


async def save_names(engine: Any, names: dict[str, str]) -> dict[str, str]:
    current = await saved_names(engine)
    current.update(names)
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
            {"k": NAMES_KEY, "v": json.dumps(current)})  # fmt: skip
    return current


async def request_names(request: Request) -> dict[str, str]:
    engine = getattr(request.app.state, "engine", None)
    return await saved_names(engine) if engine is not None else dict(N.DEFAULTS)


async def saved_inverter(engine: Any) -> str:  # kept for callers from 0.20
    return (await saved_names(engine))["inverter"]


async def request_inverter(request: Request) -> str:
    return (await request_names(request))["inverter"]


class InverterIn(BaseModel):
    name: str


class NamesIn(BaseModel):
    names: dict[str, str]


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
                    "about": about, "changed_in": meta.get("changed_in", ""), "uses": N.uses(body),
                    "inverter": "inverter" in N.uses(body),
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
            "names": (names := await request_names(request)), "inverter": names["inverter"], "inverter_default": DEFAULT_INVERTER,
            "name_fields": [{"key": n.key, "label": n.label, "default": n.default, "hint": n.hint} for n in N.NAMES],
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
    await save_names(request.app.state.engine, {"inverter": name})
    return {"inverter": name}


@router.put("/names")
async def put_names(body: NamesIn, request: Request) -> dict[str, Any]:
    """Names to write the files with (any subset; an empty value puts the default back). Used in every copy and
    download of the files that name them."""
    clean: dict[str, str] = {}
    try:
        for k, v in body.names.items():
            clean[k] = N.BY_KEY[k].default if not v.strip() and k in N.BY_KEY else N.clean(k, v)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"names": await save_names(request.app.state.engine, clean)}


@router.get("/files/{kind}/{name}", response_class=PlainTextResponse)
async def file(kind: str, name: str, request: Request) -> PlainTextResponse:
    rel = f"{kind}/{name}"
    if rel not in manifest() or not (HA_DIR / rel).is_file():  # only the files we ship: no path tricks
        raise HTTPException(404, "no such file")
    body = render((HA_DIR / rel).read_text(), await request_names(request))
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
  <p class="dim" id="ver"></p></div>
<div class="card"><h2>Find my setup</h2>
  <p class="dim">With the Samba share saved (below), World Assistant reads Home Assistant's own records: its version,
  home, integrations, entities, zones and people, and which World Assistant files are installed. It finds the names
  your files need, and checks that World Assistant's sensors, the MQTT broker and the installed files match. Nothing
  in Home Assistant is changed, and no password or key is kept.</p>
  <div class="row"><button id="disc" class="primary" type="button">Find my setup</button><span id="discmsg" class="dim"></span></div>
  <ul id="checks" class="checks-list"></ul>
  <details id="discmore"><summary>What was found</summary><div id="found" class="dim"></div></details>
  <div id="zonebox" hidden><h3>Zones and places</h3>
    <div id="zin"></div><div id="zout"></div><span id="zmsg" class="dim"></span></div>
  <details id="apicard"><summary>Live checks (optional): Home Assistant's address and a long-lived token</summary>
    <p class="dim">With a token (Home Assistant: your profile &gt; Security &gt; Long-lived access tokens), Find my setup
    also checks that each named entity is reporting and that World Assistant's sensors are live. The token is kept in
    World Assistant's keys folder and never shown again.</p>
    <div class="row"><label>Address<input id="haurl" size="28" placeholder="e.g. 10.0.2.107:8123" autocomplete="off"></label>
      <label>Token<input id="hatok" type="password" size="24" autocomplete="new-password"></label></div>
    <div class="row" style="margin-top:8px"><button id="apisave" type="button">Save and test</button>
      <button id="apiforget" type="button">Forget</button><span id="apimsg" class="dim"></span></div></details></div>
<div class="card"><h2>Names in your files</h2>
  <p class="dim">Your files are written with these names: every download and copy uses them. "Find my setup" fills in
  what it finds; check them and Save. An empty field puts the default back.</p>
  <div class="scroll"><table id="names"></table></div>
  <div class="row"><button id="nameuse" type="button" hidden>Use what was found</button>
    <button id="namesave" type="button">Save names</button><span id="namemsg" class="dim"></span></div></div>
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
    <label><input id="smblove" type="checkbox" checked> Put the dashboards in Home Assistant's sidebar, updating on every
      copy (adds them to configuration.yaml, backed up first; restart once)</label>
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
  const at = installed[f.path];
  if (at) {
    b.className = at === 'current' ? 'badge' : 'badge warn';
    b.textContent = at === 'current' ? 'Installed, current' : at === 'different' ? 'Installed, different: copy again' : 'Not installed';
    return b;
  }
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
  renderNames(null);
  smbCall('', 'GET').then(smbShow).catch(() => {});
  apiCall('GET').then(apiShow).catch(() => {});
  fetch('/v1/ha/inventory', { headers: hdr() }).then(r => r.ok ? r.json() : {}).then(inv => { if (inv && inv.checks) showInventory(inv, false); }).catch(() => {});
}
// --- names and "Find my setup" ----------------------------------------------------------------------------
let found = null, installed = {};
function renderNames(names) {
  if (!data) return;
  const rows = data.name_fields.map(n => {
    const tr = document.createElement('tr'), c1 = tr.insertCell(), c2 = tr.insertCell(), c3 = tr.insertCell();
    c1.innerHTML = '<b></b><br><span class="dim"></span>';
    c1.querySelector('b').textContent = n.label; c1.querySelector('span').textContent = n.hint;
    const box = document.createElement('input'); box.size = 30; box.dataset.key = n.key; box.placeholder = n.default;
    box.value = (names && names[n.key]) || data.names[n.key] || '';
    c2.appendChild(box);
    const f = found && found[n.key];
    if (f && f.candidates && f.candidates.length > 1) {
      const sel = document.createElement('select');
      f.candidates.forEach(v => { const o = document.createElement('option'); o.value = o.textContent = v; sel.appendChild(o); });
      sel.value = box.value; sel.onchange = () => { box.value = sel.value; };
      c2.appendChild(sel);
    }
    const b = document.createElement('span'); b.className = 'badge';
    if (f && f.found && f.found !== data.names[n.key]) { b.className = 'badge warn'; b.textContent = 'Found ' + f.found + ': Save to use it'; }
    else if (f && f.found) { b.textContent = 'Found in Home Assistant'; }
    else if (f) { b.textContent = 'Not found in Home Assistant'; }
    c3.appendChild(b);
    return tr;
  });
  $('names').replaceChildren(...rows);
  $('nameuse').hidden = !(found && Object.values(found).some(f => f.found && f.found !== data.names[Object.keys(found).find(k => found[k] === f)]));
}
$('nameuse').onclick = () => {
  document.querySelectorAll('#names input').forEach(b => { const f = found && found[b.dataset.key]; if (f && f.found) b.value = f.found; });
  $('namemsg').textContent = 'Filled in: check them, then Save names.';
};
$('namesave').onclick = async () => {
  $('namemsg').textContent = 'Saving...';
  const names = {};
  document.querySelectorAll('#names input').forEach(b => { names[b.dataset.key] = b.value.trim(); });
  try {
    const r = await fetch('/v1/ha/names', { method: 'PUT', headers: Object.assign(hdr(), { 'Content-Type': 'application/json' }),
      body: JSON.stringify({ names }) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || r.status);
    data.names = d.names; data.inverter = d.names.inverter;
    data.files.filter(f => f.uses && f.uses.length).forEach(f => { delete texts[f.path]; fetchFile(f).catch(() => {}); });
    renderNames(null);
    $('namemsg').textContent = 'Saved. Copy the files that use them again.';
  } catch (e) { $('namemsg').textContent = e.message; }
};
const MARK = { ok: '✓ ', problem: '✗ ', check: '? ', change: '→ ', info: '· ' };
function showInventory(inv, fresh) {
  found = inv.names || null;
  installed = {}; (inv.files || []).forEach(f => { installed[f.path] = f.state; });
  $('checks').replaceChildren(...(inv.checks || []).map(c => {
    const li = document.createElement('li'), b = document.createElement('b');
    b.textContent = (MARK[c.level] || '') + c.what + ': '; li.appendChild(b); li.appendChild(document.createTextNode(c.detail));
    if (c.level === 'problem') li.className = 'err';
    return li;
  }));
  const bits = [];
  if (inv.version) bits.push('Home Assistant ' + inv.version);
  if (inv.home) bits.push('home ' + (inv.home.name || '') + ' (' + inv.home.time_zone + ')');
  bits.push((inv.entities || 0) + ' entities, ' + (inv.devices || 0) + ' devices');
  if (inv.persons && inv.persons.length) bits.push('people: ' + inv.persons.join(', '));
  if (inv.zones && inv.zones.length) bits.push('zones: ' + inv.zones.map(z => z.name).join(', '));
  if (inv.integrations) bits.push('integrations: ' + inv.integrations.map(i => i.domain).join(', '));
  if (inv.ui_dashboards && inv.ui_dashboards.length) bits.push('dashboards made in the editor: ' + inv.ui_dashboards.join(', ')
    + ' (pasted ones are not compared: paste again after a change)');
  $('found').textContent = bits.join('; ') + '.';
  $('discmsg').textContent = (fresh ? 'Read just now' : 'Last read ') + ' ' + new Date(inv.read_at).toLocaleString() + '.';
  showZones(inv);
  if (data) {
    renderNames(null);
    $('pk').replaceChildren(...data.files.filter(f => f.kind === 'package').map(f => row(f, true)));
    $('db').replaceChildren(...data.files.filter(f => f.kind === 'dashboard').map(f => row(f, false)));
    secrets();
  }
}
function showZones(inv) {
  const zin = (inv.zones || []).filter(z => !z.place), zout = inv.places_not_zones || [];
  $('zonebox').hidden = !(zin.length || zout.length);
  const a = [];
  if (zin.length) {
    const p = document.createElement('p'); p.className = 'dim'; p.textContent = 'Home Assistant zones that are not World Assistant places:'; a.push(p);
    zin.forEach(z => { const l = document.createElement('label'), b = document.createElement('input');
      b.type = 'checkbox'; b.checked = true; b.dataset.zone = z.name; b.style.width = 'auto';
      l.appendChild(b); l.appendChild(document.createTextNode(' ' + z.name)); a.push(l); });
    const go = document.createElement('button'); go.type = 'button'; go.textContent = 'Make them World Assistant places';
    go.onclick = async () => {
      const names = [...document.querySelectorAll('#zin input[data-zone]')].filter(b => b.checked).map(b => b.dataset.zone);
      try { const r = await fetch('/v1/ha/zones/import', { method: 'POST', headers: Object.assign(hdr(), { 'Content-Type': 'application/json' }), body: JSON.stringify({ names }) });
        const d = await r.json(); if (!r.ok) throw new Error(d.detail || r.status);
        $('zmsg').textContent = 'Made ' + (d.made.join(', ') || 'none') + (d.skipped.length ? '. Skipped: ' + d.skipped.join('; ') : '') + '. Run Find my setup again to refresh.';
      } catch (e) { $('zmsg').textContent = e.message; }
    };
    a.push(go);
  }
  $('zin').replaceChildren(...a);
  const o = [];
  if (zout.length) {
    const p = document.createElement('p'); p.className = 'dim';
    p.textContent = 'World Assistant places that are not Home Assistant zones: ' + zout.map(x => x.name).join(', ')
      + '. As a package (packages/wa_zones.yaml), Home Assistant adds them as zones after a restart; zones you made in Home Assistant are not touched.';
    o.push(p);
    const dl = document.createElement('button'); dl.type = 'button'; dl.textContent = 'Download wa_zones.yaml';
    dl.onclick = async () => {
      try { const r = await fetch('/v1/ha/zones/package', { headers: hdr() }); if (!r.ok) throw new Error(r.status);
        const url = URL.createObjectURL(new Blob([await r.text()], { type: 'text/yaml' })), a = document.createElement('a');
        a.href = url; a.download = 'wa_zones.yaml'; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
      } catch (e) { $('zmsg').textContent = e.message; }
    };
    const cp = document.createElement('button'); cp.type = 'button'; cp.textContent = 'Copy to Home Assistant';
    cp.onclick = async () => {
      try { const r = await fetch('/v1/ha/zones/copy', { method: 'POST', headers: hdr() }); const d = await r.json();
        if (!r.ok) throw new Error(d.detail || r.status);
        $('zmsg').textContent = d.ok ? 'Copied: check configuration and restart Home Assistant.' : 'Could not copy: ' + d.error;
      } catch (e) { $('zmsg').textContent = e.message; }
    };
    o.push(dl, cp);
  }
  $('zout').replaceChildren(...o);
}
async function apiCall(method, body) {
  const r = await fetch('/v1/ha/api', { method, headers: Object.assign(hdr(), body ? { 'Content-Type': 'application/json' } : {}), body: body ? JSON.stringify(body) : undefined });
  const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.detail || r.status); return d;
}
function apiShow(d) { $('haurl').value = d.url || ''; $('hatok').value = ''; $('hatok').placeholder = d.token_set ? 'saved: leave empty' : ''; if (d.url) $('apicard').open = true; }
$('apisave').onclick = async () => {
  $('apimsg').textContent = 'Testing...';
  try { const d = await apiCall('PUT', { url: $('haurl').value.trim(), token: $('hatok').value || null }); apiShow(d);
    $('apimsg').textContent = d.ok ? 'Working: ' + d.entities + ' entities. Run Find my setup for the live checks.' : 'Saved, but: ' + d.error;
  } catch (e) { $('apimsg').textContent = e.message; }
};
$('apiforget').onclick = async () => { try { apiShow(await apiCall('DELETE')); $('apimsg').textContent = 'Forgotten.'; } catch (e) { $('apimsg').textContent = e.message; } };
$('disc').onclick = async () => {
  $('discmsg').textContent = 'Reading Home Assistant...';
  try {
    const r = await fetch('/v1/ha/discover', { method: 'POST', headers: hdr() });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(r.status === 409 ? 'save the Samba share first (below)' : (d.detail || r.status));
    showInventory(d, true);
  } catch (e) { $('discmsg').textContent = e.message; }
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
      lovelace: $('smblove').checked,
      base_url: $('base').value.trim().replace(/\\/+$/, '') });
    $('smbresult').replaceChildren(...r.files.map(f => { const li = document.createElement('li'); li.textContent = f.path + ': ' + f.result; return li; }));
    if (r.secrets_differ && r.secrets_differ.length) { const li = document.createElement('li'); li.className = 'err';
      li.textContent = 'Already in secrets.yaml with a different value (left alone, check them): ' + r.secrets_differ.join(', '); $('smbresult').append(li); }
    $('smbcopymsg').textContent = r.ok ? 'Done. Check the configuration and restart Home Assistant.' : 'Stopped: ' + r.error;
    if (r.lovelace === 'added' || r.lovelace === 'updated') $('smbcopymsg').textContent += ' The dashboards then appear in the sidebar; delete any you pasted by hand (Settings > Dashboards).';
    if (r.lovelace === 'manual') { $('smbcard').open = true; $('smbcopymsg').textContent += ' Add the lovelace lines below to your own lovelace: section.'; }
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
