"""The bus route recorder's API (/v1/buslog): what it is doing, its settings and school calendar, "record now", and the
recorded route, bus positions, requests and raw responses."""

from __future__ import annotations

import gzip
import json
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from app import bankholidays
from app.auth import require_token
from app.config import get_settings
from app.modules.buslog import store
from app.modules.buslog.schedule import (
    DEFAULT_CALENDAR,
    check_calendar,
    check_settings,
    recording,
    school_day,
)

router = APIRouter(prefix="/v1/buslog", tags=["buslog"], dependencies=[Depends(require_token)])


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().local_tz)


def _day(v: str | None) -> date:
    if not v:
        return datetime.now(_tz()).date()
    try:
        return date.fromisoformat(v)
    except ValueError as exc:
        raise HTTPException(422, "day: YYYY-MM-DD") from exc


def _next_days(cal: dict[str, Any], st: dict[str, Any], start: date, n: int = 3) -> list[dict[str, str]]:
    out, d = [], start
    for _ in range(400):
        ok, why = school_day(d, cal, bankholidays.is_bank_holiday(d))
        if ok or st.get("every_day"):
            out.append({"day": d.isoformat(), "why": why if ok else "every day (test)"})
            if len(out) >= n:
                break
        d += timedelta(days=1)
    return out


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    """Whether it is recording now and why, the next recording days, and how today's requests went per source."""
    engine = request.app.state.engine
    now = datetime.now(_tz())
    async with engine.connect() as conn:
        st, cal, until = await store.load(conn)
        today = (await conn.execute(text(
            "SELECT source, count(*) FILTER (WHERE ok AND coalesce(items, 0) > 0), count(*) FILTER (WHERE NOT ok), "
            "count(*) FILTER (WHERE ok AND coalesce(items, 0) = 0), max(polled_at), max(polled_at) FILTER (WHERE NOT ok), "
            "round(avg(ms)) FROM buslog_poll WHERE line = :l AND service_day = :d GROUP BY source ORDER BY source"),
            {"l": st["line"], "d": now.date()})).all()  # fmt: skip
        last_error = (await conn.execute(text(
            "SELECT polled_at, source, error FROM buslog_poll WHERE line = :l AND NOT ok ORDER BY polled_at DESC LIMIT 1"),
            {"l": st["line"]})).first()  # fmt: skip
        days = (await conn.execute(text(
            "SELECT service_day, count(*) FROM buslog_poll WHERE line = :l GROUP BY service_day ORDER BY service_day DESC LIMIT 30"),
            {"l": st["line"]})).all()  # fmt: skip
    await bankholidays.ensure(engine)
    on, why = recording(now, st, cal, bankholidays.is_bank_holiday(now.date()), until)
    worker = None
    try:
        raw = await request.app.state.redis.get("wa:buslog:state")
        worker = json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 - shown as unknown
        worker = None
    s = get_settings()
    return {
        "settings": st, "recording": on, "why": why,
        "record_until": datetime.fromtimestamp(until, UTC).isoformat() if until and until > time.time() else None,
        "worker": worker, "keys": {"tfl": bool(s.tfl_app_key), "bods": bool(s.bods_api_key)},
        "today": {"day": now.date().isoformat(), "school_day": school_day(now.date(), cal, bankholidays.is_bank_holiday(now.date())),
                  "sources": [{"source": r[0], "ok": r[1], "failed": r[2], "empty": r[3], "last": r[4], "last_failed": r[5],
                               "avg_ms": r[6]} for r in today]},
        "last_error": {"at": last_error[0], "source": last_error[1], "error": last_error[2]} if last_error else None,
        "next_days": _next_days(cal, st, now.date() + timedelta(days=1)),
        "days": [{"day": r[0].isoformat(), "requests": r[1]} for r in days],
    }  # fmt: skip


class SettingsIn(BaseModel):
    line: str = Field("213", max_length=8)
    start: str = Field("07:00", pattern=r"^\d{1,2}:\d{2}$")
    end: str = Field("09:00", pattern=r"^\d{1,2}:\d{2}$")
    every_day: bool = False


@router.get("/settings")
async def get_settings_(request: Request) -> dict[str, Any]:
    async with request.app.state.engine.connect() as conn:
        st, cal, _ = await store.load(conn)
    return {"settings": st, "calendar": cal}


