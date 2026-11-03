"""Database access for the bus tracker."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.integrations.bus.trips import new_state


def _j(v: Any) -> str:
    return json.dumps(v, separators=(",", ":"))


async def upsert_tracker(
    conn: AsyncConnection, tid: str, config: dict[str, Any], enabled: bool
) -> None:
    await conn.execute(
        text(
            "INSERT INTO bus_tracker (id, config, enabled) VALUES (:id, CAST(:c AS jsonb), :e) "
            "ON CONFLICT (id) DO UPDATE SET config = EXCLUDED.config, enabled = EXCLUDED.enabled"
        ),
        {"id": tid, "c": _j(config), "e": enabled},
    )
    # Stops or line may have changed: sequences are re-read and tracking restarts clean.
    await conn.execute(text("DELETE FROM bus_stop WHERE tracker = :id"), {"id": tid})
    await conn.execute(text("DELETE FROM bus_timetable WHERE tracker = :id"), {"id": tid})
    await conn.execute(
        text("UPDATE bus_tracker SET state = CAST(:s AS jsonb) WHERE id = :id"),
        {"id": tid, "s": _j(new_state())},
    )


async def update_config(conn: AsyncConnection, tid: str, config: dict[str, Any]) -> None:
    """Change a tracker's settings (times, say) without resetting its route, timetable or live state."""
    await conn.execute(
        text("UPDATE bus_tracker SET config = CAST(:c AS jsonb) WHERE id = :id"), {"id": tid, "c": _j(config)}
    )


async def list_trackers(conn: AsyncConnection, only_enabled: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT id, config, enabled, state_updated FROM bus_tracker"
    if only_enabled:
        sql += " WHERE enabled"
    return [dict(r._mapping) for r in await conn.execute(text(sql + " ORDER BY id"))]


async def get_tracker(conn: AsyncConnection, tid: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                "SELECT id, config, enabled, state, state_updated FROM bus_tracker WHERE id = :id"
            ),
            {"id": tid},
        )
    ).first()
    return dict(row._mapping) if row else None


async def delete_tracker(conn: AsyncConnection, tid: str) -> bool:
    r = await conn.execute(text("DELETE FROM bus_tracker WHERE id = :id"), {"id": tid})
    return bool(r.rowcount)


async def save_state(conn: AsyncConnection, tid: str, state: dict[str, Any]) -> None:
    await conn.execute(
        text(
            "UPDATE bus_tracker SET state = CAST(:s AS jsonb), state_updated = now() WHERE id = :id"
        ),
        {"id": tid, "s": _j(state)},
    )


async def save_sequences(
    conn: AsyncConnection, tid: str, seqs: dict[str, list[dict[str, str]]]
) -> None:
    await conn.execute(text("DELETE FROM bus_stop WHERE tracker = :id"), {"id": tid})
    rows = [
        {"t": tid, "d": d, "p": i, "s": s["id"], "n": s.get("name", "")}
        for d, stops in seqs.items()
        for i, s in enumerate(stops)
    ]
    if rows:
        await conn.execute(
            text(
                "INSERT INTO bus_stop (tracker, direction, pos, sid, name) VALUES (:t, :d, :p, :s, :n)"
            ),
            rows,
        )


async def load_sequences(conn: AsyncConnection, tid: str) -> list[dict[str, Any]]:
    res = await conn.execute(
        text(
            "SELECT direction, pos, sid, name FROM bus_stop WHERE tracker = :id ORDER BY direction, pos"
        ),
        {"id": tid},
    )
    return [dict(r._mapping) for r in res]


async def get_timetable(
    conn: AsyncConnection, tid: str, sid: str, max_age_days: int = 7
) -> list[str] | None:
    row = (
        await conn.execute(
            text(
                "SELECT times FROM bus_timetable WHERE tracker = :t AND sid = :s AND day_type = 'weekday' "
                "AND fetched_at > now() - :d * interval '1 day'"
            ),
            {"t": tid, "s": sid, "d": max_age_days},
        )
    ).first()
    return list(row[0]) if row else None


