"""The school run's API (/v1/buslog/schoolrun): every bus from the first stop (Worcester Park) towards Kingston in
the morning and what happened to it at the school's stop (Coombe Girls School), the reliability over days, the
stops it looks at, a day's GPS tracks for playback, and a day's recording as one download.

A day is worked out from what was recorded (run.morning) and kept once it is over, so the reliability over a term is
quick; today is always worked out afresh.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth import require_token
from app.jobs import tracked
from app.modules.buslog import capacity, run, store
from app.modules.buslog.api import _day, _tz

router = APIRouter(prefix="/v1/buslog", tags=["buslog"], dependencies=[Depends(require_token)])
KEY = "buslog.schoolrun"
CAPACITY_KEY = "buslog.capacity"
VERSION = 1  # bump when run.morning changes what it finds: kept days are worked out again
DEFAULTS = {"first": "490001346C", "school": "490005569W", "school_other": "490005569E", "by": "08:30"}


async def load_settings(conn: Any) -> dict[str, Any]:
    return {**DEFAULTS, **(await store.read_setting(conn, KEY) or {})}


def _local(day: date, hm: str) -> datetime:
    h, m = (int(x) for x in hm.split(":"))
    return datetime.combine(day, time(h, m), _tz())


async def _routes(conn: Any, line: str, day: date) -> dict[str, dict[str, Any]]:
    rows = (await conn.execute(text(
        "SELECT DISTINCT ON (direction) direction, stops, ST_AsGeoJSON(shape)::jsonb FROM buslog_route "
        "WHERE line = :l AND service_day <= :d ORDER BY direction, service_day DESC, taken_at DESC"), {"l": line, "d": day})).all()  # fmt: skip
    return {r[0]: {"stops": r[1], "shape": r[2]} for r in rows}


def _towards(routes: dict[str, dict[str, Any]], school: str) -> tuple[str, str | None]:
    """Which direction has the school's stop (the way towards Kingston), and the other."""
    for d, r in routes.items():
        if any(s.get("stop") == school for s in r["stops"]):
            return d, next((o for o in routes if o != d), None)
    raise HTTPException(409, "the school's stop is not on the recorded route: check the stops in the school run settings")


def _line(shape: Any) -> list[list[float]] | None:
    """The longest line of a recorded route's shape."""
    if not shape or not shape.get("coordinates"):
        return None
    lines = shape["coordinates"] if shape.get("type") == "MultiLineString" else [shape["coordinates"]]
    return max(lines, key=len)


