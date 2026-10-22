"""Read-only status API (the Settings page shows it).

Everything here reports what the application itself knows (job runs, stored data,
worker heartbeat). It cannot see other containers or Unraid, and it never writes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text

from app.auth import require_token
from app.version import COMMIT, VERSION

router = APIRouter(prefix="/v1/status", tags=["status"], dependencies=[Depends(require_token)])


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