async def save_timetable(conn: AsyncConnection, tid: str, sid: str, times: list[str]) -> None:
    await conn.execute(
        text(
            "INSERT INTO bus_timetable (tracker, sid, day_type, times) "
            "VALUES (:t, :s, 'weekday', CAST(:x AS jsonb)) "
            "ON CONFLICT (tracker, sid, day_type) DO UPDATE SET times = EXCLUDED.times, fetched_at = now()"
        ),
        {"t": tid, "s": sid, "x": _j(times)},
    )


async def save_observations(conn: AsyncConnection, tid: str, obs: list[dict[str, Any]]) -> None:
    if not obs:
        return
    await conn.execute(
        text(
            "INSERT INTO bus_segment_obs (tracker, vehicle, from_stop, to_stop, observed_at, seconds) "
            "VALUES (:t, :v, :f, :o, to_timestamp(:at), :s) ON CONFLICT DO NOTHING"
        ),
        [
            {
                "t": tid,
                "v": o["vehicle"],
                "f": o["from"],
                "o": o["to"],
                "at": o["observed_at"],
                "s": o["seconds"],
            }
            for o in obs
        ],
    )


async def save_anomalies(conn: AsyncConnection, tid: str, items: list[dict[str, Any]]) -> None:
    if not items:
        return
    await conn.execute(
        text(
            "INSERT INTO bus_anomaly (tracker, vehicle, type, detail) "
            "VALUES (:t, :v, :ty, CAST(:d AS jsonb))"
        ),
        [
            {
                "t": tid,
                "v": a["vehicle"],
                "ty": a["type"],
                "d": _j({k: v for k, v in a.items() if k not in ("vehicle", "type")}),
            }
            for a in items
        ],
    )


async def ensure_day(conn: AsyncConnection, tid: str, day: date, slots: list[str]) -> None:
    await conn.execute(
        text(
            "INSERT INTO bus_target_day (tracker, day, slots) VALUES (:t, :d, CAST(:s AS jsonb)) "
            "ON CONFLICT (tracker, day) DO UPDATE SET slots = EXCLUDED.slots"
        ),
        {"t": tid, "d": day, "s": _j(slots)},
    )


async def save_explained(conn: AsyncConnection, tid: str, day: date, explained: dict[str, Any]) -> None:
    """Why each missed bus missed (events.explain_misses), rewritten each poll."""
    await conn.execute(
        text("UPDATE bus_target_day SET explained = CAST(:e AS jsonb) WHERE tracker = :t AND day = :d"),
        {"t": tid, "d": day, "e": _j(explained)},
    )


async def save_hits(conn: AsyncConnection, tid: str, day: date, hits: list[dict[str, Any]]) -> None:
    if not hits:
        return
    await conn.execute(
        text(
            "INSERT INTO bus_target_hit (tracker, day, vehicle, board_at, arrive_at, slot, delay_min, via_return) "
            "VALUES (:t, :d, :v, to_timestamp(:b), to_timestamp(:a), :sl, :dm, :vr) ON CONFLICT (tracker, day, vehicle, board_at) DO UPDATE SET slot = EXCLUDED.slot, delay_min = EXCLUDED.delay_min"
        ),
        [
            {
                "t": tid,
                "d": day,
                "v": h["vehicle"],
                "b": h["board_ts"],
                "a": h["arrive_ts"],
                "sl": h.get("slot"),
                "dm": h.get("delay_min"),
                "vr": h["via_return"],
            }
            for h in hits
        ],
    )


async def day_hits(conn: AsyncConnection, tid: str, day: date) -> list[dict[str, Any]]:
    res = await conn.execute(
        text(
            "SELECT vehicle, board_at, arrive_at, slot, delay_min, via_return FROM bus_target_hit "
            "WHERE tracker = :t AND day = :d ORDER BY arrive_at"
        ),
        {"t": tid, "d": day},
    )
    return [dict(r._mapping) for r in res]