@router.put("/settings")
async def put_settings(request: Request, body: SettingsIn) -> dict[str, Any]:
    """The route, the window, and the test switch that records every day (weekends and holidays too)."""
    try:
        st = check_settings(body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    async with request.app.state.engine.begin() as conn:
        await store.write_setting(conn, store.SETTINGS_KEY, st)
    return st


@router.get("/calendar")
async def get_calendar(request: Request) -> dict[str, Any]:
    async with request.app.state.engine.connect() as conn:
        return await store.read_setting(conn, store.CALENDAR_KEY) or DEFAULT_CALENDAR


@router.put("/calendar")
async def put_calendar(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """The school's terms, holidays and INSET days: {school, terms: [{name, from, to}], breaks: [...], inset: [dates], notes}."""
    try:
        cal = check_calendar(body)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, f"calendar: {exc}") from exc
    async with request.app.state.engine.begin() as conn:
        await store.write_setting(conn, store.CALENDAR_KEY, cal)
    return cal


class RecordNow(BaseModel):
    minutes: int = Field(30, ge=1, le=240)


@router.post("/record-now")
async def record_now(request: Request, body: RecordNow) -> dict[str, Any]:
    """Record from the next minute for a while, whatever the day and time (the worker starts within a minute)."""
    until = time.time() + body.minutes * 60
    async with request.app.state.engine.begin() as conn:
        await store.write_setting(conn, store.UNTIL_KEY, until)
    return {"record_until": datetime.fromtimestamp(until, UTC).isoformat()}


@router.delete("/record-now")
async def stop_now(request: Request) -> dict[str, Any]:
    async with request.app.state.engine.begin() as conn:
        await store.write_setting(conn, store.UNTIL_KEY, None)
    return {"record_until": None}


@router.get("/route")
async def route(request: Request, day: str | None = None) -> dict[str, Any]:
    """The route as recorded on a day (or the newest before it): each direction's stops in order, and its line."""
    d = _day(day)
    async with request.app.state.engine.connect() as conn:
        st, _, _ = await store.load(conn)
        rows = (await conn.execute(text(
            "SELECT DISTINCT ON (direction) direction, service_day, taken_at, stops, ST_AsGeoJSON(shape)::jsonb "
            "FROM buslog_route WHERE line = :l AND service_day <= :d ORDER BY direction, service_day DESC, taken_at DESC"),
            {"l": st["line"], "d": d})).all()  # fmt: skip
    return {"line": st["line"], "directions": [
        {"direction": r[0], "day": r[1].isoformat(), "taken_at": r[2], "stops": r[3], "shape": r[4]} for r in rows]}


@router.get("/positions")
async def positions(request: Request, day: str | None = None, at: str | None = None,
                    within_s: int = Query(180, ge=10, le=1800)) -> dict[str, Any]:
    """Each bus's last BODS position at a moment (default now) within `within_s` before it, and how old it was then."""
    d = _day(day)
    try:
        moment = datetime.fromisoformat(at) if at else datetime.now(UTC)
    except ValueError as exc:
        raise HTTPException(422, "at: an ISO time") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_tz())
    async with request.app.state.engine.connect() as conn:
        st, _, _ = await store.load(conn)
        rows = (await conn.execute(text(
            "SELECT DISTINCT ON (vehicle) vehicle, seen_at, recorded_at, lat, lon, bearing, direction, destination_name, "
            "journey_ref, operator, occupancy, off_route_m FROM buslog_position "
            "WHERE line = :l AND service_day = :d AND seen_at <= :m AND seen_at > :m - make_interval(secs => :w) "
            "ORDER BY vehicle, seen_at DESC"), {"l": st["line"], "d": d, "m": moment, "w": within_s})).all()  # fmt: skip
        polls = (await conn.execute(text(
            "SELECT max(polled_at) FILTER (WHERE ok), max(polled_at) FROM buslog_poll "
            "WHERE line = :l AND service_day = :d AND source = 'bods' AND polled_at <= :m"),
            {"l": st["line"], "d": d, "m": moment})).first()  # fmt: skip
    return {"line": st["line"], "at": moment, "last_ok_poll": polls[0] if polls else None, "last_poll": polls[1] if polls else None,
            "buses": [{"vehicle": r[0], "seen_at": r[1], "recorded_at": r[2], "lat": r[3], "lon": r[4], "bearing": r[5],
                       "direction": r[6], "destination": r[7], "journey": r[8], "operator": r[9], "occupancy": r[10],
                       "off_route_m": round(r[11]) if r[11] is not None else None,
                       "age_s": round((moment - r[2]).total_seconds()) if r[2] else None} for r in rows]}  # fmt: skip


