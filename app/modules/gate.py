"""Where a switched-off module stops being reachable: the web, the worker and the assistant.

* Web: a small ASGI layer answers 404 for any path under a module's prefix when that module is off. A page gets a
  short explanation with a link to Modules; the API gets JSON.
* Worker: `gated(job, module_id)` wraps a job so it returns at once, without a job_run row, when its module is off.
* Assistant: tools of a module that is off are neither listed nor callable.

The choice is read with a short cache, so a change takes effect within seconds and needs no restart.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Awaitable, Callable
from typing import Any

from app.modules import registry, selection

Scope = dict[str, Any]


async def enabled(engine: Any) -> set[str]:
    return selection.enabled_ids(list(registry.modules()), await selection.load(engine))


async def is_enabled(module_id: str, engine: Any) -> bool:
    return module_id in await enabled(engine)


def off_html(name: str) -> bytes:
    return (
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{name} is off</title><body style='font:16px system-ui;margin:2rem auto;max-width:32rem;padding:0 1rem'>"
        f"<h1>{name} is switched off</h1><p>Turn it on under <a href='/modules'>Modules</a>.</p></body>"
    ).encode()


class ModuleGate:
    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            mid = registry.module_for_path(scope["path"])
            engine = getattr(getattr(scope.get("app"), "state", None), "engine", None)
            if mid and engine is not None and mid not in await enabled(engine):
                module = registry.get(mid)
                name = module.name if module else mid
                if scope["path"].startswith("/v1/"):
                    body = json.dumps({"detail": f"{name} is switched off. Turn it on at /modules.", "module": mid}).encode()
                    ctype = b"application/json"
                else:
                    body, ctype = off_html(name), b"text/html; charset=utf-8"
                await send({"type": "http.response.start", "status": 404,
                            "headers": [(b"content-type", ctype), (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def gated(job: Callable[..., Awaitable[Any]], module_id: str) -> Callable[..., Awaitable[Any]]:
    """The job, but a no-op while its module is off. Keeps the name, so arq and first-load enqueueing still find it."""
    if module_id == "core":
        return job

    @functools.wraps(job)
    async def run(ctx: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
        if not await is_enabled(module_id, ctx["engine"]):
            return "module off"
        return await job(ctx, *args, **kwargs)

    return run
