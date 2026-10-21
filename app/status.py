"""Read-only status API and a small status page.

Everything here reports what the application itself knows (job runs, stored data,
worker heartbeat). It cannot see other containers or Unraid, and it never writes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.auth import require_token
from app.pagestyle import themed
from app.version import COMMIT, VERSION

router = APIRouter(prefix="/v1/status", tags=["status"], dependencies=[Depends(require_token)])
page_router = APIRouter()


def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in result]


@router.get("")
async def status(request: Request) -> dict[str, Any]:
    out: dict[str, Any] = {"time": datetime.now(UTC).isoformat(),
                           "version": VERSION, "commit": COMMIT}  # fmt: skip
    async with request.app.state.engine.connect() as conn:
        out["jobs_latest"] = _rows(
            await conn.execute(
                text(
                    """
                    SELECT DISTINCT ON (job) job, started_at, finished_at, status, result, error,
                           round(extract(epoch FROM finished_at - started_at)::numeric, 1) AS seconds
                    FROM job_run ORDER BY job, started_at DESC
                    """
                )
            )
        )
        out["jobs_last_24h"] = _rows(
            await conn.execute(
                text(
                    """
                    SELECT job, status, count(*) AS runs FROM job_run
                    WHERE started_at > now() - interval '24 hours' GROUP BY job, status
                    ORDER BY job, status
                    """
                )
            )
        )
        out["events_live"] = _rows(
            await conn.execute(
                text(
                    """
                    SELECT scope, kind, status, count(*) AS n FROM nh_event
                    WHERE cleared_at IS NULL GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
                    """
                )
            )
        )
        out["events_total"] = (await conn.execute(text("SELECT count(*) FROM nh_event"))).scalar_one()
        out["corridors"] = _rows(
            await conn.execute(
                text(
                    """
                    SELECT c.id, c.name, c.enabled, c.source, jsonb_array_length(c.centreline) AS points,
                           count(e.record_id) FILTER (WHERE ev.cleared_at IS NULL) AS live_events,
                           count(e.record_id) FILTER (WHERE ev.cleared_at IS NULL
                                                      AND ev.status = 'active') AS active_events
                    FROM nh_corridor c
                    LEFT JOIN nh_event_corridor e ON e.corridor_id = c.id
                    LEFT JOIN nh_event ev ON ev.record_id = e.record_id
                    GROUP BY c.id ORDER BY c.id
                    """
                )
            )
        )
        out["signs"] = _rows(
            await conn.execute(
                text(
                    """
                    SELECT count(*) AS total,
                           count(*) FILTER (WHERE jsonb_array_length(lines) > 0) AS showing_text,
                           max(last_seen) AS last_seen,
                           (SELECT count(*) FROM nh_sign_message) AS messages_logged
                    FROM nh_sign
                    """
                )
            )
        )[0]
        out["fuel"] = _rows(
            await conn.execute(
                text(
                    """
                    SELECT (SELECT count(*) FROM fuel_station) AS stations,
                           (SELECT count(*) FROM fuel_price) AS prices,
                           (SELECT count(*) FROM fuel_price WHERE fuel_type = 'E10') AS e10_prices,
                           (SELECT max(observed_at) FROM fuel_price) AS prices_observed_at
                    """
                )
            )
        )[0]
        out["raw_capture"] = _rows(
            await conn.execute(
                text("SELECT count(*) AS rows, max(captured_at) AS latest FROM raw_capture")
            )
        )[0]
    try:
        out["worker_heartbeat"] = await request.app.state.redis.get("wa:worker:heartbeat")
    except Exception as exc:  # noqa: BLE001 - report, don't fail the page
        out["worker_heartbeat"] = f"error: {type(exc).__name__}"
    return out


@router.get("/jobs")
async def jobs(
    request: Request,
    job: str | None = None,
    limit: int = Query(20, ge=1, le=200),
) -> list[dict[str, Any]]:
    async with request.app.state.engine.connect() as conn:
        return _rows(
            await conn.execute(
                text(
                    """
                    SELECT id, job, started_at, finished_at, status, result, error
                    FROM job_run WHERE (CAST(:job AS text) IS NULL OR job = :job)
                    ORDER BY started_at DESC LIMIT :limit
                    """
                ),
                {"job": job, "limit": limit},
            )
        )


@router.get("/events")
async def events(
    request: Request,
    corridor: str | None = None,
    status: str | None = Query(None, description="e.g. active, planned, suspended"),
    kind: str | None = Query(None, description="closure or speed"),
    limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Live stored events, optionally only those tagged to a corridor."""
    async with request.app.state.engine.connect() as conn:
        return _rows(
            await conn.execute(
                text(
                    """
                    SELECT e.record_id, e.scope, e.kind, e.status, e.start_time, e.end_time,
                           e.limit_mph, e.detail, e.roads,
                           e.segments -> 0 ->> 'description' AS description,
                           e.segments -> 0 ->> 'direction' AS direction
                    FROM nh_event e
                    WHERE e.cleared_at IS NULL
                      AND (CAST(:status AS text) IS NULL OR e.status = :status)
                      AND (CAST(:kind AS text) IS NULL OR e.kind = :kind)
                      AND (CAST(:corridor AS text) IS NULL OR EXISTS (
                            SELECT 1 FROM nh_event_corridor x
                            WHERE x.record_id = e.record_id AND x.corridor_id = :corridor))
                    ORDER BY e.start_time DESC NULLS LAST LIMIT :limit
                    """
                ),
                {"status": status, "kind": kind, "corridor": corridor, "limit": limit},
            )
        )


