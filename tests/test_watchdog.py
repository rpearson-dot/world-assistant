"""World Assistant watching itself: stopped and failing jobs, disks, backups, and the restore check."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from app import backup, watchdog

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
URL = os.environ.get("WA_TEST_DATABASE_URL")


def _every(minutes: int, n: int, last_ago: timedelta, status: str = "ok") -> list[tuple[datetime, str, str | None]]:
    end = NOW - last_ago
    return [(end - timedelta(minutes=minutes * i), status, None) for i in range(n)][::-1]


def test_a_stopped_job_is_noticed_and_a_quiet_one_is_not() -> None:
    runs = {"fuel_poll": _every(10, 30, timedelta(hours=2)),        # every 10 min, silent for 2 h: stopped
            "ha_sync": _every(5, 30, timedelta(minutes=4)),          # fine
            "nightly": _every(1440, 10, timedelta(hours=20)),        # daily, last ran 20 h ago: fine
            "new_job": _every(5, 3, timedelta(hours=5)),             # too new to judge
            "bikes_sync": _every(10, 30, timedelta(hours=3))}        # its module is off  # fmt: skip
    keys = {p["key"] for p in watchdog.job_problems(runs, NOW, {"bikes_sync"})}
    assert keys == {"stopped:fuel_poll"}


def test_a_job_that_keeps_failing_is_noticed() -> None:
    rs = _every(10, 10, timedelta(minutes=5))
    rs[-3:] = [(t, "error", "HTTPError: 503") for t, _, _ in rs[-3:]]
    p = watchdog.job_problems({"tfl": rs}, NOW)
    assert p == [{"key": "failing:tfl", "text": "tfl has failed 3 times in a row: HTTPError: 503"}]


def test_a_windowed_job_is_judged_by_its_own_longest_gap() -> None:
    # the bus log: a minute at a time for two hours a day, then a day's gap; the same gap again is normal
    day = [NOW - timedelta(days=2) + timedelta(minutes=i) for i in range(120)]
    day2 = [NOW - timedelta(days=1) + timedelta(minutes=i) for i in range(120)]
    rs = [(t, "ok", None) for t in day + day2]
    assert watchdog.job_problems({"buslog": rs}, NOW) == []


def test_backups_and_restore_checks() -> None:
    assert watchdog.backup_problems(None, None, NOW)[0]["key"] == "backup:none"
    assert watchdog.backup_problems(NOW - timedelta(days=3), None, NOW)[0]["key"] == "backup:old"
    assert watchdog.backup_problems(NOW - timedelta(hours=5), {"ok": True}, NOW) == []
    assert watchdog.backup_problems(NOW - timedelta(hours=5), {"ok": False, "error": "x"}, NOW)[0]["key"] == "restore:failed"


@pytest.mark.skipif(not URL, reason="needs WA_TEST_DATABASE_URL")
def test_the_restore_check_restores_into_a_scratch_schema(tmp_path: Path) -> None:
    ci = (URL or "").replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(ci, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS rc_src CASCADE; CREATE SCHEMA rc_src")
        c.execute("""CREATE TABLE rc_src.place (id text PRIMARY KEY, name text);
                     CREATE TABLE rc_src.log (at timestamptz NOT NULL, n int) PARTITION BY RANGE (at);
                     CREATE TABLE rc_src.log_d PARTITION OF rc_src.log DEFAULT;
                     CREATE TABLE rc_src.alembic_version (version_num text); INSERT INTO rc_src.alembic_version VALUES ('0051');
                     INSERT INTO rc_src.place VALUES ('home', 'Home'), ('work', 'Work');
                     INSERT INTO rc_src.log VALUES (now(), 1), (now(), 2), (now(), 3);""")
    try:
        backup.create(tmp_path, schema="rc_src", conninfo=ci)
        out = watchdog.restore_check_sync(ci, tmp_path)
        assert out["ok"] and out["tables"] == 2 and out["rows"] == 5 and out["mismatches"] is None
        with psycopg.connect(ci) as c:  # the scratch schema is gone, the source untouched
            assert c.execute("SELECT count(*) FROM pg_namespace WHERE nspname = 'wa_restore_check'").fetchone() == (0,)
            assert c.execute("SELECT count(*) FROM rc_src.log").fetchone() == (3,)
        assert watchdog.restore_check_sync(ci, tmp_path / "none")["ok"] is False
    finally:
        with psycopg.connect(ci, autocommit=True) as c:
            c.execute("DROP SCHEMA IF EXISTS rc_src CASCADE")
