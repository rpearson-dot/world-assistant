"""Record every scheduled job run in Postgres so the status page can show it."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger("wa.jobs")

KEEP_DAYS = 14
_SKIPPED = {"disabled", "no key", "up to date"}


async def tracked(
    engine: AsyncEngine, name: str, work: Callable[[], Awaitable[Any]]
) -> Any:
    """Run `work`, writing a job_run row before and after. Errors are re-raised."""
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM job_run WHERE started_at < now() - make_interval(days => :d)"),
            {"d": KEEP_DAYS},
        )
        run_id = (
            await conn.execute(
                text("INSERT INTO job_run (job) VALUES (:job) RETURNING id"), {"job": name}
            )
        ).scalar_one()
    try:
        result = await work()
    except Exception as exc:
        await _finish(engine, run_id, "error", None, f"{type(exc).__name__}: {exc}")
        raise
    status = "skipped" if isinstance(result, str) and result in _SKIPPED else "ok"
    await _finish(engine, run_id, status, result, None)
    return result


async def _finish(
    engine: AsyncEngine, run_id: int, status: str, result: Any, error: str | None
) -> None:
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE job_run SET finished_at = now(), status = :status,
                      result = CAST(:result AS jsonb), error = :error WHERE id = :id
                    """
                ),
                {"id": run_id, "status": status, "error": error,
                 "result": json.dumps(result, default=str)},
            )  # fmt: skip
    except Exception:
        log.exception("could not record job result for run %s", run_id)