async def save_positions(conn: AsyncConnection, tid: str, vehicles: list[dict[str, Any]]) -> int:
    """GPS fixes of followed buses. A fix already stored (BODS refreshes less often than we poll) is skipped."""
    rows = [
        {"t": tid, "v": v["vehicle"], "at": v["recorded_at"], "lat": v["lat"], "lon": v["lon"],
         "b": v.get("bearing"), "d": v.get("destination"), "bd": v.get("direction"),
         "o": v.get("occupancy"), "oc": _j(v.get("occupancy_counts") or {})}
        for v in vehicles if v.get("recorded_at")
    ]  # fmt: skip
    if not rows:
        return 0
    from datetime import datetime

    for r in rows:
        r["at"] = datetime.fromisoformat(r["at"])
    result = await conn.execute(
        text(
            "INSERT INTO bus_position (tracker, vehicle, recorded_at, lat, lon, bearing, destination, bods_direction, "
            "occupancy, occupancy_counts) VALUES (:t, :v, :at, :lat, :lon, :b, :d, :bd, :o, CAST(:oc AS jsonb)) "
            "ON CONFLICT DO NOTHING"
        ),
        rows,
    )
    return int(result.rowcount or 0)


async def occupancy_at(conn: AsyncConnection, tid: str, vehicle: str, at: float) -> dict[str, Any] | None:
    """How full a bus said it was in its GPS report nearest `at` (15 minutes before to 5 after): {occupancy,
    counts} (occupancy None: the report did not say). None when there was no report from it then."""
    row = (await conn.execute(text(
        "SELECT occupancy, occupancy_counts FROM bus_position WHERE tracker = :t AND vehicle = :v "
        "AND recorded_at BETWEEN to_timestamp(:a) - interval '15 minutes' AND to_timestamp(:a) + interval '5 minutes' "
        "ORDER BY abs(extract(epoch FROM recorded_at) - :a) LIMIT 1"), {"t": tid, "v": vehicle, "a": at})).first()  # fmt: skip
    return {"occupancy": row[0], "counts": dict(row[1] or {})} if row else None


async def latest_occupancy(conn: AsyncConnection, tid: str, vehicles: list[str]) -> dict[str, dict[str, Any]]:
    """Each bus's latest GPS report in the last 10 minutes: {vehicle: {occupancy, counts, recorded_at}}."""
    if not vehicles:
        return {}
    res = await conn.execute(text(
        "SELECT DISTINCT ON (vehicle) vehicle, occupancy, occupancy_counts, recorded_at FROM bus_position "
        "WHERE tracker = :t AND vehicle = ANY(:v) AND recorded_at > now() - interval '10 minutes' "
        "ORDER BY vehicle, recorded_at DESC"), {"t": tid, "v": vehicles})  # fmt: skip
    return {r[0]: {"occupancy": r[1], "counts": dict(r[2] or {}), "recorded_at": r[3].isoformat()} for r in res}


async def occupancy_summary(conn: AsyncConnection, tid: str, since: date) -> dict[str, Any]:
    """Whether this route's buses share how full they are: GPS reports kept since `since`, how many said, and
    the last time one did."""
    row = (await conn.execute(text(
        "SELECT count(*), count(occupancy), max(recorded_at) FILTER (WHERE occupancy IS NOT NULL) FROM bus_position "
        "WHERE tracker = :t AND recorded_at >= :s"), {"t": tid, "s": since})).first()  # fmt: skip
    reports, shared, last = (row[0], row[1], row[2]) if row else (0, 0, None)
    return {"reports": reports, "with_occupancy": shared, "last_shared": last.isoformat() if last else None,
            "shared": bool(shared)}  # fmt: skip


async def prune_positions(conn: AsyncConnection, days: int) -> None:
    await conn.execute(
        text("DELETE FROM bus_position WHERE recorded_at < now() - make_interval(days => :d)"), {"d": days}
    )


async def stop_coords(conn: AsyncConnection, sids: list[str]) -> dict[str, tuple[float, float]]:
    """(lat, lon) of each stop that the national stop register (NaPTAN) knows."""
    if not sids:
        return {}
    rows = await conn.execute(text("SELECT atco, lat, lon FROM naptan_stop WHERE atco = ANY(:s)"), {"s": sids})
    return {r[0]: (float(r[1]), float(r[2])) for r in rows}