def _sample(points: list[list[float]], n: int = 6) -> list[list[float]]:
    """At most n points spread along a line, always including both ends."""
    if len(points) <= n:
        return points
    step = (len(points) - 1) / (n - 1)
    return [points[round(i * step)] for i in range(n)]


@router.get("/events/map")
async def events_map(
    request: Request,
    planned_days: int = Query(7, ge=0, le=60, description="planned works starting within this many days"),
) -> dict[str, Any]:
    """Every live road event nationwide with where it is, for a client that filters by its own
    location (Home Assistant). Unplanned events always; planned only if they start soon."""
    async with request.app.state.engine.connect() as conn:
        rows = _rows(
            await conn.execute(
                text(
                    """
                    SELECT record_id, scope, kind, status, start_time, end_time, limit_mph, detail,
                           cause, roads, segments
                    FROM nh_event
                    WHERE cleared_at IS NULL
                      AND (end_time IS NULL OR end_time > now())
                      AND (scope = 'unplanned'
                           OR start_time IS NULL
                           OR start_time < now() + make_interval(days => :days))
                    ORDER BY start_time DESC NULLS LAST
                    """
                ),
                {"days": planned_days},
            )
        )
    out: list[dict[str, Any]] = []
    for r in rows:
        segs = r.pop("segments") or []
        pts = [p for sg in segs for p in (sg.get("points") or [])]
        if not pts:
            continue
        first = segs[0]
        out.append({
            "id": r["record_id"], "scope": r["scope"], "kind": r["kind"], "status": r["status"],
            "start": r["start_time"], "end": r["end_time"], "limit_mph": r["limit_mph"],
            "detail": r["detail"], "cause": r["cause"], "roads": r["roads"],
            "description": first.get("description"), "direction": first.get("direction"),
            "lat": round(pts[0][0], 5), "lon": round(pts[0][1], 5),
            "pts": [[round(a, 4), round(b, 4)] for a, b in _sample(pts)],
        })  # fmt: skip
    return {"count": len(out), "events": out}


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant status</title>
<style>
/*BASE_CSS*/</style></head><body><main>
<!--NAV-->
<h1>Status</h1>
<form id="f" class="bar"><input id="t" type="password" placeholder="API token" autocomplete="current-password">
<button class="primary">Load</button></form>
<p id="msg" class="err"></p>
<div class="card"><h2>Backups</h2>
  <p class="dim">Your places, trips, vehicles, boards and bus history. Fuel prices and road events refill by themselves
  and are not included. Download a copy and keep it somewhere other than this server.</p>
  <div class="row"><button id="bk" class="primary" type="button">Back up now</button>
  <span id="bkmsg" class="dim"></span></div>
  <table id="bklist"></table></div>
<div class="card"><h2>Automatic updates</h2>
  <p class="dim">When on, a job on this server checks every night at 04:00 for a newer signed release, backs up,
  updates, checks that every page and endpoint still answers, and puts the old version back if they do not.
  It can only apply what has been released. The one-time setup of that job is in the runbook.</p>
  <div class="row"><label style="flex-direction:row;align-items:center;gap:8px;margin:0">
  <input id="au" type="checkbox" style="width:auto"> Update this server automatically</label></div>
  <p id="aumsg" class="dim"></p></div>
