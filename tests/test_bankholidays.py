"""Bank holidays from GOV.UK: read, kept, and what they change (weekday-only trackers rest; no weekday timetable)."""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from app import bankholidays as BH

FEED = {"england-and-wales": {"division": "england-and-wales", "events": [
            {"title": "Christmas Day", "date": "2026-12-25", "notes": "", "bunting": True},
            {"title": "Boxing Day", "date": "2026-12-28", "notes": "Substitute day", "bunting": True}]},
        "scotland": {"division": "scotland", "events": [{"title": "St Andrew's Day", "date": "2026-11-30"}]}}  # fmt: skip


@pytest.fixture(autouse=True)
def fresh() -> Any:
    BH.forget()
    yield
    BH.forget()


def test_built_in_until_the_first_fetch() -> None:
    assert BH.is_bank_holiday(date(2026, 12, 28)) and BH.name_of(date(2026, 5, 25)) == "Spring bank holiday"
    assert not BH.is_bank_holiday(date(2026, 11, 9))
    assert BH.parse(FEED, "scotland") == {"2026-11-30": "St Andrew's Day"}
    assert BH.parse({}, "scotland") == {}


def test_a_weekdays_only_tracker_rests_on_a_bank_holiday() -> None:
    from app.modules.bus.sources.bus.config import TrackerConfig
    from app.modules.bus.sources.bus.poller import in_window

    cfg = TrackerConfig.model_construct(weekdays_only=True, poll_start="06:30", poll_end="09:30")
    tz = ZoneInfo("Europe/London")
    assert in_window(cfg, datetime(2026, 12, 29, 7, 30, tzinfo=tz))  # an ordinary Tuesday
    assert not in_window(cfg, datetime(2026, 12, 28, 7, 30, tzinfo=tz))  # Boxing Day (substitute), a Monday


def test_no_weekday_timetable_on_a_bank_holiday() -> None:
    from app.modules.bods.sources.bods.txc import Pattern, Route
    from app.modules.bus.sources.bus.departures import _next_times

    r = Route(line="1", names={}, patterns={"p": Pattern("outbound", ["A", "B"], [0, 60])}, journeys=[("p", 9 * 60)])
    tz = ZoneInfo("Europe/London")
    assert _next_times(r, "A", "outbound", datetime(2026, 12, 29, 8, 0, tzinfo=tz)) == ["09:00"]
    assert _next_times(r, "A", "outbound", datetime(2026, 12, 28, 8, 0, tzinfo=tz)) == []


DB = os.environ.get("WA_TEST_DATABASE_URL")


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
async def test_fetched_kept_and_a_failure_keeps_the_last_list() -> None:
    import sqlalchemy as sa
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    sync = sa.create_engine(DB)  # type: ignore[arg-type]
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS app_setting"))
        conn.execute(text("CREATE TABLE app_setting (key text primary key, value jsonb not null, updated_at timestamptz)"))
    engine = create_async_engine(DB, poolclass=NullPool)  # type: ignore[arg-type]
    asked: list[str] = []

    def answer(req: httpx.Request) -> httpx.Response:
        asked.append(str(req.url))
        return httpx.Response(200, json=FEED)

    try:
        days = await BH.ensure(engine, httpx.AsyncClient(transport=httpx.MockTransport(answer)), force=True)
        assert days == {"2026-12-25": "Christmas Day", "2026-12-28": "Boxing Day"} and asked == [BH.FEED]
        BH.forget()  # another process: reads what was kept, and does not fetch while it is fresh
        down = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
        assert await BH.ensure(engine, down) == days
        BH.forget()
        assert await BH.ensure(engine, down, force=True) == days  # GOV.UK down: the last list is kept
    finally:
        await engine.dispose()
        with sync.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS app_setting"))
        sync.dispose()