async def compute(conn: Any, line: str, day: date, st: dict[str, Any], window: tuple[str, str]) -> dict[str, Any]:
    """The school run on one day, from what was recorded."""
    w0, w1 = _local(day, window[0]), _local(day, window[1])
    routes = await _routes(conn, line, day)
    if not routes:
        return {"day": day.isoformat(), "line": line, "recorded": False, "why": "no route recorded for this day",
                "summary": {}, "buses": [], "events": []}  # fmt: skip
    towards, other = _towards(routes, st["school"])
    tt = [dict(r) for r in (await conn.execute(text(
        "SELECT t.journey, t.stop, t.scheduled_at FROM buslog_timetable t "
        "JOIN (SELECT max(taken_at) AS taken_at FROM buslog_timetable WHERE line = :l AND service_day = :d AND direction = :dir) n "
        "ON n.taken_at = t.taken_at WHERE t.line = :l AND t.service_day = :d AND t.direction = :dir"),
        {"l": line, "d": day, "dir": towards})).mappings().all()]  # fmt: skip
    pos: dict[str, list[dict[str, Any]]] = {}
    for r in (await conn.execute(text(
            "SELECT DISTINCT ON (vehicle, recorded_at) vehicle, recorded_at, lat, lon, origin_aimed_departure, destination_name, occupancy, "
            "(SELECT jsonb_object_agg(k, v) FROM jsonb_each_text(fields) e(k, v) WHERE k ~* '(occupancy|capacity)') AS cap "
            "FROM buslog_position WHERE line = :l AND service_day = :d AND seen_at BETWEEN :a AND :b AND lat IS NOT NULL "
            "ORDER BY vehicle, recorded_at, seen_at"),
            {"l": line, "d": day, "a": w0 - timedelta(minutes=30), "b": w1 + timedelta(minutes=75)})).mappings().all():  # fmt: skip
        pos.setdefault(r["vehicle"], []).append({**r, "capacity": capacity.read(r["occupancy"], r["cap"])})
    preds = [dict(r) for r in (await conn.execute(text(
        "SELECT seen_at, vehicle, stop, change, expected_at FROM buslog_prediction "
        "WHERE line = :l AND service_day = :d AND seen_at BETWEEN :a AND :b ORDER BY seen_at"),
        {"l": line, "d": day, "a": w0 - timedelta(minutes=60), "b": w1 + timedelta(minutes=75)})).mappings().all()]  # fmt: skip
    answered = {r[0] for r in (await conn.execute(text(
        "SELECT DISTINCT date_trunc('minute', polled_at) FROM buslog_poll WHERE line = :l AND service_day = :d "
        "AND source = 'bods' AND ok AND polled_at BETWEEN :a AND :b"), {"l": line, "d": day, "a": w0, "b": w1})).all()}  # fmt: skip
    gaps, start = [], None
    m = w0.astimezone(UTC).replace(second=0, microsecond=0)
    while m < w1:
        if m not in answered and start is None:
            start = m
        elif m in answered and start is not None:
            gaps.append((start, m))
            start = None
        m += timedelta(minutes=1)
    if start is not None:
        gaps.append((start, w1))
    status = [dict(r) for r in (await conn.execute(text(
        "SELECT DISTINCT ON (severity, reason) seen_at, severity, reason FROM buslog_status WHERE line = :l AND service_day = :d "
        "AND seen_at BETWEEN :a AND :b AND severity IS NOT NULL AND severity <> 'Good Service' ORDER BY severity, reason, seen_at"),
        {"l": line, "d": day, "a": w0, "b": w1})).mappings().all()]  # fmt: skip
    if not pos and not preds:
        return {"day": day.isoformat(), "line": line, "recorded": False, "why": "nothing was recorded in the window on this day",
                "summary": {}, "buses": [], "events": []}  # fmt: skip
    try:
        out = run.morning(routes[towards]["stops"], _line(routes[towards]["shape"]), routes[other]["stops"] if other else [],
                          tt, pos, preds, st["first"], st["school"], st.get("school_other"), (w0, w1),
                          by=_local(day, st["by"]) if st.get("by") else None, gaps=gaps)  # fmt: skip
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    for s in status:
        out["events"].append({"at": s["seen_at"], "vehicle": None, "kind": "status",
                              "text": f"TfL: {s['severity']}" + (f" ({s['reason']})" if s["reason"] else "")})  # fmt: skip
    out["events"].sort(key=lambda e: e["at"])
    gap_min = sum((b - a).total_seconds() for a, b in gaps) / 60
    out["capacity_check"] = await store.read_setting(conn, CAPACITY_KEY)
    return {"day": day.isoformat(), "line": line, "recorded": True, "window": [w0, w1], "timetable": bool(tt),
            "gaps": [{"from": a, "to": b} for a, b in gaps], "gap_minutes": round(gap_min), "status": status, **out}  # fmt: skip


def _jsonable(v: Any) -> Any:
    return json.loads(json.dumps(v, default=lambda x: x.isoformat() if isinstance(x, datetime | date) else str(x)))


async def day_result(engine: Any, day: date, fresh: bool = False) -> dict[str, Any]:
    """A day's school run: kept once the day's window is well over, else worked out now."""
    async with engine.connect() as conn:
        st_, _, _ = await store.load(conn)
        st = await load_settings(conn)
        if not fresh:
            row = (await conn.execute(text(
                "SELECT result, settings FROM buslog_schoolrun WHERE line = :l AND service_day = :d AND version = :v"),
                {"l": st_["line"], "d": day, "v": VERSION})).first()  # fmt: skip
            if row and store._json(row[1]) == {**st, "window": [st_["start"], st_["end"]]}:
                return store._json(row[0])
        out = _jsonable(await compute(conn, st_["line"], day, st, (st_["start"], st_["end"])))
    over = datetime.now(_tz()) > _local(day, st_["end"]) + timedelta(minutes=90)
    if over and out.get("recorded"):
        async with engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO buslog_schoolrun (line, service_day, version, settings, summary, result) "
                "VALUES (:l, :d, :v, CAST(:s AS jsonb), CAST(:m AS jsonb), CAST(:r AS jsonb)) "
                "ON CONFLICT (line, service_day) DO UPDATE SET computed_at = now(), version = EXCLUDED.version, "
                "settings = EXCLUDED.settings, summary = EXCLUDED.summary, result = EXCLUDED.result"),
                {"l": st_["line"], "d": day, "v": VERSION, "s": json.dumps({**st, "window": [st_["start"], st_["end"]]}),
                 "m": json.dumps(out["summary"]), "r": json.dumps(out)})  # fmt: skip
    return out


