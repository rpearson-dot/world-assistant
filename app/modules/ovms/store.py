"""The car's OVMS record (migration 0050) and settings."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

SETTINGS_KEY = "ovms.settings"
# "ovms/+/+/": every vehicle of every user on the broker, as OVMS publishes by default (ovms/<user>/<vehicle id>/)
DEFAULTS: dict[str, Any] = {"enabled": True, "prefix": "ovms/+/+/", "vehicles": {}}
KEEP_DAYS = 730


async def _get(conn: Any, key: str) -> Any:
    v = (await conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": key})).scalar()
    return json.loads(v) if isinstance(v, str) else v


async def _put(conn: Any, key: str, value: Any) -> None:
    await conn.execute(text(
        "INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"), {"k": key, "v": json.dumps(value)})  # fmt: skip


async def settings(conn: Any) -> dict[str, Any]:
    return {**DEFAULTS, **(await _get(conn, SETTINGS_KEY) or {})}


async def save_settings(conn: Any, st: dict[str, Any]) -> None:
    await _put(conn, SETTINGS_KEY, st)


async def notice(conn: Any, vehicle: str, kind: str, subtype: str | None, words: str) -> int:
    return int((await conn.execute(text(
        "INSERT INTO ovms_notice (vehicle, kind, subtype, text) VALUES (:v, :k, :s, :t) RETURNING id"),
        {"v": vehicle, "k": kind, "s": subtype or None, "t": words})).scalar_one())  # fmt: skip


async def latest(conn: Any, rows: list[tuple[str, str, str, datetime]]) -> None:
    if rows:
        await conn.execute(text(
            "INSERT INTO ovms_metric (vehicle, metric, value, updated_at) VALUES (:v, :m, :val, :at) "
            "ON CONFLICT (vehicle, metric) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at"),
            [{"v": v, "m": m, "val": val, "at": at} for v, m, val, at in rows])  # fmt: skip


async def history(conn: Any, rows: list[tuple[str, str, str, datetime]]) -> None:
    if rows:
        await conn.execute(text("INSERT INTO ovms_metric_log (vehicle, metric, value, at) VALUES (:v, :m, :val, :at)"),
                           [{"v": v, "m": m, "val": val, "at": at} for v, m, val, at in rows])


async def metrics(conn: Any, vehicle: str) -> dict[str, tuple[str | None, datetime]]:
    rows = (await conn.execute(text("SELECT metric, value, updated_at FROM ovms_metric WHERE vehicle = :v"), {"v": vehicle})).all()
    return {r[0]: (r[1], r[2]) for r in rows}


async def vehicles(conn: Any) -> list[str]:
    return list((await conn.execute(text("SELECT DISTINCT vehicle FROM ovms_metric ORDER BY 1"))).scalars().all())


async def warnings_now(conn: Any, vehicle: str, keys: list[str]) -> dict[str, str]:
    """Record which warnings stand now; return the ones that have just started, with when (their notification key)."""
    k = f"ovms.active.{vehicle}"
    before: dict[str, str] = await _get(conn, k) or {}
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M")
    now = {key: before.get(key, stamp) for key in keys}
    if now != before:
        await _put(conn, k, now)
    return {key: v for key, v in now.items() if key not in before}


async def notices(conn: Any, vehicle: str, days: int = 30, limit: int = 200) -> list[dict[str, Any]]:
    rows = (await conn.execute(text(
        "SELECT at, kind, subtype, text FROM ovms_notice WHERE vehicle = :v AND at > now() - make_interval(days => :d) "
        "ORDER BY at DESC LIMIT :n"), {"v": vehicle, "d": days, "n": limit})).all()  # fmt: skip
    return [{"at": r[0], "kind": r[1], "subtype": r[2], "text": r[3]} for r in rows]


async def housekeep(conn: Any) -> dict[str, int]:
    a = (await conn.execute(text("DELETE FROM ovms_metric_log WHERE at < now() - make_interval(days => :d)"), {"d": KEEP_DAYS})).rowcount
    b = (await conn.execute(text("DELETE FROM ovms_notice WHERE at < now() - make_interval(days => :d)"), {"d": KEEP_DAYS})).rowcount
    return {"history_deleted": a, "notices_deleted": b}
