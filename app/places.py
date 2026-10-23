"""Places and trips API: named locations and the journeys between them."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.auth import require_token
from app.geo import PostcodeError, lookup_postcode
from app.pagestyle import themed
from app.postcodes import local_postcode

router = APIRouter(prefix="/v1", tags=["places"], dependencies=[Depends(require_token)])

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
KINDS = {"home", "work", "other"}


def check_slug(value: str) -> str:
    if not _SLUG.match(value):
        raise HTTPException(422, "id must be lower-case letters, digits and hyphens")
    return value


class PlaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: str = "other"
    postcode: str | None = None
    lat: float | None = Field(default=None, ge=49.5, le=61.0)  # UK and its waters
    lon: float | None = Field(default=None, ge=-8.7, le=2.0)

    @model_validator(mode="after")
    def _needs_location(self) -> PlaceIn:
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {sorted(KINDS)}")
        if self.postcode is None and (self.lat is None or self.lon is None):
            raise ValueError("give a postcode, or both lat and lon")
        return self


class TripIn(BaseModel):
    from_place: str
    to_place: str
    name: str | None = Field(default=None, max_length=120)
    enabled: bool = True


def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in result]


async def place_or_default(request: Request, place: str | None) -> str:
    """The given place id, or the default place when none is given.

    The default is the place of kind "home", else the one with the id "home", else the first by name.
    """
    if place:
        return place
    async with request.app.state.engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT id FROM place ORDER BY (kind = 'home') DESC, (id = 'home') DESC, name LIMIT 1")
            )
        ).first()
    if row is None:
        raise HTTPException(404, "no places saved yet: add your home on /places")
    return str(row[0])


@router.get("/places")
async def list_places(request: Request) -> list[dict[str, Any]]:
    async with request.app.state.engine.connect() as conn:
        return _rows(
            await conn.execute(
                text("SELECT id, name, kind, postcode, lat, lon FROM place ORDER BY name")
            )
        )


@router.put("/places/{place_id}")
async def put_place(place_id: str, body: PlaceIn, request: Request) -> dict[str, Any]:
    check_slug(place_id)
    postcode, lat, lon = body.postcode, body.lat, body.lon
    if postcode:
        try:
            # the local Code-Point Open table first; postcodes.io for anything it does not know
            located = await local_postcode(request.app.state.engine, postcode) or await lookup_postcode(postcode)
        except PostcodeError as exc:
            raise HTTPException(422, str(exc)) from exc
        postcode = located.postcode
        if lat is None or lon is None:  # explicit coordinates win (a drag-and-drop pin)
            lat, lon = located.lat, located.lon
    async with request.app.state.engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO place (id, name, kind, postcode, lat, lon)
                VALUES (:id, :name, :kind, :postcode, :lat, :lon)
                ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, kind = EXCLUDED.kind,
                  postcode = EXCLUDED.postcode, lat = EXCLUDED.lat, lon = EXCLUDED.lon,
                  updated_at = now()
                """
            ),
            {"id": place_id, "name": body.name, "kind": body.kind,
             "postcode": postcode, "lat": lat, "lon": lon},
        )  # fmt: skip
    return {"id": place_id, "name": body.name, "kind": body.kind,
            "postcode": postcode, "lat": lat, "lon": lon}  # fmt: skip


@router.delete("/places/{place_id}", status_code=204)
async def delete_place(place_id: str, request: Request) -> None:
    async with request.app.state.engine.begin() as conn:
        result = await conn.execute(text("DELETE FROM place WHERE id = :id"), {"id": place_id})
    if not result.rowcount:
        raise HTTPException(404, "no such place")


@router.get("/trips")
async def list_trips(request: Request) -> list[dict[str, Any]]:
    async with request.app.state.engine.connect() as conn:
        return _rows(
            await conn.execute(
                text(
                    """
                    SELECT id, name, from_place, to_place, enabled, corridor_id, distance_km,
                           duration_s, roads, route_built_at
                    FROM trip ORDER BY name
                    """
                )
            )
        )