@router.get("/polls")
async def polls(request: Request, day: str | None = None) -> dict[str, Any]:
    """Every minute of a day, per source: requests that answered with data, answered empty, or failed. A gap in the
    record shows here as a failed (or missing) request, never as a bus that did not come."""
    d = _day(day)
    async with request.app.state.engine.connect() as conn:
        st, _, _ = await store.load(conn)
        rows = (await conn.execute(text(
            "SELECT date_trunc('minute', polled_at), source, count(*) FILTER (WHERE ok AND coalesce(items, 0) > 0), "
            "count(*) FILTER (WHERE ok AND coalesce(items, 0) = 0), count(*) FILTER (WHERE NOT ok), "
            "string_agg(DISTINCT error, '; ') FILTER (WHERE NOT ok) FROM buslog_poll "
            "WHERE line = :l AND service_day = :d GROUP BY 1, 2 ORDER BY 1, 2"), {"l": st["line"], "d": d})).all()  # fmt: skip
    return {"line": st["line"], "day": d.isoformat(), "minutes": [
        {"minute": r[0], "source": r[1], "ok": r[2], "empty": r[3], "failed": r[4], "errors": r[5]} for r in rows]}


@router.get("/predictions")
async def predictions(request: Request, day: str | None = None, stop: str | None = None, vehicle: str | None = None,
                      limit: int = Query(500, ge=1, le=5000)) -> dict[str, Any]:
    """TfL's prediction changes on a day: new, moved (by how much) and gone, for one stop or bus if given."""
    d = _day(day)
    q = ("SELECT seen_at, vehicle, stop, stop_name, direction, change, expected_at, previous_at, moved_s, poll_id "
         "FROM buslog_prediction WHERE line = :l AND service_day = :d")
    async with request.app.state.engine.connect() as conn:
        st, _, _ = await store.load(conn)
        args: dict[str, Any] = {"l": st["line"], "d": d, "n": limit}
        if stop:
            q += " AND stop = :s"
            args["s"] = stop
        if vehicle:
            q += " AND vehicle = :v"
            args["v"] = vehicle
        rows = (await conn.execute(text(q + " ORDER BY seen_at LIMIT :n"), args)).all()
    return {"line": st["line"], "day": d.isoformat(), "changes": [
        {"seen_at": r[0], "vehicle": r[1], "stop": r[2], "stop_name": r[3], "direction": r[4], "change": r[5],
         "expected_at": r[6], "previous_at": r[7], "moved_s": r[8], "poll": r[9]} for r in rows]}  # fmt: skip


@router.get("/raw/{poll_id}")
async def raw(request: Request, poll_id: int) -> Response:
    """A request's response exactly as it came (kept a school year)."""
    async with request.app.state.engine.connect() as conn:
        row = (await conn.execute(text("SELECT content_type, body FROM buslog_raw WHERE poll_id = :p"), {"p": poll_id})).first()
    if row is None:
        raise HTTPException(404, "no stored response for that request (failed before an answer, or older than a school year)")
    # always a download, never rendered: an upstream error page is someone else's HTML
    ext = "xml" if "xml" in (row[0] or "") else "json" if "json" in (row[0] or "") else "txt"
    return Response(gzip.decompress(row[1]), media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="buslog-{poll_id}.{ext}"',
                             "X-Content-Type-Options": "nosniff"})


# --- a stop's next hour, a bus against its timetable, and taking the timetable now -------------------------------


async def _moment(at: str | None) -> datetime:
    try:
        m = datetime.fromisoformat(at) if at else datetime.now(UTC)
    except ValueError as exc:
        raise HTTPException(422, "at: an ISO time") from exc
    return m if m.tzinfo else m.replace(tzinfo=_tz())


