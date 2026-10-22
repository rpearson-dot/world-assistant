"""Transport for London departures API: saved boards, live departures, line status."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Self

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text

from app.auth import require_token
from app.config import get_settings
from app.integrations.rail import make_rail_client
from app.integrations.rail.rdm import RailError, RdmClient, parse_services
from app.integrations.tfl.client import TflClient, TflError
from app.integrations.tfl.normalise import (
    parse_departures,
    select,
    summarise_status,
)
from app.pagestyle import themed
from app.places import check_slug
from app.ratelimit import SlidingWindowLimiter

log = logging.getLogger("wa.tfl")
router = APIRouter(prefix="/v1/tfl", tags=["tfl"], dependencies=[Depends(require_token)])
page_router = APIRouter()

CACHE_SECONDS = 30.0
_cache: dict[str, tuple[float, Any]] = {}
_ID = re.compile(r"^[A-Za-z0-9,-]{1,120}$")


class BoardIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    source: Literal["tfl", "nationalrail"] = Field(
        "tfl",
        description="tfl: London (TfL data). nationalrail: anywhere in Great Britain (Darwin)",
    )
    station: str = Field(
        description="tfl: StopPoint id, e.g. 910GWORCSPK. nationalrail: three-letter CRS code, e.g. SNL. "
        "Find either with /v1/tfl/stations"
    )
    lines: list[str] = Field(
        default_factory=list, max_length=10, description="tfl only, e.g. south-western-railway"
    )
    include: list[str] = Field(
        default_factory=list, description="destination must contain one of these"
    )
    exclude: list[str] = Field(default_factory=list, description="destination must contain none")
    max_rows: int = Field(6, ge=1, le=20)

    @model_validator(mode="after")
    def _check_source(self) -> Self:
        if self.source == "tfl" and not self.lines:
            raise ValueError("a TfL board needs at least one line id")
        if self.source == "nationalrail" and not re.fullmatch(r"[A-Za-z]{3}", self.station):
            raise ValueError("a National Rail station is a three-letter code, e.g. SNL")
        return self


def _client(request: Request) -> TflClient:
    settings = get_settings()
    key = settings.tfl_app_key
    limiter = SlidingWindowLimiter(request.app.state.redis, "tfl", 250 if key else 30)
    return TflClient(key, limiter=limiter)


STALE_SECONDS = 900.0  # if the provider fails, keep serving what we last got for up to this long
_locks: dict[str, asyncio.Lock] = {}


async def _cached_aged(key: str, ttl: float, fetch: Any) -> tuple[Any, float]:
    """(value, age in seconds). Concurrent callers share one upstream request. When the
    provider errors, a recent earlier answer is returned instead of an error."""
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1], time.monotonic() - hit[0]
    async with _locks.setdefault(key, asyncio.Lock()):
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1], time.monotonic() - hit[0]
        try:
            value = await fetch()
        except (TflError, RailError):
            if hit and time.monotonic() - hit[0] < STALE_SECONDS:
                log.warning("upstream failed for %s; serving data %ds old", key.split(":")[0],
                            time.monotonic() - hit[0])  # fmt: skip
                return hit[1], time.monotonic() - hit[0]
            raise
        _cache[key] = (time.monotonic(), value)
        return value, 0.0


async def _cached(key: str, ttl: float, fetch: Any) -> Any:
    return (await _cached_aged(key, ttl, fetch))[0]


def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in result]


@router.get("/stations")
async def find_stations(
    request: Request,
    q: str = Query(min_length=2, max_length=60),
    modes: str = Query("national-rail,tube,overground,dlr,elizabeth-line,bus"),
    source: Literal["tfl", "nationalrail"] = Query("tfl"),
) -> list[dict[str, Any]]:
    """Find a station or stop id by name (tfl), or a three-letter station code (nationalrail)."""
    if source == "nationalrail":
        try:
            async with _rail_client(request) as rc:
                found = await rc.search_stations(q)
        except RailError as exc:
            raise HTTPException(502, str(exc)) from exc
        return [
            {"id": f.get("crsCode"), "name": f.get("stationName"), "modes": ["national-rail"]}
            for f in found[:15]
        ]
    try:
        async with _client(request) as c:
            matches = await c.search_stops(q, modes)
    except TflError as exc:
        raise HTTPException(502, str(exc)) from exc
    return [{"id": m.get("id"), "name": m.get("name"), "modes": m.get("modes")} for m in matches]


@router.get("/lines/status")
async def lines_status(
    request: Request, lines: str = Query("tube", pattern=_ID.pattern)
) -> list[dict[str, Any]]:
    ids = lines.split(",")
    try:
        async with _client(request) as c:
            data = await _cached(f"status:{lines}", CACHE_SECONDS, lambda: c.line_status(ids))
    except TflError as exc:
        raise HTTPException(502, str(exc)) from exc
    return summarise_status(data)


@router.get("/boards")
async def list_boards(request: Request) -> list[dict[str, Any]]:
    async with request.app.state.engine.connect() as conn:
        return _rows(
            await conn.execute(
                text(
                    "SELECT id, name, source, station, lines, include, exclude, max_rows FROM tfl_board ORDER BY name"
                )
            )
        )


@router.put("/boards/{board_id}")
async def put_board(board_id: str, body: BoardIn, request: Request) -> dict[str, Any]:
    check_slug(board_id)
    for value in [body.station, *body.lines]:
        if not _ID.match(value):
            raise HTTPException(422, f"unexpected characters in {value!r}")
    async with request.app.state.engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO tfl_board (id, name, source, station, lines, include, exclude, max_rows)
                VALUES (:id, :name, :source, :station, :lines, :include, :exclude, :max_rows)
                ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, source = EXCLUDED.source, station = EXCLUDED.station,
                  lines = EXCLUDED.lines, include = EXCLUDED.include, exclude = EXCLUDED.exclude,
                  max_rows = EXCLUDED.max_rows
                """
            ),
            {"id": board_id, **body.model_dump()},
        )
    return {"id": board_id, **body.model_dump()}