@router.put("/trips/{trip_id}")
async def put_trip(trip_id: str, body: TripIn, request: Request) -> dict[str, Any]:
    check_slug(trip_id)
    if body.from_place == body.to_place:
        raise HTTPException(422, "a trip needs two different places")
    name = body.name or f"{body.from_place} to {body.to_place}"
    try:
        async with request.app.state.engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO trip (id, name, from_place, to_place, enabled)
                    VALUES (:id, :name, :f, :t, :enabled)
                    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name,
                      from_place = EXCLUDED.from_place, to_place = EXCLUDED.to_place,
                      enabled = EXCLUDED.enabled
                    """
                ),
                {"id": trip_id, "name": name, "f": body.from_place, "t": body.to_place,
                 "enabled": body.enabled},
            )  # fmt: skip
    except IntegrityError as exc:
        raise HTTPException(
            422, "both places must exist, and each pair of places can have one trip"
        ) from exc
    return {"id": trip_id, "name": name, "from_place": body.from_place,
            "to_place": body.to_place, "enabled": body.enabled}  # fmt: skip


@router.post("/trips/{trip_id}/route")
async def build_route(trip_id: str, request: Request) -> dict[str, Any]:
    """Route the trip with Valhalla and (re)build its corridor."""
    from app.config import get_settings
    from app.routing import RoutingError, ValhallaClient, build_trips

    client = ValhallaClient(get_settings().valhalla_url)
    try:
        return (await build_trips(request.app.state.engine, client, [trip_id]))[0]
    except RoutingError as exc:
        status = 404 if str(exc).startswith("no trip") else 502
        raise HTTPException(status, str(exc)) from exc
    finally:
        await client.aclose()


@router.delete("/trips/{trip_id}", status_code=204)
async def delete_trip(trip_id: str, request: Request) -> None:
    async with request.app.state.engine.begin() as conn:
        result = await conn.execute(text("DELETE FROM trip WHERE id = :id"), {"id": trip_id})
    if not result.rowcount:
        raise HTTPException(404, "no such trip")


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant places</title>
<style>
/*BASE_CSS*/</style></head><body><main>
<!--NAV-->
<h1>Places and trips</h1>
<form id="auth" class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button class="primary">Load</button></form>
<p id="msg" class="err"></p>

<div class="card"><h2>Places</h2>
<table id="places"><tbody></tbody></table>
<form id="addplace" class="row" style="margin-top:10px">
  <input id="pname" placeholder="Name (e.g. Brighton)" required>
  <input id="ppc" placeholder="Postcode" required>
  <select id="pkind"><option>other</option><option>home</option><option>work</option></select>
  <button>Add or update place</button>
</form></div>

<div class="card"><h2>Trips</h2>
<table id="trips"><tbody></tbody></table>
<form id="addtrip" class="row" style="margin-top:10px">
  <select id="tfrom"></select> <span class="muted">to</span> <select id="tto"></select>
  <button>Add trip</button>
</form></div>

<p class="dim">Vehicles have their own tab: <a href="/vehicles">Vehicles</a>.</p>

<script>
const tok = document.getElementById('t'), msg = document.getElementById('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
const slug = s => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40);
const FIELD = { battery_kwh: 'Battery size', soh_percent: 'Battery health', miles_per_kwh: 'Miles per kWh',
  home_pence_per_kwh: 'Home electricity', mpg: 'Real-world mpg', name: 'Name', fuel: 'Fuel', postcode: 'Postcode' };
async function api(method, path, body) {
  const r = await fetch(path, { method, headers: { Authorization: 'Bearer ' + tok.value,
    'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) {
    let d = r.status; try { d = (await r.json()).detail; } catch (e) {}
    if (Array.isArray(d)) d = d.map(x => {  // a form the API refused: say which box and why
      const f = (x.loc || []).filter(k => k !== 'body').pop();
      return (typeof f === 'string' ? (FIELD[f] || f.replace(/_/g, ' ')) + ': ' : '') + (x.msg || '').replace(/^Value error, /, '');
    }).join('; ');
    throw new Error(typeof d === 'string' ? d : JSON.stringify(d));
  }
  return r.status === 204 ? null : r.json();
}
function cell(tr, text) { const td = document.createElement('td'); td.textContent = text; tr.appendChild(td); }
function delBtn(tr, path) {
  const td = document.createElement('td'), b = document.createElement('button');
  b.textContent = 'Delete'; b.onclick = () => run(() => api('DELETE', path)); td.appendChild(b); tr.appendChild(td);
}
function routeBtn(tr, t) {
  const td = document.createElement('td'), b = document.createElement('button');
  b.textContent = t.corridor_id ? 'Rebuild route' : 'Build route';
  b.onclick = () => run(() => api('POST', '/v1/trips/' + t.id + '/route')); td.appendChild(b); tr.appendChild(td);
}
const when = s => s ? new Date(s).toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : 'no end date';
function condLine(i) {
  const d = document.createElement('div');
  d.textContent = '[' + i.source.toUpperCase() + '] ' + i.headline + (i.where ? ' - ' + i.where : '')
    + ' (' + (i.starts ? 'from ' + when(i.starts) + ', ' : '') + 'until ' + when(i.ends) + ')';
  d.className = i.level >= 3 ? 'err' : 'dim';
  return d;
}
function condCell(tr, t) {
  const td = document.createElement('td'), s = document.createElement('span'), b = document.createElement('button');
  s.textContent = t.corridor_id ? 'checking...' : ''; td.appendChild(s);
  tr.appendChild(td);
  if (!t.corridor_id) return null;
  const more = document.createElement('tr'), mt = document.createElement('td');
  mt.colSpan = 5; more.appendChild(mt); more.hidden = true;
  b.textContent = 'Conditions'; b.style.marginLeft = '8px'; b.onclick = () => { more.hidden = !more.hidden; };
  td.appendChild(b);
  api('GET', '/v1/trips/' + t.id + '/conditions').then(c => {
    s.textContent = c.now.status + (c.planned.count ? ', ' + c.planned.count + ' planned' : '');
    const lines = [];
    const head = document.createElement('div'); head.textContent = c.summary; lines.push(head);
    if (c.now.items.length) { const h = document.createElement('strong'); h.textContent = 'Now'; lines.push(h, ...c.now.items.map(condLine)); }
    if (c.planned.items.length) { const h = document.createElement('strong'); h.textContent = 'Planned, next ' + c.planned.days + ' days'; lines.push(h, ...c.planned.items.map(condLine)); }
    const note = document.createElement('div'); note.className = 'dim'; note.textContent = c.caveats.join(' '); lines.push(note);
    mt.replaceChildren(...lines);
  }).catch(e => { s.textContent = 'unavailable'; mt.textContent = e.message; });
  return more;
}
async function load() {
  msg.textContent = '';
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  const [places, trips] = await Promise.all([api('GET', '/v1/places'), api('GET', '/v1/trips')]);
  const pb = document.querySelector('#places tbody'); pb.replaceChildren();
  for (const p of places) {
    const tr = document.createElement('tr');
    cell(tr, p.name); cell(tr, p.kind); cell(tr, p.postcode || ''); delBtn(tr, '/v1/places/' + p.id);
    pb.appendChild(tr);
  }
  const tb = document.querySelector('#trips tbody'); tb.replaceChildren();
  for (const t of trips) {
    const tr = document.createElement('tr');
    cell(tr, t.name);
    cell(tr, t.corridor_id ? (t.distance_km.toFixed(0) + ' km, ' + Math.round(t.duration_s / 60) + ' min, ' + (t.roads || []).join(' ')) : 'route pending');
    const more = condCell(tr, t);
    routeBtn(tr, t); delBtn(tr, '/v1/trips/' + t.id);
    tb.appendChild(tr); if (more) tb.appendChild(more);
  }
  for (const id of ['tfrom', 'tto']) {
    const sel = document.getElementById(id); sel.replaceChildren();
    for (const p of places) { const o = document.createElement('option'); o.value = p.id; o.textContent = p.name; sel.appendChild(o); }
  }
}
async function run(fn, out = msg) {  // out: where a failure is shown, next to the form that caused it
  msg.textContent = ''; out.textContent = '';
  try { await fn(); await load(); } catch (e) { out.textContent = e.message; }
}
document.getElementById('auth').onsubmit = e => { e.preventDefault(); run(() => Promise.resolve()); };
document.getElementById('addplace').onsubmit = e => {
  e.preventDefault();
  const name = document.getElementById('pname').value;
  run(async () => {
    await api('PUT', '/v1/places/' + slug(name), { name, postcode: document.getElementById('ppc').value,
      kind: document.getElementById('pkind').value });
    e.target.reset();
  });
};
document.getElementById('addtrip').onsubmit = e => {
  e.preventDefault();
  const f = document.getElementById('tfrom').value, t = document.getElementById('tto').value;
  run(() => api('PUT', '/v1/trips/' + f + '-to-' + t, { from_place: f, to_place: t }));
};
if (tok.value) run(() => Promise.resolve());
</script></main></body></html>"""

page_router = APIRouter()


@page_router.get("/places", response_class=HTMLResponse, include_in_schema=False)
async def places_page(request: Request) -> str:
    """The page holds no data itself; it asks for the token and calls /v1/places."""
    return themed(PAGE, "places", request)
