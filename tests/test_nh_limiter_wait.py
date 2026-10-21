from __future__ import annotations

import httpx

from app.integrations.nationalhighways.client import CLOSURES, NationalHighwaysClient


class _RecordingLimiter:
    def __init__(self) -> None:
        self.waits: list[float] = []

    async def acquire(self, max_wait: float = 30.0) -> None:
        self.waits.append(max_wait)


def _ok(_req: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"D2Payload": {}})


async def test_limiter_wait_is_configurable() -> None:
    patient, default = _RecordingLimiter(), _RecordingLimiter()
    async with NationalHighwaysClient(
        "k", patient, transport=httpx.MockTransport(_ok), limiter_max_wait=150.0
    ) as c:
        _ = [p async for p in c.pages(CLOSURES, {})]
    async with NationalHighwaysClient("k", default, transport=httpx.MockTransport(_ok)) as c:
        _ = [p async for p in c.pages(CLOSURES, {})]
    assert patient.waits == [150.0]
    assert default.waits == [30.0]
