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
    return kraken.parse_saving(DATA) + kraken.parse_free(
        {
            "data": [
                {"id": 9, "code": "F1", "start": t(3), "end": t(4)},
                {"id": 8, "code": "T", "start": t(1), "end": t(2), "is_test": True},
            ]
        }
    )


def test_parse_saving_skips_dev_and_bad_and_marks_joined():
    by = {r["code"]: r for r in kraken.parse_saving(DATA)}
    assert set(by) == {"SAVE1", "SAVE2", "UP"}
    assert by["SAVE1"]["joined"] and not by["SAVE2"]["joined"]
    assert by["SAVE1"]["regions"] == "J" and by["UP"]["kind"] == "boost"


def test_parse_free_skips_tests():
    assert [r["code"] for r in rows() if r["kind"] == "free"] == ["F1"]


def test_parse_free_bad_shape():
    with pytest.raises(kraken.OctopusError):
        kraken.parse_free({"nope": 1})


def test_summary_now_and_next():
    s = sessions.summarise(rows(), NOW)
    assert s["saving"]["now"] and s["saving"]["now_joined"]
    assert s["saving"]["next"]["code"] == "SAVE2" and s["saving"]["next_joined"] is None
    assert not s["free"]["now"] and s["free"]["next"]["starts_in_minutes"] == 180


def test_empty():
    s = sessions.summarise([], NOW)
    assert s["saving"]["next"] is None and not s["free"]["now"]


def test_sensors():
    s = sessions.summarise(rows(), NOW)
    keys = {x.key: x for x in session_sensors(s)}
    assert keys["saving_session_now"].state == "on" and keys["free_electricity_now"].state == "off"
    assert keys["free_electricity_next_start"].state is not None
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
        await store.save_sessions(
            engine, ["saving", "boost"], [mk("saving", "A", 1), mk("saving", "B", 2)]
        )
        await store.save_sessions(engine, ["free"], [mk("free", "F", 3)])
        await store.save_sessions(
            engine, ["saving", "boost"], [mk("saving", "B", 2)]
        )  # A is gone, free stays
        got = {(r["kind"], r["code"]) for r in await store.load_sessions(engine)}
        assert got == {("saving", "B"), ("free", "F")}
    finally:
        await engine.dispose()