@router.delete("/boards/{board_id}", status_code=204)
async def delete_board(board_id: str, request: Request) -> None:
    async with request.app.state.engine.begin() as conn:
        result = await conn.execute(text("DELETE FROM tfl_board WHERE id = :id"), {"id": board_id})
    if not result.rowcount:
        raise HTTPException(404, "no such board")


def _rail_client(request: Request) -> RdmClient:
    settings = get_settings()
    limiter = SlidingWindowLimiter(request.app.state.redis, "nr", 60)
    return make_rail_client(settings, limiter)


async def board_departures(request: Request, board: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    now = datetime.now(UTC)
    try:
        if board.get("source") == "nationalrail":
            async with _rail_client(request) as rc:
                data, age = await _cached_aged(
                    f"nr:{board['station']}",
                    CACHE_SECONDS,
                    lambda: rc.departures(board["station"], 10),
                )
            parsed = parse_services(data, now - timedelta(seconds=age), settings.tfl_naive_tz)
        else:
            async with _client(request) as c:
                records, age = await _cached_aged(
                    f"dep:{board['station']}:{','.join(board['lines'])}", CACHE_SECONDS,
                    lambda: c.arrival_departures(board["station"], board["lines"]),
                )  # fmt: skip
            parsed = parse_departures(records, settings.tfl_naive_tz)
    except (TflError, RailError) as exc:
        raise HTTPException(502, str(exc)) from exc
    deps = select(
        parsed, include=board["include"], exclude=board["exclude"], now=now, limit=board["max_rows"]
    )
    return {"board": board["name"], "source": board.get("source", "tfl"), "station": board["station"],
            "as_of": now.isoformat(), "data_age_seconds": round(age), "stale": age > 2 * CACHE_SECONDS,
            "departures": [d.as_dict(now) for d in deps]}  # fmt: skip


@router.get("/boards/{board_id}/departures")
async def departures(board_id: str, request: Request) -> dict[str, Any]:
    async with request.app.state.engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT name, source, station, lines, include, exclude, max_rows FROM tfl_board WHERE id = :id"
                    ),
                    {"id": board_id},
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise HTTPException(404, f"no board {board_id!r}")
    return await board_departures(request, dict(row))


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant trains</title>
<style>
/*BASE_CSS*/</style></head><body><main>
<!--NAV-->
<h1>Trains</h1>
<div class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button id="go" class="primary" type="button">Load</button></div>
<p id="msg" class="err"></p><div id="boards"></div>
<div class="card"><h2>Show by platform</h2>
  <p class="dim">Display only, saved in this browser. For example 1=To London, 2=From London.</p>
  <div class="row"><input id="split" placeholder="1=To London, 2=From London" style="flex:1 1 260px">
  <button id="splitsave" type="button">Apply</button></div></div>
<div class="card"><h2>Add a board</h2>
  <div class="row"><label>Source<select id="src"><option value="tfl">TfL (London)</option>
    <option value="nationalrail">National Rail (anywhere in GB)</option></select></label>
    <label>Station<input id="q" placeholder="e.g. Worcester Park, or SNL for National Rail"></label>
    <button id="find" type="button" style="align-self:end">Find</button>
    <label>Pick<select id="hits"></select></label></div>
  <div class="row"><label>Name<input id="bname" placeholder="e.g. To Waterloo"></label>
    <label>TfL line ids<input id="blines" placeholder="south-western-railway"></label>
    <label>Only destinations containing<input id="binc" placeholder="comma separated"></label>
    <label>Never destinations containing<input id="bexc"></label>
    <button id="add" class="primary" type="button" style="align-self:end">Add board</button></div>