<div class="bar"><button id="copy" type="button">Copy to clipboard</button>
<button id="refresh" type="button">Refresh</button></div>
<div class="card"><pre id="out">Enter the token from api_token.txt, then Load.</pre></div>
<script>
const out = document.getElementById('out'), msg = document.getElementById('msg');
const tok = document.getElementById('t');
try { tok.value = sessionStorage.getItem('wa_token') || ''; } catch (e) {}
async function load() {
  msg.textContent = '';
  try { sessionStorage.setItem('wa_token', tok.value); } catch (e) {}
  const r = await fetch('/v1/status', { headers: { Authorization: 'Bearer ' + tok.value } });
  if (!r.ok) { msg.textContent = 'Request failed: ' + r.status; return; }
  out.textContent = JSON.stringify(await r.json(), null, 2);
  bkLoad(); auLoad();
}
const AU_WORDS = { updated: 'Updated', unchanged: 'Already up to date', refused: 'Not applied',
  rolled_back: 'Update failed its checks; the previous version is running again', failed: 'Needs attention' };
async function auLoad() {
  const m = document.getElementById('aumsg'), box = document.getElementById('au');
  try {
    const r = await fetch('/v1/auto-deploy', { headers: { Authorization: 'Bearer ' + tok.value } });
    if (!r.ok) throw new Error('auto-deploy returned ' + r.status);
    const d = await r.json(); box.checked = d.enabled;
    const l = d.last_run;
    m.textContent = 'Running ' + d.running.version + '. ' + (l
      ? 'Last run ' + new Date(l.reported).toLocaleString() + ': ' + (AU_WORDS[l.result] || l.result)
        + (l.message ? ' (' + l.message + ')' : '') + '.'
      : 'The nightly job has not reported yet.');
  } catch (e) { m.textContent = e.message; }
}
document.getElementById('au').onchange = async ev => {
  const m = document.getElementById('aumsg');
  try {
    const r = await fetch('/v1/auto-deploy', { method: 'PUT',
      headers: { Authorization: 'Bearer ' + tok.value, 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: ev.target.checked }) });
    if (!r.ok) throw new Error('could not save: ' + r.status);
    await auLoad();
  } catch (e) { ev.target.checked = !ev.target.checked; m.textContent = e.message; }
};
function bkRow(b) {
  const tr = document.createElement('tr');
  for (const t of [b.name, Math.max(1, Math.round(b.size_bytes / 1024)) + ' KB']) { const td = tr.insertCell(); td.textContent = t; }
  const td = tr.insertCell(), a = document.createElement('button');
  a.textContent = 'Download'; a.onclick = () => bkDownload(b.name); td.appendChild(a); return tr;
}
async function bkLoad() {
  const m = document.getElementById('bkmsg'), tbl = document.getElementById('bklist');
  try {
    const r = await fetch('/v1/backups', { headers: { Authorization: 'Bearer ' + tok.value } });
    if (!r.ok) throw new Error('backups returned ' + r.status);
    const d = await r.json(); tbl.replaceChildren(...d.backups.map(bkRow));
    m.textContent = d.backups.length ? 'Keeping the latest ' + d.keep + '.' : 'No backups yet.';
  } catch (e) { m.textContent = e.message; }
}
async function bkDownload(name) {
  const r = await fetch('/v1/backups/' + encodeURIComponent(name), { headers: { Authorization: 'Bearer ' + tok.value } });
  if (!r.ok) { document.getElementById('bkmsg').textContent = 'download failed: ' + r.status; return; }
  const url = URL.createObjectURL(await r.blob()), a = document.createElement('a');
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
}
document.getElementById('bk').onclick = async () => {
  const m = document.getElementById('bkmsg'); m.textContent = 'Backing up...';
  try {
    const r = await fetch('/v1/backups', { method: 'POST', headers: { Authorization: 'Bearer ' + tok.value } });
    if (!r.ok) { let d = r.status; try { d = (await r.json()).detail; } catch (e) {} throw new Error(d); }
    const b = await r.json(); await bkLoad(); m.textContent = 'Saved ' + b.name + '.';
  } catch (e) { m.textContent = e.message; }
};
document.getElementById('f').onsubmit = e => { e.preventDefault(); load(); };
document.getElementById('refresh').onclick = load;
document.getElementById('copy').onclick = () => navigator.clipboard.writeText(out.textContent);
if (tok.value) load();
</script></main></body></html>"""


@page_router.get("/status", response_class=HTMLResponse, include_in_schema=False)
async def status_page() -> str:
    """The page itself holds no data; it asks for the token and calls /v1/status."""
    return themed(PAGE, "status")
