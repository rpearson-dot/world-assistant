"""The recorder's tables (migration 0048) and its settings. Rows are only ever added; nothing here updates or deletes a
recorded row (the nightly job drops whole months of raw responses once they are a school year old)."""

from __future__ import annotations

import gzip
import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import text

from app.modules.buslog.schedule import DEFAULT_CALENDAR, DEFAULT_SETTINGS

SETTINGS_KEY, CALENDAR_KEY, UNTIL_KEY = "buslog.settings", "buslog.calendar", "buslog.record_until"
KEEP_RAW_MONTHS = 13  # a school year, and the month either side
TABLES = ("buslog_poll", "buslog_raw", "buslog_prediction", "buslog_position", "buslog_status", "buslog_timetable",
          "buslog_route")  # fmt: skip


def _json(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


async def read_setting(conn: Any, key: str) -> Any:
    return _json((await conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": key})).scalar())


async def write_setting(conn: Any, key: str, value: Any) -> None:
    await conn.execute(text(
        "INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"), {"k": key, "v": json.dumps(value)})  # fmt: skip


async def load(conn: Any) -> tuple[dict[str, Any], dict[str, Any], float | None]:
    """The settings, the school calendar and when "record now" ends (epoch seconds), with defaults."""
    st = {**DEFAULT_SETTINGS, **(await read_setting(conn, SETTINGS_KEY) or {})}
    cal = await read_setting(conn, CALENDAR_KEY) or DEFAULT_CALENDAR
    until = await read_setting(conn, UNTIL_KEY)
    return st, cal, float(until) if until else None


async def poll(conn: Any, *, at: datetime, day: date, line: str, source: str, url: str, status: int | None, ok: bool,
               error: str | None, ms: int, items: int | None, body: bytes | None, content_type: str | None) -> int:
    """One request, however it went, and the response exactly as it came (gzipped)."""
    pid = (await conn.execute(text(
        "INSERT INTO buslog_poll (polled_at, service_day, line, source, url, status, ok, error, ms, items, bytes) "
        "VALUES (:at, :day, :line, :source, :url, :status, :ok, :error, :ms, :items, :bytes) RETURNING id"),
        {"at": at, "day": day, "line": line, "source": source, "url": url, "status": status, "ok": ok,
         "error": (error or None) and error[:500], "ms": ms, "items": items,
         "bytes": len(body) if body is not None else None})).scalar_one()  # fmt: skip
    if body:
        await conn.execute(text("INSERT INTO buslog_raw (poll_id, polled_at, content_type, body) VALUES (:p, :at, :ct, :b)"),
                           {"p": pid, "at": at, "ct": content_type, "b": gzip.compress(body, 6)})
    return pid


async def predictions(conn: Any, pid: int, at: datetime, day: date, line: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    await conn.execute(text(
        "INSERT INTO buslog_prediction (seen_at, poll_id, service_day, line, vehicle, stop, stop_name, direction, "
        "destination, towards, change, expected_at, previous_at, moved_s, time_to_station, computed_at, current_location, fields) "
        "VALUES (:at, :pid, :day, :line, :vehicle, :stop, :stop_name, :direction, :destination, :towards, :change, "
        ":expected_at, :previous_at, :moved_s, :time_to_station, :computed_at, :current_location, CAST(:fields AS jsonb))"),
        [{**{k: r.get(k) for k in ("vehicle", "stop", "stop_name", "direction", "destination", "towards", "change",
                                   "expected_at", "previous_at", "moved_s", "time_to_station", "computed_at", "current_location")},
          "at": at, "pid": pid, "day": day, "line": line, "fields": json.dumps(r.get("fields") or {}, default=str)}
         for r in rows])  # fmt: skip


async def positions(conn: Any, pid: int, at: datetime, day: date, line: str, rows: list[dict[str, Any]]) -> None:
    """Every bus in the answer, with how far it is from the day's route line (none when there is no route yet)."""
    if not rows:
        return
    await conn.execute(text(
        "INSERT INTO buslog_position (seen_at, poll_id, service_day, line, vehicle, recorded_at, valid_until, lat, lon, geog, "
        "bearing, direction, journey_ref, block_ref, origin_ref, origin_name, origin_aimed_departure, destination_ref, "
        "destination_name, operator, occupancy, off_route_m, fields) "
        "SELECT :at, :pid, :day, :line, :vehicle, :recorded_at, :valid_until, :lat, :lon, g, :bearing, :direction, "
        ":journey_ref, :block_ref, :origin_ref, :origin_name, :origin_aimed_departure, :destination_ref, :destination_name, "
        ":operator, :occupancy, (SELECT min(ST_Distance(r.shape, g)) FROM buslog_route r "
        "  WHERE r.line = :line AND r.service_day = :day AND r.shape IS NOT NULL), CAST(:fields AS jsonb) "
        "FROM (SELECT CASE WHEN CAST(:lat AS float8) IS NULL OR CAST(:lon AS float8) IS NULL THEN NULL "
        "  ELSE ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography END AS g) p"),
        [{**{k: r.get(k) for k in ("vehicle", "recorded_at", "valid_until", "lat", "lon", "bearing", "direction",
                                   "journey_ref", "block_ref", "origin_ref", "origin_name", "origin_aimed_departure",
                                   "destination_ref", "destination_name", "operator", "occupancy")},
          "at": at, "pid": pid, "day": day, "line": line, "fields": json.dumps(r.get("fields") or {})} for r in rows])  # fmt: skip


async def status(conn: Any, pid: int, at: datetime, day: date, line: str, s: dict[str, Any]) -> None:
    await conn.execute(text(
        "INSERT INTO buslog_status (seen_at, poll_id, service_day, line, severity, reason, disruptions) "
        "VALUES (:at, :pid, :day, :line, :sev, :reason, CAST(:d AS jsonb))"),
        {"at": at, "pid": pid, "day": day, "line": line, "sev": s["severity"], "reason": s["reason"],
         "d": json.dumps(s["disruptions"], default=str)})  # fmt: skip


async def route(conn: Any, pid: int, at: datetime, day: date, line: str, direction: str, r: dict[str, Any]) -> None:
    await conn.execute(text(
        "INSERT INTO buslog_route (taken_at, poll_id, service_day, line, direction, stops, shape) "
        "VALUES (:at, :pid, :day, :line, :dir, CAST(:stops AS jsonb), "
        "CASE WHEN CAST(:wkt AS text) IS NULL THEN NULL ELSE ST_GeogFromText('SRID=4326;' || :wkt) END)"),
        {"at": at, "pid": pid, "day": day, "line": line, "dir": direction, "stops": json.dumps(r["stops"]),
         "wkt": r["shape_wkt"]})  # fmt: skip


async def timetable(conn: Any, pid: int, at: datetime, day: date, line: str, direction: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    await conn.execute(text(
        "INSERT INTO buslog_timetable (taken_at, poll_id, service_day, line, source, direction, journey, stop, stop_name, "
        "stop_seq, scheduled_at) VALUES (:at, :pid, :day, :line, 'tfl', :dir, :journey, :stop, :stop_name, :stop_seq, :scheduled_at)"),
        [{**r, "at": at, "pid": pid, "day": day, "line": line, "dir": direction} for r in rows if r.get("stop")])  # fmt: skip


async def naptan_points(conn: Any, ids: list[str]) -> dict[str, tuple[float, float, str]]:
    """NaPTAN's position and name for each stop (the naptan_stop table, when NaPTAN has been loaded)."""
    if not ids:
        return {}
    try:
        rows = (await conn.execute(text(
            "SELECT atco, ST_Y(geog::geometry), ST_X(geog::geometry), name FROM naptan_stop WHERE atco = ANY(:ids)"),
            {"ids": ids})).all()  # fmt: skip
    except Exception:  # noqa: BLE001 - no NaPTAN table or rows yet: TfL's positions stand
        return {}
    return {r[0]: (float(r[1]), float(r[2]), r[3]) for r in rows}


async def route_box(conn: Any, line: str) -> tuple[float, float, float, float] | None:
    """The newest route's stops' extent (min lon, min lat, max lon, max lat), for BODS's bounding box."""
    row = (await conn.execute(text(
        "SELECT min((s->>'lon')::float8), min((s->>'lat')::float8), max((s->>'lon')::float8), max((s->>'lat')::float8) "
        "FROM (SELECT stops FROM buslog_route WHERE line = :l ORDER BY taken_at DESC LIMIT 2) r, "
        "jsonb_array_elements(r.stops) s WHERE s->>'lat' IS NOT NULL"), {"l": line})).first()  # fmt: skip
    return tuple(row) if row and row[0] is not None else None  # type: ignore[return-value]


PARTITIONS_SQL = """
DO $$ DECLARE m date := date_trunc('month', now())::date; BEGIN
  FOR i IN 0..12 LOOP
    EXECUTE format('CREATE TABLE IF NOT EXISTS {t}_%s PARTITION OF {t} FOR VALUES FROM (%L) TO (%L)',
                   to_char(m + make_interval(months => i), 'YYYYMM'), m + make_interval(months => i),
                   m + make_interval(months => i + 1));
  END LOOP; END $$"""


async def housekeep(conn: Any) -> dict[str, Any]:
    """Make the coming months' partitions; drop raw-response months older than KEEP_RAW_MONTHS; take what is over out
    of the school calendar. Returns what it did."""
    for t in TABLES:
        await conn.execute(text(PARTITIONS_SQL.replace("{t}", t)))
    old = (await conn.execute(text(
        "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid JOIN pg_class p ON p.oid = i.inhparent "
        "WHERE p.relname = 'buslog_raw' AND c.relname ~ '^buslog_raw_[0-9]{6}$' "
        "AND to_date(right(c.relname, 6), 'YYYYMM') < date_trunc('month', now()) - make_interval(months => :k)"),
        {"k": KEEP_RAW_MONTHS})).scalars().all()  # fmt: skip
    for name in old:
        await conn.execute(text(f'DROP TABLE IF EXISTS "{name}"'))
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.config import get_settings
    from app.modules.buslog.schedule import drop_past

    cal = await read_setting(conn, CALENDAR_KEY)
    gone = 0
    if cal:
        kept = drop_past(cal, datetime.now(ZoneInfo(get_settings().local_tz)).date())
        gone = sum(len(cal.get(k) or []) - len(kept.get(k) or []) for k in ("terms", "breaks", "inset", "notes"))
        if gone:
            await write_setting(conn, CALENDAR_KEY, kept)
    return {"dropped": list(old), "calendar_entries_over": gone}
