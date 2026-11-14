"""The charger's record (migration 0049). Messages, events and sessions are only added to; ocpp_charger holds how
each charger stands now."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text

SETTINGS_KEY = "ocpp.settings"
# mode: "off" (the charger refuses to charge), "on" (charges whenever a car is plugged in), "auto" (Home Assistant's
# charging plan decides through its switch)
MODES = ("off", "on", "auto")
DEFAULTS: dict[str, Any] = {"chargers": [], "meter_every_s": 60, "publish_ha": True, "mode": "auto"}
KEEP_DAYS = 730
_COLS = ("last_seen", "connected", "connected_at", "boot", "booted_at", "status", "error_code", "status_info", "meter",
         "config", "heartbeat_s")  # fmt: skip
_JSON = {"boot", "status_info", "meter", "config"}


def _j(v: Any) -> str:
    return json.dumps(v, default=str)


async def settings(conn: Any) -> dict[str, Any]:
    v = (await conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": SETTINGS_KEY})).scalar()
    v = json.loads(v) if isinstance(v, str) else v
    return {**DEFAULTS, **(v or {})}


async def save_settings(conn: Any, st: dict[str, Any]) -> None:
    await conn.execute(text(
        "INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"), {"k": SETTINGS_KEY, "v": _j(st)})  # fmt: skip


async def message(conn: Any, charger: str, direction: str, kind: int, uid: str | None, action: str | None,
                  payload: Any, raw: str) -> None:
    await conn.execute(text(
        "INSERT INTO ocpp_message (charger, direction, kind, unique_id, action, payload, raw) "
        "VALUES (:c, :d, :k, :u, :a, CAST(:p AS jsonb), :r)"),
        {"c": charger, "d": direction, "k": kind, "u": uid, "a": action, "p": _j(payload), "r": raw})  # fmt: skip


async def event(conn: Any, charger: str, kind: str, words: str, detail: dict[str, Any] | None = None) -> None:
    await conn.execute(text("INSERT INTO ocpp_event (charger, kind, text, detail) VALUES (:c, :k, :t, CAST(:d AS jsonb))"),
                       {"c": charger, "k": kind, "t": words[:500], "d": _j(detail or {})})


async def charger(conn: Any, cid: str, **cols: Any) -> None:
    """Make sure the charger has a row, and set the given columns (a meter merges into the last one)."""
    await conn.execute(text("INSERT INTO ocpp_charger (id) VALUES (:id) ON CONFLICT (id) DO NOTHING"), {"id": cid})
    cols = {k: v for k, v in cols.items() if k in _COLS}
    if not cols:
        return
    sets = []
    for k in cols:
        if k == "meter":
            sets.append("meter = meter || CAST(:meter AS jsonb)")
        elif k in _JSON:
            sets.append(f"{k} = CAST(:{k} AS jsonb)")
        else:
            sets.append(f"{k} = :{k}")
    await conn.execute(text(f"UPDATE ocpp_charger SET {', '.join(sets)} WHERE id = :id"),
                       {"id": cid, **{k: (_j(v) if k in _JSON else v) for k, v in cols.items()}})


async def next_transaction(conn: Any, cid: str) -> int:
    return int((await conn.execute(text("SELECT coalesce(max(transaction_id), 0) + 1 FROM ocpp_session WHERE charger = :c"),
                                   {"c": cid})).scalar_one())


async def open_transaction(conn: Any, cid: str) -> int | None:
    return (await conn.execute(text(
        "SELECT transaction_id FROM ocpp_session WHERE charger = :c AND stopped_at IS NULL ORDER BY started_at DESC LIMIT 1"),
        {"c": cid})).scalar()  # fmt: skip


async def session_start(conn: Any, cid: str, s: dict[str, Any]) -> None:
    await conn.execute(text(
        "INSERT INTO ocpp_session (charger, transaction_id, connector, id_tag, started_at, meter_start_wh) "
        "VALUES (:c, :transaction_id, :connector, :id_tag, CAST(:started_at AS timestamptz), :meter_start_wh) "
        "ON CONFLICT (charger, transaction_id) DO NOTHING"), {"c": cid, **s})  # fmt: skip


async def session_stop(conn: Any, cid: str, s: dict[str, Any]) -> None:
    await conn.execute(text(
        "UPDATE ocpp_session SET stopped_at = CAST(:stopped_at AS timestamptz), meter_stop_wh = :meter_stop_wh, "
        "reason = :reason WHERE charger = :c AND transaction_id = :transaction_id"), {"c": cid, **s})  # fmt: skip


async def session_meter(conn: Any, cid: str, m: dict[str, Any]) -> None:
    await conn.execute(text(
        "UPDATE ocpp_session SET last_meter_wh = coalesce(:e, last_meter_wh), last_power_w = coalesce(:p, last_power_w) "
        "WHERE charger = :c AND transaction_id = :t"),
        {"c": cid, "t": m["transaction_id"], "e": m.get("energy_wh"), "p": m.get("power_w")})  # fmt: skip


async def housekeep(conn: Any) -> int:
    return (await conn.execute(text("DELETE FROM ocpp_message WHERE at < now() - make_interval(days => :d)"),
                               {"d": KEEP_DAYS})).rowcount


async def chargers(conn: Any) -> list[dict[str, Any]]:
    rows = (await conn.execute(text(
        "SELECT id, first_seen, last_seen, connected, connected_at, boot, booted_at, status, error_code, status_info, "
        "meter, config, heartbeat_s FROM ocpp_charger ORDER BY id"))).mappings().all()  # fmt: skip
    return [dict(r) for r in rows]


def as_of(v: datetime | None) -> str | None:
    return v.isoformat() if v else None