</div>
<script>const $ = id => document.getElementById(id), tok = $('t'), msg = $('msg');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
const slug = s => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40);
function platformSplit() {
  try { return JSON.parse(localStorage.getItem('wa_platform_split') || '{}'); } catch (e) { return {}; }
}
const csv = v => v.split(',').map(x => x.trim()).filter(Boolean);
async function api(method, path, body) {
  const r = await fetch(path, { method, headers: { Authorization: 'Bearer ' + tok.value, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) { let d = r.status; try { d = (await r.json()).detail; } catch (e) {} throw new Error(typeof d === 'string' ? d : JSON.stringify(d)); }
  return r.status === 204 ? null : r.json();
}
const hhmm = iso => iso ? new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : '';
function cell(tr, text, cls) { const td = tr.insertCell(); td.textContent = text; if (cls) td.className = cls; }
async function load() {
  msg.textContent = '';
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  try {
    const boards = await api('GET', '/v1/tfl/boards'), root = $('boards'); root.replaceChildren();
    for (const b of boards) {
      const card = document.createElement('div'); card.className = 'card'; root.appendChild(card);
      const h = document.createElement('h2'); h.textContent = b.name; card.appendChild(h);
      const del = document.createElement('button'); del.textContent = 'Remove'; del.className = 'link push'; del.onclick = async () => { await api('DELETE', '/v1/tfl/boards/' + b.id); load(); };
      h.appendChild(del);
      const tbl = document.createElement('table'); card.appendChild(tbl);
      try {
        const d = await api('GET', '/v1/tfl/boards/' + b.id + '/departures');
        if (d.stale) { const n = document.createElement('p'); n.className = 'dim'; n.textContent = 'Showing the last good reading from ' + d.data_age_seconds + ' seconds ago; the provider is not answering.'; card.appendChild(n); }
        const split = platformSplit();
        const known = d.departures.some(x => split[x.platform]);
        const groups = known ? [...Object.keys(split).sort().map(p => ({ title: split[p] + ' (platform ' + p + ')', rows: d.departures.filter(x => x.platform === p) })),
          { title: 'Other or not yet known', rows: d.departures.filter(x => !split[x.platform]) }].filter(g => g.rows.length)
          : [{ title: null, rows: d.departures }];
        for (const g of groups) {
          if (g.title) { const h3 = document.createElement('h3'); h3.textContent = g.title; card.appendChild(h3); }
          const t2 = g.title ? document.createElement('table') : tbl; if (g.title) card.appendChild(t2);
          for (const x of g.rows) {
            const tr = t2.insertRow(); const late = x.status && x.status !== 'OnTime';
            cell(tr, hhmm(x.expected || x.scheduled)); cell(tr, x.destination || ''); cell(tr, g.title ? '' : (x.platform ? 'Plat ' + x.platform : ''));
            cell(tr, x.status || (x.delay_minutes > 0 ? 'Late' : ''), late ? 'late' : '');
            cell(tr, x.minutes == null ? '' : x.minutes + ' min', 'dim');
          }
        }
        if (!d.departures.length) { const p = document.createElement('p'); p.className = 'dim'; p.textContent = 'No departures found.'; card.appendChild(p); }
      } catch (e) { const p = document.createElement('p'); p.className = 'err'; p.textContent = e.message; card.appendChild(p); }
    }
    if (!boards.length) root.textContent = 'No boards yet. Add one below.';
  } catch (e) { msg.textContent = e.message; }
}
try { const cur = platformSplit(); $('split').value = Object.entries(cur).map(([k, v]) => k + '=' + v).join(', '); } catch (e) {}
$('splitsave').onclick = () => {
  const m = Object.fromEntries(csv($('split').value).map(x => x.split('=').map(y => y.trim())).filter(p => p.length === 2 && p[0] && p[1]));
  try { localStorage.setItem('wa_platform_split', JSON.stringify(m)); } catch (e) {}
  load();
};
$('go').onclick = load;
$('find').onclick = async () => {
  try { const r = await api('GET', '/v1/tfl/stations?source=' + $('src').value + '&q=' + encodeURIComponent($('q').value));
    $('hits').replaceChildren(...r.map(m => new Option(m.name + ' (' + (m.modes || []).join(', ') + ') ' + m.id, m.id))); }
  catch (e) { msg.textContent = e.message; }
};
$('add').onclick = async () => {
  try { await api('PUT', '/v1/tfl/boards/' + slug($('bname').value), { name: $('bname').value, source: $('src').value, station: $('hits').value,
    lines: csv($('blines').value), include: csv($('binc').value), exclude: csv($('bexc').value) }); load(); }
  catch (e) { msg.textContent = e.message; }
};
if (tok.value) load();
setInterval(() => { if (tok.value && !document.hidden) load(); }, 30000);
</script></main></body></html>"""


@page_router.get("/trains", response_class=HTMLResponse, include_in_schema=False)
async def trains_page(request: Request) -> str:
    return themed(PAGE, "trains", request)
