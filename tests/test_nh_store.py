"""Integration tests for storage and polling. Need a real Postgres.

Run with: WA_TEST_DATABASE_URL=postgresql+psycopg://user@localhost:5432/dbname pytest
The tests create and drop their own nh_* tables (PostGIS is not required).
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import Settings
from app.integrations.nationalhighways.client import Page
from app.integrations.nationalhighways.match import Corridor
from app.integrations.nationalhighways.normalise import RoadEvent, Segment, VmsSign
from app.integrations.nationalhighways.poller import poll_live_with, vms_bbox
from app.integrations.nationalhighways.store import (
    clear_unseen,
    load_corridors,
    rematch_all,
    save_events,
    save_signs,
    upsert_corridor,
)
from tests.test_nh_normalise_match import _speed_payload

URL = os.environ.get("WA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")

MIGRATION = Path(__file__).parent.parent / "migrations" / "versions" / "0003_nh_storage.py"
TABLES = ["nh_sign_message", "nh_sign", "nh_event_corridor", "nh_event", "nh_corridor"]

NEAR = Corridor("near", "Near", ((51.30, -0.499), (51.32, -0.499)), buffer_m=500)
FAR = Corridor("far", "Far", ((53.0, -2.0), (53.1, -2.0)), buffer_m=500)


def _run_migration(fn: str) -> None:
    spec = importlib.util.spec_from_file_location("m0003", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        getattr(module, fn)()
    sync.dispose()


@pytest.fixture(scope="module", autouse=True)
def schema() -> Iterator[None]:
    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn:
        for t in TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
    sync.dispose()
    _run_migration("upgrade")
    yield
    _run_migration("downgrade")


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(URL)  # type: ignore[arg-type]
    async with eng.begin() as conn:
        for t in TABLES:
            await conn.execute(text(f"DELETE FROM {t}"))
    yield eng
    await eng.dispose()


def _event(rid: str, status: str = "active", lat: float = 51.31) -> RoadEvent:
    seg = Segment("M25", "clockwise", "M25 clockwise within J11", ((lat, -0.499), (lat + 0.001, -0.499)))
    return RoadEvent("speed", rid, "s", status, None, None, None, "mandatory", 40, (seg,))


async def _count(engine: AsyncEngine, sql: str) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(text(sql))).scalar_one())


async def test_corridor_roundtrip(engine: AsyncEngine) -> None:
    await upsert_corridor(engine, NEAR, source="test")
    await upsert_corridor(engine, FAR, enabled=False)
    loaded = await load_corridors(engine)
    assert [c.id for c in loaded] == ["near"]
    assert loaded[0].centreline == NEAR.centreline
    assert len(await load_corridors(engine, only_enabled=False)) == 2


async def test_events_tagged_cleared_and_revived(engine: AsyncEngine) -> None:
    await upsert_corridor(engine, NEAR)
    await upsert_corridor(engine, FAR)
    t0 = datetime.now(UTC)
    hit = await save_events(engine, [_event("a"), _event("b", lat=60.0)], [NEAR, FAR], "unplanned", t0)
    assert hit == 1
    assert await _count(engine, "SELECT count(*) FROM nh_event_corridor WHERE corridor_id = 'near'") == 1

    # second poll only returns "a": "b" is cleared
    t1 = t0 + timedelta(minutes=5)
    await save_events(engine, [_event("a")], [NEAR, FAR], "unplanned", t1)
    assert await clear_unseen(engine, "unplanned", "speed", t1) == 1
    assert await _count(engine, "SELECT count(*) FROM nh_event WHERE cleared_at IS NOT NULL") == 1
    # a different scope is never touched
    assert await clear_unseen(engine, "planned", "speed", t1) == 0

    # "b" comes back and is live again
    t2 = t1 + timedelta(minutes=5)
    await save_events(engine, [_event("a"), _event("b", lat=60.0)], [NEAR], "unplanned", t2)
    assert await _count(engine, "SELECT count(*) FROM nh_event WHERE cleared_at IS NOT NULL") == 0


async def test_rematch_after_corridor_change(engine: AsyncEngine) -> None:
    t0 = datetime.now(UTC)
    await save_events(engine, [_event("a")], [], "unplanned", t0)
    assert await _count(engine, "SELECT count(*) FROM nh_event_corridor") == 0
    await upsert_corridor(engine, NEAR)
    assert await rematch_all(engine) == 1
    assert await _count(engine, "SELECT count(*) FROM nh_event_corridor") == 1


async def test_sign_history_only_on_change(engine: AsyncEngine) -> None:
    t0 = datetime.now(UTC)

    def sign(lines: tuple[str, ...], set_at: datetime) -> VmsSign:
        return VmsSign("signs/X", "M25", "clockwise", "d", 51.3, -0.5, True, set_at, lines)

    await save_signs(engine, [sign(("QUEUE",), t0)], t0)
    await save_signs(engine, [sign(("QUEUE",), t0)], t0 + timedelta(minutes=5))  # unchanged
    assert await _count(engine, "SELECT count(*) FROM nh_sign_message") == 1
    await save_signs(engine, [sign((), t0 + timedelta(minutes=9))], t0 + timedelta(minutes=10))
    assert await _count(engine, "SELECT count(*) FROM nh_sign_message") == 2
    # same text re-set later (the feed refreshes "last set"): not a change
    await save_signs(engine, [sign((), t0 + timedelta(minutes=14))], t0 + timedelta(minutes=15))
    assert await _count(engine, "SELECT count(*) FROM nh_sign_message") == 2
    # a real change after that is logged
    await save_signs(engine, [sign(("CAUTION",), t0 + timedelta(minutes=19))], t0 + timedelta(minutes=20))
    assert await _count(engine, "SELECT count(*) FROM nh_sign_message") == 3
    assert await _count(engine, "SELECT count(*) FROM nh_sign") == 1


class _FakeClient:
    """Stands in for NationalHighwaysClient: serves canned payloads per path."""

    def __init__(self, bodies: dict[str, dict[str, Any]], *, fail_vms: bool = False) -> None:
        self.bodies = bodies
        self.fail_vms = fail_vms
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def pages(self, path: str, params: dict[str, str], *, max_pages: int = 1):
        self.calls.append((path, params))
        if path.endswith("/vms") and self.fail_vms:
            yield Page(0, path, params, 500, None, 1, 0, None, None, "boom")
            return
        body = self.bodies.get(path, {"situation": []})
        yield Page(0, path, params, 200, None, 1, 10, {"D2Payload": body}, None, None)


async def test_poll_live_end_to_end(engine: AsyncEngine) -> None:
    from app.integrations.nationalhighways import client as c

    await upsert_corridor(engine, NEAR)
    vms = {"vmsControllerStatus": []}
    fake = _FakeClient({c.SPEED_MANAGED_AREAS: _speed_payload(), c.VMS: vms})
    settings = Settings()
    result = await poll_live_with(engine, fake, settings)  # type: ignore[arg-type]
    assert result["speed"]["records"] == 1
    assert result["speed"]["on_corridor"] == 1
    assert result["speed"]["complete"] is True
    # VMS box now comes from the corridor, not the default
    vms_call = next(p for path, p in fake.calls if path == c.VMS)
    assert vms_call["bBox"] == vms_bbox(settings, [NEAR])
    assert vms_call["bBox"] != settings.nh_vms_bbox

    # next poll the feed is empty: the speed record is cleared
    fake2 = _FakeClient({c.VMS: vms})
    result2 = await poll_live_with(engine, fake2, settings)  # type: ignore[arg-type]
    assert result2["speed"]["cleared"] == 1


async def test_failed_fetch_does_not_clear(engine: AsyncEngine) -> None:
    from app.integrations.nationalhighways import client as c

    await upsert_corridor(engine, NEAR)
    settings = Settings()
    await poll_live_with(engine, _FakeClient({c.SPEED_MANAGED_AREAS: _speed_payload(), c.VMS: {}}), settings)  # type: ignore[arg-type]

    class _Failing(_FakeClient):
        async def pages(self, path: str, params: dict[str, str], *, max_pages: int = 1):
            yield Page(0, path, params, 500, None, 1, 0, None, None, "boom")

    result = await poll_live_with(engine, _Failing({}), settings)  # type: ignore[arg-type]
    assert result["speed"]["complete"] is False
    assert result["speed"]["cleared"] == 0
    assert await _count(engine, "SELECT count(*) FROM nh_event WHERE cleared_at IS NULL") == 1


async def test_duplicate_corridor_tags_do_not_fail(engine: AsyncEngine) -> None:
    """Rebuilding a route while the poller tags the same event must not raise a unique violation."""
    await upsert_corridor(engine, NEAR)
    t0 = datetime.now(UTC)
    await save_events(engine, [_event("a")], [NEAR, NEAR], "unplanned", t0)  # same corridor twice
    assert await _count(engine, "SELECT count(*) FROM nh_event_corridor") == 1
    assert await rematch_all(engine) == 1
    assert await rematch_all(engine) == 1
    assert await _count(engine, "SELECT count(*) FROM nh_event_corridor") == 1