@router.get("/schoolrun")
async def schoolrun(request: Request, day: str | None = None, fresh: bool = False) -> dict[str, Any]:
    """The morning's buses from the first stop towards Kingston: timetabled and actual departure, the school's stop
    (timetabled, expected, actual or probable), what happened (reached it, from the other side, turned and where,
    left the route and what it missed), each stop's times, and the moments to mark on the playback."""
    return await day_result(request.app.state.engine, _day(day), fresh)


@router.get("/schoolrun/reliability")
async def reliability(request: Request, days: int = Query(120, ge=1, le=400)) -> dict[str, Any]:
    """Each recorded day's figures (buses recorded, how many made it to the school, in time, turned, skipped, not
    seen) and the totals, newest first."""
    engine = request.app.state.engine
    today = datetime.now(_tz()).date()
    async with engine.connect() as conn:
        st_, _, _ = await store.load(conn)
        recorded = [r[0] for r in (await conn.execute(text(
            "SELECT DISTINCT service_day FROM buslog_poll WHERE line = :l AND source = 'bods' AND ok AND service_day > :d "
            "ORDER BY service_day DESC"), {"l": st_["line"], "d": today - timedelta(days=days)})).all()]  # fmt: skip
    out = []
    for d in recorded:
        r = await day_result(engine, d)
        if r.get("recorded") and r.get("summary", {}).get("recorded"):
            out.append({"day": d.isoformat(), "summary": r["summary"], "gap_minutes": r.get("gap_minutes")})
    return run.reliability(out)


class SchoolRunIn(BaseModel):
    first: str = Field(DEFAULTS["first"], max_length=20, pattern=r"^[0-9A-Za-z]+$")
    school: str = Field(DEFAULTS["school"], max_length=20, pattern=r"^[0-9A-Za-z]+$")
    school_other: str | None = Field(DEFAULTS["school_other"], max_length=20, pattern=r"^[0-9A-Za-z]*$")
    by: str | None = Field(DEFAULTS["by"], pattern=r"^(\d{1,2}:\d{2})?$")


