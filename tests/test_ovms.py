"""The car's OVMS module over MQTT: topics, warnings, and (with a database) a stream of messages recorded."""

from __future__ import annotations

import asyncio
import importlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from app.modules.ovms import parse

URL = os.environ.get("WA_TEST_DATABASE_URL")
NOW = datetime(2026, 11, 14, 8, 0, tzinfo=UTC)


def test_topics_under_the_prefix() -> None:
    assert parse.topic("ovms/rob/HV66BZD/metric/v/b/soc", "ovms/+/+/") == ("HV66BZD", "metric", "v.b.soc")
    assert parse.topic("ovms/rob/HV66BZD/notify/alert/charge.stopped", "ovms/+/+/") == ("HV66BZD", "notify", "alert/charge.stopped")
    assert parse.topic("ovms/rob/HV66BZD/event", "ovms/rob/HV66BZD/") == ("HV66BZD", "event", "")
    assert parse.topic("ovms/other/X/metric/v/b/soc", "ovms/rob/+/") is None
    assert parse.topic("ovms/rob/HV66BZD/client/x/command/1", "ovms/+/+/") is None


def _m(**kw: Any) -> dict[str, tuple[str, datetime]]:
    return {k.replace("_", "."): (v, NOW) for k, v in kw.items()}


def test_warnings_from_the_cars_metrics() -> None:
    parked = {"v.e.on": ("no", NOW), "v.e.locked": ("no", NOW), "v.e.handbrake": ("no", NOW), "v.p.speed": ("0", NOW),
              "v.d.trunk": ("yes", NOW), "v.b.12v.voltage.alert": ("yes", NOW), "v.b.12v.voltage": ("11.57", NOW),
              "v.b.soc": ("12", NOW)}  # fmt: skip
    keys = {w["key"]: w["text"] for w in parse.warnings(parked, NOW)}
    assert keys == {"12v": "12 V battery low (11.57 V)", "doors": "Open while parked: boot", "unlocked": "Unlocked while parked",
                    "handbrake": "Handbrake off while parked", "soc": "Battery at 12%"}  # fmt: skip
    # driving: nothing about doors, locks or the handbrake
    driving = {**parked, "v.e.on": ("yes", NOW), "v.b.12v.voltage.alert": ("no", NOW), "v.b.soc": ("60", NOW)}
    assert parse.warnings(driving, NOW) == []
    quiet = {"v.b.soc": ("60", NOW - timedelta(hours=7))}
    assert [w["key"] for w in parse.warnings(quiet, NOW)] == ["quiet"]
    s = parse.summary({**parked, "v.b.range.est": ("120", NOW), "v.c.charging": ("no", NOW)})
    assert (s["soc"], s["range_km"], s["locked"], s["charging"], s["volts_12v"]) == (12.0, 120.0, False, False, 11.57)


@pytest.fixture
def db() -> Iterator[str]:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mig = importlib.import_module("migrations.versions.0050_ovms")
    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn:
        for t in ("ovms_notice", "ovms_metric_log", "ovms_metric", "app_setting", "notify_log"):
            conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
        conn.execute(text("CREATE TABLE app_setting (key text primary key, value jsonb not null, updated_at timestamptz not null default now())"))
        with Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()
    yield URL  # type: ignore[misc]
    with sync.begin() as conn:
        for t in ("ovms_notice", "ovms_metric_log", "ovms_metric", "app_setting"):
            conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
    sync.dispose()


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
def test_messages_are_recorded_and_warnings_sent_once(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.modules.ovms import listener

    sent: list[tuple[str, str]] = []

    async def phone(engine: Any, key: str, title: str, body: str) -> None:
        sent.append((key, body))

    monkeypatch.setattr(listener, "_phone", phone)

    async def go() -> None:
        engine = create_async_engine(db, poolclass=NullPool)
        L = listener.Listener()
        L.engine = engine
        pre = "ovms/+/+/"
        for t, v in [("v/e/on", b"no"), ("v/e/locked", b"no"), ("v/b/soc", b"81"), ("v/b/soc", b"80"), ("x/nl/whatever", b"1")]:
            await L._message(f"ovms/rob/HV66BZD/metric/{t}", v, pre)
        await L._message("ovms/rob/HV66BZD/notify/alert/charge.stopped", b"Charging stopped: interrupted", pre)
        await L._flush()
        await L._message("ovms/rob/HV66BZD/metric/v/b/soc", b"79", pre)
        await L._flush()  # the same warning again: not sent again
        async with engine.connect() as c:
            assert (await c.execute(text("SELECT value FROM ovms_metric WHERE metric = 'v.b.soc'"))).scalar() == "79"
            assert (await c.execute(text("SELECT count(*) FROM ovms_metric WHERE metric = 'x.nl.whatever'"))).scalar() == 1
            # the history keeps only the listed metrics; soc's second change is within a minute, so waits
            assert (await c.execute(text("SELECT metric, value FROM ovms_metric_log ORDER BY id"))).all() == [
                ("v.e.on", "no"), ("v.e.locked", "no"), ("v.b.soc", "80")]
            assert (await c.execute(text("SELECT kind, subtype, text FROM ovms_notice"))).all() == [
                ("alert", "charge.stopped", "Charging stopped: interrupted")]
        await engine.dispose()

    asyncio.run(go())
    assert [k.split(":")[1] for k, _ in sent] == ["notice", "warn"]
    assert sent[1][1] == "HV66BZD: Unlocked while parked"
