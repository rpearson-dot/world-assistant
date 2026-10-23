"""Does this module's key work? A small live call for the sources where one is cheap and clear.

Everything else answers "no test for this one yet" rather than guessing. A test never changes anything and never
returns a key.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings


async def _bods(s: Settings) -> tuple[bool, str]:
    from app.integrations.bods.client import BodsError, find_datasets

    try:
        sets = await find_datasets(s.bods_api_key, limit=1)
    except BodsError as exc:
        return False, str(exc)
    return True, "BODS accepted the key" + (" and answered." if sets else ", though it listed nothing.")


async def _tfl(s: Settings) -> tuple[bool, str]:
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


async def _tfwm(s: Settings) -> tuple[bool, str]:
    from app.integrations.tfwm.client import TfwmClient, TfwmError

    try:
        client = TfwmClient(s.tfwm_app_id, s.tfwm_app_key, s.tfwm_base_url)
        _, updates = await client.trip_updates()
    except TfwmError as exc:
        return False, str(exc)
    return True, f"TfWM answered with {len(updates)} trip updates" + ("." if client.secure else " (over plain HTTP).")


TESTS = {"bods": _bods, "tfl": _tfl, "tfwm": _tfwm}


async def run(module_id: str, s: Settings) -> dict[str, Any]:
    test = TESTS.get(module_id)
    if test is None:
        return {"ok": None, "message": "There is no live test for this one yet; check its row on the Setup list."}
    ok, message = await test(s)
    return {"ok": ok, "message": message}