@router.get("/schoolrun/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    """The stops the school run looks at (by NaPTAN id) and when the school must be reached by."""
    async with request.app.state.engine.connect() as conn:
        return await load_settings(conn)


@router.put("/schoolrun/settings")
async def put_settings(request: Request, body: SchoolRunIn) -> dict[str, Any]:
    v = body.model_dump()
    v["school_other"] = v["school_other"] or None
    v["by"] = v["by"] or None
    if v["first"] == v["school"]:
        raise HTTPException(422, "the first stop and the school's stop must differ")
    async with request.app.state.engine.begin() as conn:
        await store.write_setting(conn, KEY, v)
    return v


@router.get("/tracks")
async def tracks(request: Request, day: str | None = None) -> dict[str, Any]:
    """Every bus's GPS positions over a day's window (and half an hour either side), for playback:
    {vehicle: [[epoch seconds, lat, lon, destination, how full (when BODS says)], ...]}."""
    d = _day(day)
    async with request.app.state.engine.connect() as conn:
        st_, _, _ = await store.load(conn)
        a, b = _local(d, st_["start"]) - timedelta(minutes=30), _local(d, st_["end"]) + timedelta(minutes=60)
        rows = (await conn.execute(text(
            "SELECT DISTINCT ON (vehicle, recorded_at) vehicle, recorded_at, lat, lon, destination_name, occupancy, "
            "(SELECT jsonb_object_agg(k, v) FROM jsonb_each_text(fields) e(k, v) WHERE k ~* '(occupancy|capacity)') FROM buslog_position "
            "WHERE line = :l AND service_day = :d AND seen_at BETWEEN :a AND :b AND lat IS NOT NULL ORDER BY vehicle, recorded_at, seen_at"),
            {"l": st_["line"], "d": d, "a": a, "b": b})).all()  # fmt: skip
    out: dict[str, list[Any]] = {}
    for v, at, lat, lon, dest, occ, cap in rows:
        c = capacity.read(occ, cap)
        out.setdefault(v, []).append([round(at.timestamp()), round(lat, 6), round(lon, 6), dest, c["words"] if c else None])
    return {"day": d.isoformat(), "from": a, "to": b, "buses": out}


@router.get("/export")
async def export(request: Request, day: str | None = None) -> Response:
    """A day's recording as one file (gzip JSON): the route, timetable, positions, predictions, status and every
    request's outcome (not the raw answers)."""
    d = _day(day)
    out: dict[str, Any] = {"day": d.isoformat()}
    async with request.app.state.engine.connect() as conn:
        st_, _, _ = await store.load(conn)
        out["line"] = st_["line"]
        for name, sql in (
            ("route", "SELECT direction, taken_at, stops, ST_AsGeoJSON(shape)::jsonb AS shape FROM buslog_route WHERE line = :l AND service_day = :d"),
            ("timetable", "SELECT taken_at, direction, journey, stop, stop_name, stop_seq, scheduled_at FROM buslog_timetable WHERE line = :l AND service_day = :d"),
            ("positions", ("SELECT seen_at, vehicle, recorded_at, lat, lon, bearing, direction, journey_ref, origin_aimed_departure, "
                          "destination_name, off_route_m FROM buslog_position WHERE line = :l AND service_day = :d ORDER BY seen_at")),
            ("predictions", ("SELECT seen_at, vehicle, stop, stop_name, direction, destination, change, expected_at, moved_s "
                            "FROM buslog_prediction WHERE line = :l AND service_day = :d ORDER BY seen_at")),
            ("status", "SELECT seen_at, severity, reason, disruptions FROM buslog_status WHERE line = :l AND service_day = :d ORDER BY seen_at"),
            ("polls", "SELECT polled_at, source, status, ok, error, ms, items FROM buslog_poll WHERE line = :l AND service_day = :d ORDER BY polled_at"),
        ):
            out[name] = [dict(r) for r in (await conn.execute(text(sql), {"l": st_["line"], "d": d})).mappings().all()]
    body = gzip.compress(json.dumps(_jsonable(out)).encode())
    return Response(body, media_type="application/gzip",
                    headers={"Content-Disposition": f'attachment; filename="buslog-{st_["line"]}-{d.isoformat()}.json.gz"'})


async def keep_yesterday(engine: Any) -> dict[str, Any]:
    """Each night: yesterday's school run worked out and kept (for the reliability over time)."""
    d = datetime.now(_tz()).date() - timedelta(days=1)
    r = await day_result(engine, d, fresh=True)
    return {"schoolrun": d.isoformat(), "buses": r.get("summary", {}).get("recorded")}


async def buslog_capacity_check(ctx: dict[str, Any]) -> Any:
    """Each week: has BODS started saying how full the route's buses are? The first time it has, a notification (once);
    from then on the school run shows it (it is read from every position already recorded)."""
    engine = ctx["engine"]

    async def work() -> dict[str, Any]:
        from app import notify

        async with engine.connect() as conn:
            st_, _, _ = await store.load(conn)
            row = (await conn.execute(text(
                "SELECT count(*), count(*) FILTER (WHERE occupancy IS NOT NULL) FROM buslog_position "
                "WHERE line = :l AND seen_at > now() - interval '7 days'"), {"l": st_["line"]})).first()  # fmt: skip
            keys = [r[0] for r in (await conn.execute(text(
                "SELECT DISTINCT k FROM (SELECT fields FROM buslog_position WHERE line = :l AND seen_at > now() - interval '7 days' "
                "ORDER BY seen_at DESC LIMIT 20000) p, jsonb_object_keys(p.fields) k WHERE k ~* '(occupancy|capacity)' LIMIT 30"),
                {"l": st_["line"]})).all()]  # fmt: skip
            state = await store.read_setting(conn, CAPACITY_KEY) or {}
        positions, with_occ = row[0] if row else 0, row[1] if row else 0
        seen = bool(with_occ or keys)
        now = datetime.now(UTC).isoformat()
        state = {**state, "line": st_["line"], "last_checked": now, "positions": positions, "with_occupancy": with_occ,
                 "fields": keys, "sent": seen}  # fmt: skip
        first = seen and not state.get("first_seen")
        if first:
            state["first_seen"] = now
        async with engine.begin() as conn:
            await store.write_setting(conn, CAPACITY_KEY, state)
        if first:
            await notify.send(engine, "buses", f"buslog:capacity:{st_['line']}", f"BODS now says how full the {st_['line']} is",
                              f"{with_occ} of {positions} positions this week carry it. The school run shows it from now on.",
                              click="/buslog")  # fmt: skip
        return {"positions": positions, "with_occupancy": with_occ, "fields": keys, "first_seen": first}

    return await tracked(engine, "buslog_capacity_check", work)
