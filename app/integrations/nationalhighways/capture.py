"""Temporary capture of raw National Highways responses into Postgres.

Purpose: learn what the live payloads really look like (sizes, page counts,
how variable speed limits and closures appear on our corridor) before writing
the normaliser. Pages are stored verbatim in `raw_capture`, kept for a few days,
and can be exported to files for inspection.

Run once by hand (inside the worker container):

    python -m app.integrations.nationalhighways.capture
    python -m app.integrations.nationalhighways.capture --export /tmp/nh
    python -m app.integrations.nationalhighways.capture --no-fetch --export /tmp/nh
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import Settings, get_settings
from app.integrations.nationalhighways.client import (
    CLOSURES,
    SPEED_MANAGED_AREAS,
    VMS,
    Limiter,
    NationalHighwaysClient,
    Page,
    iso,
)
from app.jobs import tracked
from app.logging import configure_logging
from app.ratelimit import RateLimitExceeded, SlidingWindowLimiter

log = logging.getLogger("wa.nh.capture")


@dataclass(frozen=True)
class CaptureCall:
    name: str
    path: str
    params: Callable[[datetime, Settings], dict[str, str]]
    max_pages: int


def _past(hours: int) -> Callable[[datetime, Settings], dict[str, str]]:
    return lambda now, _s: {"startDateTime": iso(now - timedelta(hours=hours)), "endDateTime": iso(now)}


def _ahead(days: int) -> Callable[[datetime, Settings], dict[str, str]]:
    return lambda now, _s: {"startDateTime": iso(now), "endDateTime": iso(now + timedelta(days=days))}


# Live-ish calls first, large nationwide "planned" pulls last and page-capped.
# Unplanned windows look back 24h so events that began earlier today but are
# still active have a chance of being returned (unverified until we see data).
PLAN: list[CaptureCall] = [
    CaptureCall(
        "closures_unplanned", CLOSURES,
        lambda now, s: {"closureType": "unplanned", **_past(24)(now, s)}, 10,
    ),
    CaptureCall(
        "speed_unplanned", SPEED_MANAGED_AREAS,
        lambda now, s: {"speedRestrictionType": "unplanned", **_past(24)(now, s)}, 10,
    ),
    CaptureCall("vms_corridor", VMS, lambda _now, s: {"bBox": s.nh_vms_bbox}, 10),
    CaptureCall(
        "closures_planned", CLOSURES,
        lambda now, s: {"closureType": "planned", **_ahead(7)(now, s)}, 3,
    ),
    CaptureCall(
        "speed_planned", SPEED_MANAGED_AREAS,
        lambda now, s: {"speedRestrictionType": "planned", **_ahead(7)(now, s)}, 3,
    ),
]  # fmt: skip

_INSERT = text(
    """
    INSERT INTO raw_capture
      (run_id, call_name, page_index, request_path, request_params, status_code,
       x_next, elapsed_ms, body_bytes, body, body_text, error)
    VALUES
      (:run_id, :call_name, :page_index, :request_path, CAST(:request_params AS jsonb),
       :status_code, :x_next, :elapsed_ms, :body_bytes, CAST(:body AS jsonb),
       :body_text, :error)
    """
)


def _clean(value: str | None) -> str | None:
    # Postgres rejects NUL characters and the \u0000 JSON escape.
    return None if value is None else value.replace("\x00", "").replace("\\u0000", "")


async def _store(engine: AsyncEngine, run_id: str, call: CaptureCall, page: Page) -> None:
    body = _clean(json.dumps(page.json)) if page.json is not None else None
    values: dict[str, Any] = {
        "run_id": run_id,
        "call_name": call.name,
        "page_index": page.index,
        "request_path": page.path,
        "request_params": json.dumps(page.params),
        "status_code": page.status_code,
        "x_next": page.x_next,
        "elapsed_ms": page.elapsed_ms,
        "body_bytes": page.body_bytes,
        "body": body,
        "body_text": _clean(page.text),
        "error": page.error,
    }
    async with engine.begin() as conn:
        await conn.execute(_INSERT, values)


async def run_capture(
    settings: Settings,
    engine: AsyncEngine,
    client: NationalHighwaysClient,
    *,
    run_id: str | None = None,
    only: set[str] | None = None,
) -> str:
    """Run the capture plan once and return the run id."""
    run_id = run_id or str(uuid.uuid4())
    now = datetime.now(UTC)
    for call in PLAN:
        if only and call.name not in only:
            continue
        params = call.params(now, settings)
        try:
            async for page in client.pages(call.path, params, max_pages=call.max_pages):
                await _store(engine, run_id, call, page)
                log.info(
                    "captured %s page %d: status=%s bytes=%d next=%s",
                    call.name, page.index, page.status_code, page.body_bytes, bool(page.x_next),
                )  # fmt: skip
                if page.rate_limited:
                    log.warning("upstream returned 429; stopping capture run %s", run_id)
                    return run_id
        except RateLimitExceeded:
            log.warning("local rate limit budget exhausted; stopping capture run %s", run_id)
            return run_id
    return run_id


async def purge_old(engine: AsyncEngine, days: int) -> int:
    async with engine.begin() as conn:
        result = await conn.execute(
            text("DELETE FROM raw_capture WHERE captured_at < now() - make_interval(days => :d)"),
            {"d": days},
        )
        return result.rowcount or 0


async def latest_run_id(engine: AsyncEngine) -> str | None:
    async with engine.connect() as conn:
        row = (await conn.execute(text("SELECT run_id FROM raw_capture ORDER BY id DESC LIMIT 1"))).first()
        return str(row[0]) if row else None


async def summarize(engine: AsyncEngine, run_id: str) -> list[dict[str, Any]]:
    query = text(
        """
        SELECT call_name,
               count(*) AS pages,
               coalesce(sum(body_bytes), 0) AS bytes,
               string_agg(DISTINCT coalesce(status_code::text, 'none'), ',') AS statuses,
               (array_agg(x_next ORDER BY page_index DESC))[1] IS NOT NULL AS truncated,
               max(error) AS error
        FROM raw_capture WHERE run_id = :r
        GROUP BY call_name ORDER BY min(id)
        """
    )
    async with engine.connect() as conn:
        return [dict(row._mapping) for row in await conn.execute(query, {"r": run_id})]


async def export_run(engine: AsyncEngine, run_id: str, directory: Path) -> int:
    """Write each stored page of a run to `directory` plus a manifest.json."""
    query = text(
        """
        SELECT call_name, page_index, request_path, request_params, status_code, x_next,
               body_bytes, body, body_text, error
        FROM raw_capture WHERE run_id = :r ORDER BY id
        """
    )
    directory.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    async with engine.connect() as conn:
        rows = (await conn.execute(query, {"r": run_id})).mappings().all()
    for row in rows:
        stem = f"{row['call_name']}-p{row['page_index']:02d}"
        if row["body"] is not None:
            name, content = f"{stem}.json", json.dumps(row["body"], indent=1)
        else:
            name, content = f"{stem}.txt", row["body_text"] or ""
        (directory / name).write_text(content, encoding="utf-8")
        manifest.append(
            {
                "file": name,
                "call": row["call_name"],
                "page": row["page_index"],
                "path": row["request_path"],
                "params": row["request_params"],
                "status": row["status_code"],
                "x_next": row["x_next"],
                "bytes": row["body_bytes"],
                "error": row["error"],
            }
        )
    manifest_doc = {"run_id": run_id, "files": manifest}
    (directory / "manifest.json").write_text(json.dumps(manifest_doc, indent=1), encoding="utf-8")
    return len(manifest)


async def capture_job(ctx: dict[str, Any]) -> str:
    """arq job: one capture run, then purge old rows. Needs ctx['engine'] and ctx['limiter']."""
    settings = get_settings()

    async def work() -> str:
        if not settings.nh_capture_enabled:
            return "disabled"
        key = settings.nh_primary_key
        if not key:
            log.warning("nh_subscription_key is empty; skipping capture")
            return "no key"
        limiter: Limiter = ctx["limiter"]
        async with NationalHighwaysClient(key, limiter, settings.nh_base_url) as client:
            run_id = await run_capture(settings, ctx["engine"], client)
        purged = await purge_old(ctx["engine"], settings.nh_capture_retention_days)
        log.info("capture run %s finished; purged %d old rows", run_id, purged)
        return run_id

    return await tracked(ctx["engine"], "nh_capture", work)  # type: ignore[no-any-return]


async def _cli(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging("WARNING")
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    redis = Redis.from_url(settings.redis_dsn, decode_responses=True)
    try:
        run_id = args.run
        if not args.no_fetch:
            key = settings.nh_primary_key
            if not key:
                print("nh_subscription_key is empty: put your key in the secrets folder.")
                return 2
            limiter = SlidingWindowLimiter(redis, "nh", settings.nh_rate_limit_per_minute)
            only = set(args.only.split(",")) if args.only else None
            print("Capturing (this can take a few minutes: calls are paced to the rate limit)...")
            async with NationalHighwaysClient(key, limiter, settings.nh_base_url) as client:
                run_id = await run_capture(settings, engine, client, only=only)
        run_id = run_id or await latest_run_id(engine)
        if not run_id:
            print("No captures stored yet.")
            return 1
        print(f"\nRun {run_id}")
        for row in await summarize(engine, run_id):
            more = "  (more pages not fetched)" if row["truncated"] else ""
            err = f"  error={row['error']}" if row["error"] else ""
            print(
                f"  {row['call_name']:<20} pages={row['pages']:<3} "
                f"bytes={row['bytes']:<10} status={row['statuses']}{more}{err}"
            )
        if args.export:
            count = await export_run(engine, run_id, Path(args.export))
            print(f"\nExported {count} files to {args.export}")
        return 0
    finally:
        await redis.aclose()
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture raw National Highways responses.")
    parser.add_argument("--only", help="comma-separated call names: " + ", ".join(c.name for c in PLAN))
    parser.add_argument("--no-fetch", action="store_true", help="skip fetching; summarise/export a stored run")
    parser.add_argument("--run", help="run id to summarise/export with --no-fetch (default: latest)")
    parser.add_argument("--export", metavar="DIR", help="write the run's pages to files in DIR")
    return asyncio.run(_cli(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
