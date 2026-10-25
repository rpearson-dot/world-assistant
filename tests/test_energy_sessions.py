import importlib.util
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.energy import kraken, sessions, store
from app.energy.ha import energy_device, session_sensors
from app.energy.probe import redact

NOW = datetime(2026, 10, 23, 17, 30, tzinfo=UTC)


def t(h):
    return (NOW + timedelta(hours=h)).isoformat().replace("+00:00", "Z")


DATA = {
    "savingSessions": {
        "events": [
            {
                "id": "1",
                "code": "SAVE1",
                "rewardPerKwhInOctoPoints": 1800,
                "startAt": t(-0.5),
                "endAt": t(0.5),
                "devEvent": False,
                "targetRegion": [{"regionId": "J"}],
                "eventType": "TURN_DOWN",
                "capacityStatus": "AVAILABLE",
            },
            {
                "id": "2",
                "code": "SAVE2",
                "rewardPerKwhInOctoPoints": 2250,
                "startAt": t(24),
                "endAt": t(25),
                "devEvent": False,
                "targetRegion": None,
                "eventType": "TURN_DOWN",
            },
            {
                "id": "3",
                "code": "DEV",
                "startAt": t(2),
                "endAt": t(3),
                "devEvent": True,
                "eventType": "TURN_DOWN",
            },
            {
                "id": "4",
                "code": "UP",
                "startAt": t(5),
                "endAt": t(6),
                "devEvent": False,
                "eventType": "TURN_UP",
            },
            {
                "id": "5",
                "code": "BAD",
                "startAt": "nonsense",
                "endAt": t(6),
                "eventType": "TURN_DOWN",
            },
        ],
        "account": {
            "joinedEvents": [
                {
                    "eventId": "1",
                    "startAt": t(-0.5),
                    "endAt": t(0.5),
                    "rewardGivenInOctoPoints": 0,
                    "eventType": "TURN_DOWN",
                }
            ]
        },
    }
}


def rows():
    return kraken.parse_saving(DATA)


def test_parse_saving_skips_dev_and_bad_and_marks_joined():
    by = {r["code"]: r for r in kraken.parse_saving(DATA)}
    assert set(by) == {"SAVE1", "SAVE2", "UP"}
    assert by["SAVE1"]["joined"] and not by["SAVE2"]["joined"]
    assert by["SAVE1"]["regions"] == "J" and by["UP"]["kind"] == "boost"


def test_summary_now_and_next():
    s = sessions.summarise(rows(), NOW)
    assert s["saving"]["now"] and s["saving"]["now_joined"]
    assert s["saving"]["next"]["code"] == "SAVE2" and s["saving"]["next_joined"] is None
    assert s["saving"]["next"]["starts_in_minutes"] == 1440


def test_empty():
    s = sessions.summarise([], NOW)
    assert s["saving"]["next"] is None


def test_sensors():
    s = sessions.summarise(rows(), NOW)
    keys = {x.key: x for x in session_sensors(s)}
    assert keys["saving_session_now"].state == "on"
    assert keys["saving_session_next_start"].state is not None
    # the sample's Power Up ("boost") event is Free Electricity
    assert keys["free_electricity_now"].state == "off" and keys["free_electricity_next_start"].state is not None
    assert session_sensors({}) == []
    dev = energy_device({"available": False, "sessions": s}, [])
    assert {x.key for x in dev.sensors} >= {"status", "saving_session_now"}


def test_redact():
    out = redact("acct A-954A75D2 key sk_live_abc token", ["sk_live_abc"])
    assert "954A75D2" not in out and "sk_live_abc" not in out


URL = os.environ.get("WA_TEST_DATABASE_URL")
VERSIONS = Path(__file__).resolve().parent.parent / "migrations" / "versions"


