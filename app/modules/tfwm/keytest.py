"""Does the tfwm key work? A small live call that changes nothing (provides "key.test")."""

from __future__ import annotations

from app.config import Settings


async def test_key(s: Settings) -> tuple[bool, str]:
    from app.modules.tfwm.sources.tfwm.client import TfwmClient, TfwmError

    try:
        client = TfwmClient(s.tfwm_app_id, s.tfwm_app_key, s.tfwm_base_url)
        _, updates = await client.trip_updates()
    except TfwmError as exc:
        return False, str(exc)
    return True, f"TfWM answered with {len(updates)} trip updates" + ("." if client.secure else " (over plain HTTP).")
