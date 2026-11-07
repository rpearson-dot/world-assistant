"""UK bank holidays, from GOV.UK's own feed (https://www.gov.uk/bank-holidays.json, Open Government Licence).

Used wherever World Assistant takes "a weekday" to mean a working day: a bus tracker set to weekdays only rests on a
bank holiday (so its buses are not counted as missed), and next-bus times from a weekday timetable are not given on
one. Which part of the UK comes from `WA_BANK_HOLIDAY_DIVISION` (england-and-wales, scotland or northern-ireland).

    GET /v1/bank-holidays          the next ones, and whether today is one

The feed is fetched at most once a week and kept in the database, so every process (the API and the worker) reads the
same list; if GOV.UK cannot be reached the last list is kept, and before the first fetch a built-in list (2026 and
2027, England and Wales) stands in. `is_bank_holiday` is plain and quick: call `ensure` first (it is cheap when the list
is fresh).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from app.auth import require_token
from app.config import get_settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/bank-holidays", tags=["bank holidays"], dependencies=[Depends(require_token)])
FEED = "https://www.gov.uk/bank-holidays.json"
KEY = "bank_holidays"
STALE = timedelta(days=7)
MEMORY_S = 6 * 3600
RETRY_S = 3600  # after a failed fetch, the next try waits this long
# England and Wales, 2026 and 2027, for before the first fetch (from GOV.UK's published list)
BUILT_IN = {"2026-01-01": "New Year's Day", "2026-04-03": "Good Friday", "2026-04-06": "Easter Monday",
            "2026-05-04": "Early May bank holiday", "2026-05-25": "Spring bank holiday",
            "2026-08-31": "Summer bank holiday", "2026-12-25": "Christmas Day", "2026-12-28": "Boxing Day",
            "2027-01-01": "New Year's Day", "2027-03-26": "Good Friday", "2027-03-29": "Easter Monday",
            "2027-05-03": "Early May bank holiday", "2027-05-31": "Spring bank holiday",
            "2027-08-30": "Summer bank holiday", "2027-12-27": "Christmas Day", "2027-12-28": "Boxing Day"}  # fmt: skip

_days: dict[str, str] = dict(BUILT_IN)
_loaded_at = 0.0
_tried_at = 0.0


def is_bank_holiday(d: date) -> bool:
    return d.isoformat() in _days


def name_of(d: date) -> str | None:
    return _days.get(d.isoformat())


def parse(feed: dict[str, Any], division: str) -> dict[str, str]:
    """{date: title} for one division of GOV.UK's feed."""
    events = ((feed or {}).get(division) or {}).get("events") or []
    return {str(e["date"]): str(e.get("title") or "Bank holiday") for e in events if isinstance(e, dict) and e.get("date")}


async def ensure(engine: Any, client: httpx.AsyncClient | None = None, force: bool = False) -> dict[str, str]:
    """Make the list current: from memory if loaded lately, else the database, fetching GOV.UK's feed when the stored
    copy is a week old (or missing). Never raises: a list that cannot be refreshed is kept."""
    global _days, _loaded_at, _tried_at
    now = time.time()
    if not force and now - _loaded_at < MEMORY_S:
        return _days
    division = get_settings().bank_holiday_division
    stored: dict[str, Any] | None = None
    try:
        async with engine.connect() as conn:
            v = (await conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": KEY})).scalar()
        stored = json.loads(v) if isinstance(v, str) else v
    except Exception:  # noqa: BLE001 - no database (yet): the built-in list stands
        stored = None
    fresh = stored and stored.get("division") == division and stored.get("days") and \
        datetime.now(UTC) - datetime.fromisoformat(stored["fetched"]) < STALE  # fmt: skip
    if stored and stored.get("division") == division and stored.get("days"):
        _days = dict(stored["days"])
    if (force or not fresh) and now - _tried_at > RETRY_S:
        _tried_at = now
        try:
            async with client or httpx.AsyncClient(timeout=15) as c:
                r = await c.get(FEED, headers={"Accept": "application/json"})
                r.raise_for_status()
                days = parse(r.json(), division)
            if days:
                _days = days
                async with engine.begin() as conn:
                    await conn.execute(text(
                        "INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
                        {"k": KEY, "v": json.dumps({"division": division, "fetched": datetime.now(UTC).isoformat(), "days": days})})  # fmt: skip
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.info("bank holidays: kept the last list (%s)", type(exc).__name__)
        except Exception as exc:  # noqa: BLE001 - never take a caller down
            log.warning("bank holidays: kept the last list (%s)", exc)
    _loaded_at = now
    return _days


def forget() -> None:
    global _days, _loaded_at, _tried_at
    _days, _loaded_at, _tried_at = dict(BUILT_IN), 0.0, 0.0


@router.get("")
async def upcoming(request: Request) -> dict[str, Any]:
    """The next bank holidays (in the division set by WA_BANK_HOLIDAY_DIVISION), and whether today is one."""
    from zoneinfo import ZoneInfo

    days = await ensure(request.app.state.engine)
    today = datetime.now(ZoneInfo("Europe/London")).date()
    nxt = [{"date": d, "title": t} for d, t in sorted(days.items()) if d >= today.isoformat()][:8]
    return {"division": get_settings().bank_holiday_division, "today": name_of(today), "next": nxt,
            "source": "GOV.UK (Open Government Licence)"}  # fmt: skip
