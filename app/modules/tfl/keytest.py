"""Does the tfl key work? A small live call that changes nothing (provides "key.test")."""

from __future__ import annotations

import httpx

from app.config import Settings


async def test_key(s: Settings) -> tuple[bool, str]:
    params = {"app_key": s.tfl_app_key} if s.tfl_app_key else {}
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get("https://api.tfl.gov.uk/Line/Meta/Modes", params=params)
    except httpx.HTTPError as exc:
        return False, f"TfL could not be reached: {type(exc).__name__}"
    if r.status_code in (401, 403):
        return False, "TfL refused the key"
    if r.status_code != 200:
        return False, f"TfL answered HTTP {r.status_code}"
    return True, "TfL answered" + (" with your key." if s.tfl_app_key else " (no key set: low rate limit).")
