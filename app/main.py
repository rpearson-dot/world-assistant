"""World Assistant API gateway."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.logging import configure_logging
from app.modules import registry
from app.modules.gate import ModuleGate
from app.version import VERSION

log = logging.getLogger("wa.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)

    app.state.engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    app.state.redis = Redis.from_url(settings.redis_dsn, decode_responses=True)

    if not settings.nh_primary_key:
        log.warning("nh_subscription_key is empty; National Highways calls will fail")

    from app import look

    await look.load(app.state.engine)
    from app.modules import selection

    if await selection.first_start(app.state.engine):
        log.info("new install: starting from the essentials; the setup wizard asks which areas to add")
    for _, start in registry.provided("startup"):
        try:
            await start(app)
        except Exception:
            log.exception("a module's startup failed")
    from app import watchdog

    await watchdog.WATCH.start(app)
    log.info("world-assistant api started (env=%s)", settings.env)
    yield

    await watchdog.WATCH.stop()
    for _, close in registry.provided("shutdown"):
        await close()
    await app.state.redis.aclose()
    await app.state.engine.dispose()


app = FastAPI(title="World Assistant", version=VERSION, lifespan=lifespan)
# Files shipped with the app (Leaflet), so the pages load no third-party script. Read-only and
# unauthenticated: it holds nothing private, and StaticFiles refuses paths outside the directory.
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
for _module_id, _path in registry.router_paths():
    app.include_router(registry.import_string(_path))
app.add_middleware(ModuleGate)  # a switched-off module's routes answer 404 (see app/modules/gate.py)


class ErrorCount:
    """Counts the server errors (5xx) this process has answered, for an update's trial of a new version
    (scripts/rollout.sh reads /healthz/errors before and after)."""

    n = 0

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)

        async def counted(message: Any) -> None:
            if message["type"] == "http.response.start" and message["status"] >= 500:
                ErrorCount.n += 1
            await send(message)

        try:
            await self.inner(scope, receive, counted)
        except Exception:
            ErrorCount.n += 1
            raise


app.add_middleware(ErrorCount)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. Does not touch dependencies."""
    return {"status": "ok"}


@app.get("/healthz/errors")
async def healthz_errors() -> dict[str, Any]:
    """How many server errors this process has answered since it started, and its version (for updates)."""
    return {"errors": ErrorCount.n, "version": VERSION}


@app.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness: Postgres and Redis are reachable."""
    checks: dict[str, str] = {}

    try:
        async with request.app.state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
        checks["postgres"] = f"error: {type(exc).__name__}"

    try:
        await request.app.state.redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
        checks["redis"] = f"error: {type(exc).__name__}"

    ready = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "degraded", "checks": checks},
    )
