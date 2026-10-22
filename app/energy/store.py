"""Rates in Postgres, and the answers worked out from them."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.energy import rates as R

UPSERT = text(
    """
    INSERT INTO energy_rate (tariff_code, valid_from, valid_to, pence_inc_vat, pence_exc_vat, fetched_at)
    VALUES (:tariff_code, :valid_from, :valid_to, :pence_inc_vat, :pence_exc_vat, now())
    ON CONFLICT (tariff_code, valid_from) DO UPDATE SET valid_to = EXCLUDED.valid_to,
      pence_inc_vat = EXCLUDED.pence_inc_vat, pence_exc_vat = EXCLUDED.pence_exc_vat, fetched_at = now()
    """
)


async def save(engine: AsyncEngine, tariff: str, rows: list[dict[str, Any]]) -> int:
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM energy_rate WHERE tariff_code <> :t"), {"t": tariff})
        if rows:
            await conn.execute(UPSERT, [{**r, "tariff_code": tariff} for r in rows])
        await conn.execute(
            text("DELETE FROM energy_rate WHERE valid_to < now() - interval '3 days'")
        )
    return len(rows)


async def load(
    engine: AsyncEngine, now: datetime | None = None
) -> tuple[str | None, list[R.Period]]:
    now = now or datetime.now(UTC)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT tariff_code, valid_from, valid_to, pence_inc_vat FROM energy_rate ORDER BY valid_from"
                )
            )
        ).all()
    if not rows:
        return None, []
    far = now + timedelta(
        days=30
    )  # an open-ended rate (a fixed tariff) runs until the next one starts
    periods: list[R.Period] = []
    for i, r in enumerate(rows):
        end = r.valid_to or (rows[i + 1].valid_from if i + 1 < len(rows) else far)
        periods.append(R.Period(r.valid_from, end, r.pence_inc_vat))
    return rows[0].tariff_code, R.normalise(periods)


UPSERT_SESSION = text(
    """
    INSERT INTO energy_session (kind, code, start_at, end_at, octopoints_per_kwh, joined, regions, event_type, fetched_at)
    VALUES (:kind, :code, :start_at, :end_at, :octopoints_per_kwh, :joined, :regions, :event_type, now())
    ON CONFLICT (kind, code) DO UPDATE SET start_at = EXCLUDED.start_at, end_at = EXCLUDED.end_at,
      octopoints_per_kwh = EXCLUDED.octopoints_per_kwh, joined = EXCLUDED.joined, regions = EXCLUDED.regions,
      event_type = EXCLUDED.event_type, fetched_at = now()
    """
)


async def save_sessions(engine: AsyncEngine, kinds: list[str], rows: list[dict[str, Any]]) -> int:
    """Replace the stored sessions of `kinds` with `rows` (a complete fetch); other kinds are untouched."""
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM energy_session WHERE kind = ANY(:k)"), {"k": kinds})
        if rows:
            await conn.execute(UPSERT_SESSION, rows)
        await conn.execute(
            text("DELETE FROM energy_session WHERE end_at < now() - interval '2 days'")
        )
    return len(rows)


async def load_sessions(engine: AsyncEngine) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT kind, code, start_at, end_at, octopoints_per_kwh, joined, regions FROM energy_session "
                        "WHERE end_at > now() ORDER BY start_at"
                    )
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]
