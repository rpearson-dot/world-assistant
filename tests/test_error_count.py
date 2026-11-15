"""The update trial counts a fault in the new version (a 500 or an uncaught exception), but not a deliberate
502/503/504 — a device or outside service that did not answer, which happens whichever version runs. A dropped write
to an air conditioner during the trial must not roll the deploy back (it did, for 0.57.0)."""

from __future__ import annotations

import asyncio
from typing import Any

from app.main import ErrorCount


def _run_once(status: int | None, raises: bool = False) -> int:
    before = ErrorCount.n

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        if raises:
            raise RuntimeError("boom")
        await send({"type": "http.response.start", "status": status})
        await send({"type": "http.response.body", "body": b""})

    async def send(_message: Any) -> None:
        return None

    async def go() -> None:
        mw = ErrorCount(inner)
        await mw({"type": "http"}, None, send)

    try:
        asyncio.run(go())
    except RuntimeError:
        pass
    return ErrorCount.n - before


def test_only_a_real_fault_counts() -> None:
    assert _run_once(200) == 0
    assert _run_once(404) == 0
    assert _run_once(502) == 0  # an air conditioner did not answer a write
    assert _run_once(503) == 0  # an outside service is unavailable
    assert _run_once(504) == 0  # an upstream timed out
    assert _run_once(500) == 1  # this version threw
    assert _run_once(None, raises=True) == 1  # an uncaught exception
