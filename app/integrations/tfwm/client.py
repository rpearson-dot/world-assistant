"""Transport for West Midlands (TfWM) open data: the GTFS timetable and the GTFS-Realtime trip updates.

    GET {base}/gtfs/trip_updates?app_id=ID&app_key=KEY        predicted times for every vehicle, protobuf
    GET {base}/gtfs/tfwm_gtfs.zip?app_id=ID&app_key=KEY       the whole schedule, GTFS zip

The portal documents the base as plain `http://api.tfwm.org.uk`, and the key travels in the query string.
This sends it over HTTPS by default and does not fall back: if HTTPS does not work, set `WA_TFWM_BASE_URL`
yourself, knowing the key is then readable on the way. The key lives in the `tfwm_app_id` and `tfwm_app_key`
secrets.

Not run against the live service from where this was written: the decoder is tested on a feed built to the
published protobuf layout. `python -m app.integrations.tfwm.probe` checks it.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.integrations.tfwm.gtfsrt import FeedError, parse_trip_updates

DEFAULT_BASE = "https://api.tfwm.org.uk"
MAX_FEED = 40 * 1024 * 1024  # a whole region's predictions; more means something is wrong


class TfwmError(Exception):
    """The feed could not be read, or no key is set."""


class TfwmClient:
    def __init__(self, app_id: str, app_key: str, base: str = "") -> None:
        if not app_id or not app_key:
            raise TfwmError("the tfwm_app_id and tfwm_app_key secrets are not both set")
        self._params = {"app_id": app_id, "app_key": app_key}
        self._base = (base or DEFAULT_BASE).rstrip("/")

    @property
    def secure(self) -> bool:
        return self._base.startswith("https://")

    async def _get(self, path: str) -> bytes:
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
                r = await client.get(f"{self._base}{path}", params=self._params)
        except httpx.HTTPError as exc:
            raise TfwmError(f"TfWM could not be reached: {type(exc).__name__}") from exc
        if r.status_code in (401, 403):
            raise TfwmError("TfWM refused the key (check tfwm_app_id and tfwm_app_key)")
        if r.status_code != 200:
            raise TfwmError(f"TfWM answered HTTP {r.status_code}")
        if len(r.content) > MAX_FEED:
            raise TfwmError("the TfWM feed was larger than expected")
        return r.content

    async def trip_updates(self) -> tuple[int | None, list[dict[str, Any]]]:
        body = await self._get("/gtfs/trip_updates")
        try:
            return parse_trip_updates(body)
        except FeedError as exc:
            raise TfwmError(f"the TfWM feed could not be read: {exc}") from exc

    async def schedule_zip(self) -> bytes:
        return await self._get("/gtfs/tfwm_gtfs.zip")