@pytest.fixture
def schema():
    assert URL
    engine = create_engine(URL)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS energy_session"))
        with Operations.context(MigrationContext.configure(conn)):
            spec = importlib.util.spec_from_file_location(
                "m0024", VERSIONS / "0024_energy_session.py"
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.upgrade()
    yield
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS energy_session"))
    engine.dispose()


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_save_replaces_only_its_kinds(schema) -> None:
    engine = create_async_engine(URL)
    now = datetime.now(UTC)
    mk = lambda kind, code, h: {
        "kind": kind,
        "code": code,
        "start_at": now + timedelta(hours=h),
        "end_at": now + timedelta(hours=h + 1),
        "octopoints_per_kwh": None,
        "joined": False,
        "regions": None,
        "event_type": None,
    }
    try:
        await store.save_sessions(engine, ["saving"], [mk("saving", "A", 1), mk("saving", "B", 2)])
        await store.save_sessions(engine, ["boost"], [mk("boost", "U", 3)])
        await store.save_sessions(
            engine, ["saving"], [mk("saving", "B", 2)]
        )  # A is gone, boost stays
        got = {(r["kind"], r["code"]) for r in await store.load_sessions(engine)}
        assert got == {("saving", "B"), ("boost", "U")}
        # Free Electricity is its own kind again: a saving save leaves it, a free save replaces it
        await store.save_sessions(engine, ["free"], [mk("free", "F1", 5)])
        await store.save_sessions(engine, ["saving"], [mk("saving", "B", 2)])
        assert {r["kind"] for r in await store.load_sessions(engine)} == {"saving", "boost", "free"}
        await store.save_sessions(engine, ["free"], [])
        assert {r["kind"] for r in await store.load_sessions(engine)} == {"saving", "boost"}
    finally:
        await engine.dispose()


def test_retired_sensors_are_not_published_and_are_cleared():
    from app import ha
    from app.energy.ha import RETIRED

    published = {x.key for x in session_sensors(sessions.summarise(rows(), NOW))}
    assert not published & set(RETIRED)
    msgs = ha.clear("wa", "homeassistant", "energy", RETIRED)
    assert ("homeassistant/sensor/wa_energy_free_electricity_next_joined_start/config", "", True) in msgs


def test_spoken_answer():
    from zoneinfo import ZoneInfo

    from app.energy import rates as R
    from app.energy import speech, summary

    tz = ZoneInfo("Europe/London")
    d = datetime(2026, 10, 23, 0, 0, tzinfo=UTC)
    ps = [
        R.Period(d + timedelta(hours=a), d + timedelta(hours=b), p)
        for a, b, p in [
            (0, 4, 26),
            (4, 7, 13),
            (7, 13, 26),
            (13, 16, 13),
            (16, 19, 39),
            (19, 24, 26),
        ]
    ]
    now = d + timedelta(hours=14)
    res = summary.summarise("T", ps, now)
    res["sessions"] = sessions.summarise(rows(), now)
    text = speech.sentence(res, tz, now)
    assert (
        "cheap now at 13.0p" in text and "until 17:00" in text
    )  # 16:00 UTC is 17:00 in London (BST)
    assert "Peak is 39.0p from 17:00" in text and "cheapest 3 hours" in text



def _happy(code: str, start: datetime, joined: bool) -> dict:
    end = start + timedelta(hours=1)
    return {"id": code, "code": code, "startAt": start.isoformat(), "endAt": end.isoformat(), "devEvent": False,
            "eventType": "WEEKEND_HAPPY_HOUR", "rewardPerKwhInOctoPoints": 0, "targetRegion": []}  # fmt: skip


def test_weekend_happy_hours_your_joined_hour_comes_first() -> None:
    """As Octopus sent them on 24 October 2026: Weekend Happy Hours from 07:00 to 15:00, the 13:00 one joined."""
    from app.energy import kraken
    from app.energy import speech as sp
    from app.energy.ha import free_sensors

    day = datetime(2026, 10, 25, tzinfo=UTC)
    events = [_happy(f"EVENT_{110 + h}_251026", day + timedelta(hours=h), h == 13) for h in range(7, 15)]
    data = {"savingSessions": {"events": events, "account": {"joinedEvents": [
        {"eventId": "EVENT_123_251026", "startAt": (day + timedelta(hours=13)).isoformat(),
         "endAt": (day + timedelta(hours=14)).isoformat(), "eventType": "WEEKEND_HAPPY_HOUR"}]}}}  # fmt: skip
    rows = kraken.parse_saving(data)
    assert {r["kind"] for r in rows} == {"boost"} and sum(r["joined"] for r in rows) == 1
    for r in rows:
        r["event_type"] = "WEEKEND_HAPPY_HOUR"
    s = sessions.summarise(rows, datetime(2026, 10, 24, 9, tzinfo=UTC))
    assert s["free"]["next"]["start"].startswith("2026-10-25T07:00") and s["free"]["next"]["type"] == "Weekend Happy Hour"
    assert s["free"]["next_joined"]["start"].startswith("2026-10-25T13:00")
    assert s["saving"]["next"] is None
    sensors = {x.key: x for x in free_sensors(s["free"])}
    assert sensors["free_electricity_next_start"].state.startswith("2026-10-25T13:00")
    assert sensors["free_electricity_next_start"].attributes["joined"] is True
    at_noon = sessions.summarise(rows, day + timedelta(hours=12, minutes=30))  # a Happy Hour, but not yours
    noon = {x.key: x for x in free_sensors(at_noon["free"])}
    assert noon["free_electricity_now"].state == "off" and noon["free_electricity_now"].attributes["announced_now"]
    at_one = sessions.summarise(rows, day + timedelta(hours=13, minutes=30))
    assert {x.key: x.state for x in free_sensors(at_one["free"])}["free_electricity_now"] == "on"
    said = sp.sentence({"available": True, "now": {"pence": 20.0, "band": "standard", "until": "2026-10-24T16:00:00+00:00"}, "next": None,
                        "sessions": s}, ZoneInfoLondon(), datetime(2026, 10, 24, 9, tzinfo=UTC))
    assert "13:00" in said and "Weekend Happy Hour" in said


def ZoneInfoLondon():
    from zoneinfo import ZoneInfo

    return ZoneInfo("Europe/London")


async def test_saving_sessions_query_retried_once_after_a_timeout() -> None:
    import httpx

    from app.energy import kraken

    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.host)
        if req.url.host == "api.oegb-kraken.energy":
            return httpx.Response(200, json={"data": {"obtainKrakenToken": {"token": "t"}}})
        if len(calls) == 2:
            raise httpx.ReadTimeout("slow", request=req)
        return httpx.Response(200, json={"data": {"savingSessions": {"events": [], "account": {"joinedEvents": [
            {"eventId": "9", "startAt": "2026-10-25T13:00:00+00:00", "endAt": "2026-10-25T14:00:00+00:00",
             "eventType": "FREE_ELECTRICITY"}]}}}})  # fmt: skip

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        rows = await kraken.fetch_saving(c, "k", "A-1")
    assert len(calls) == 3 and rows[0]["joined"] and rows[0]["kind"] == "boost"
