"""Does the bods key work? A small live call that changes nothing (provides "key.test")."""

from __future__ import annotations

from app.config import Settings


async def test_key(s: Settings) -> tuple[bool, str]:
    from app.modules.bods.sources.bods.client import BodsError, find_datasets

    try:
        sets = await find_datasets(s.bods_api_key, limit=1)
    except BodsError as exc:
        return False, str(exc)
    return True, "BODS accepted the key" + (" and answered." if sets else ", though it listed nothing.")