async def _context(conn: Any, line: str, d: date, at: datetime) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]], datetime | None]:
    """The route's stops by direction, the day's timetable (as last taken), TfL's predictions standing at `at`, and
    when the timetable was taken."""
    routes = {r[0]: r[1] for r in (await conn.execute(text(
        "SELECT DISTINCT ON (direction) direction, stops FROM buslog_route WHERE line = :l AND service_day <= :d "
        "ORDER BY direction, service_day DESC, taken_at DESC"), {"l": line, "d": d})).all()}  # fmt: skip
    tt = (await conn.execute(text(
        "SELECT t.direction, t.journey, t.stop, t.stop_name, t.scheduled_at, t.taken_at FROM buslog_timetable t "
        "JOIN (SELECT direction, max(taken_at) AS taken_at FROM buslog_timetable WHERE line = :l AND service_day = :d "
        "      GROUP BY direction) n ON n.direction = t.direction AND n.taken_at = t.taken_at "
        "WHERE t.line = :l AND t.service_day = :d"), {"l": line, "d": d})).mappings().all()  # fmt: skip
    preds = (await conn.execute(text(
        "SELECT * FROM (SELECT DISTINCT ON (vehicle, stop) vehicle, stop, stop_name, direction, destination, change, "
        "expected_at, seen_at FROM buslog_prediction WHERE line = :l AND service_day = :d AND seen_at <= :at "
        "AND seen_at > :at - interval '3 hours' ORDER BY vehicle, stop, seen_at DESC) p "
        "WHERE change <> 'gone' AND expected_at > :at - interval '2 minutes'"), {"l": line, "d": d, "at": at})).mappings().all()  # fmt: skip
    taken = max((r["taken_at"] for r in tt), default=None)
    return routes, [dict(r) for r in tt], [dict(r) for r in preds], taken


def _ends(routes: dict[str, list[dict[str, Any]]]) -> dict[str, str]:
    return {d: (s[-1].get("name") or s[-1].get("stop")) for d, s in routes.items() if s}


@router.get("/stop/{stop}")
async def stop_hour(request: Request, stop: str, day: str | None = None, at: str | None = None) -> dict[str, Any]:
    """A stop's next hour: the buses the timetable has there, and the ones TfL expects (as it stood at `at`)."""
    from app.modules.buslog.compare import stop_hour as hour

    d, moment = _day(day), await _moment(at)
    async with request.app.state.engine.connect() as conn:
        st, _, _ = await store.load(conn)
        routes, tt, preds, taken = await _context(conn, st["line"], d, moment)
    name = next((s.get("name") for ss in routes.values() for s in ss if s.get("stop") == stop), None)
    out = hour(stop, moment, tt, preds, _ends(routes))
    return {**out, "name": name, "line": st["line"], "timetable_taken_at": taken}


@router.get("/vehicle/{vehicle}")
async def vehicle_run(request: Request, vehicle: str, day: str | None = None, at: str | None = None) -> dict[str, Any]:
    """A bus's remaining stops to the end of its route, as TfL expected at `at`, against the timetable: on time,
    early or late at each, and by how much."""
    from app.modules.buslog.compare import bus_run

    d, moment = _day(day), await _moment(at)
    async with request.app.state.engine.connect() as conn:
        st, _, _ = await store.load(conn)
        routes, tt, preds, taken = await _context(conn, st["line"], d, moment)
    def norm(v: str) -> str:  # "LJ16 EZX" and "lj16ezx" are the same bus
        return v.replace(" ", "").upper()

    mine = [p for p in preds if norm(p["vehicle"]) == norm(vehicle)]
    direction = mine[0]["direction"] if mine else None
    out = bus_run(vehicle, mine, routes.get(direction or "", []), tt)
    if not out["destination"] and direction:
        out["destination"] = _ends(routes).get(direction)
    return {**out, "line": st["line"], "at": moment, "timetable_taken_at": taken,
            "note": None if mine else "TfL has no prediction for this bus at that time (it may be out of service, or "
                                      "TfL and BODS name it differently)"}  # fmt: skip


@router.post("/schedule-now")
async def schedule_now(request: Request) -> dict[str, Any]:
    """Take today's route and timetable from TfL now (as the recorder does before the window), kept like any other
    request. Takes a few seconds."""
    import httpx

    from app.modules.buslog.collect import Recorder

    engine = request.app.state.engine
    async with engine.connect() as conn:
        st, _, _ = await store.load(conn)
    day = datetime.now(_tz()).date()
    async with httpx.AsyncClient(follow_redirects=True, headers={"User-Agent": "WorldAssistant bus log"}) as client:
        rec = Recorder(engine, request.app.state.redis, client, st["line"], day)
        out = await rec.prepare()
    failed = {k: v for k, v in rec.counts.items() if v[1]}
    return {"day": day.isoformat(), "line": st["line"], "taken": out,
            "requests": {k: {"ok": v[0], "failed": v[1], "empty": v[2]} for k, v in rec.counts.items()},
            "ok": not failed and bool(out.get("outbound") or out.get("inbound"))}
