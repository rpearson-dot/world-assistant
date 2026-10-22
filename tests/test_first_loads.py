"""On a new install the worker loads stations, stops and postcodes now, not at tonight's run."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

from app import worker
from app.config import get_settings


class FakeConn:
    def __init__(self, filled: set[str]) -> None:
        self.filled = filled

    async def execute(self, stmt: Any) -> Any:
        sql = str(stmt)
        table = sql.split("FROM ")[1].split(")")[0].strip()
        if table == "postcode" and "missing" in self.filled:
            raise RuntimeError("relation postcode does not exist")
        return type("R", (), {"scalar_one": lambda _s: table in self.filled})()


class FakeEngine:
    def __init__(self, filled: set[str]) -> None:
        self.filled = filled

    @asynccontextmanager
    async def connect(self) -> Any:
        yield FakeConn(self.filled)


class FakeRedis:
    def __init__(self) -> None:
        self.ids: set[str] = set()

    async def enqueue_job(self, job: str, *, _job_id: str, _defer_by: int) -> Any:
        if _job_id in self.ids:
            return None  # arq: a job with this id is already queued or finished recently
        self.ids.add(_job_id)
        return object()


@pytest.fixture
def polls_on(monkeypatch: pytest.MonkeyPatch) -> Any:
    for name in ("FUEL", "NAPTAN", "CODEPOINT"):
        monkeypatch.setenv(f"WA_{name}_POLL_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_empty_tables_are_loaded_at_once_and_only_once(polls_on: None) -> None:
    redis = FakeRedis()
    ctx = {"engine": FakeEngine({"fuel_station"}), "redis": redis}
    assert await worker.first_loads(ctx) == ["naptan_sync", "codepoint_sync"]  # stations are already there
    assert await worker.first_loads(ctx) == []  # a restart does not queue them again


@pytest.mark.asyncio
async def test_switched_off_polls_and_unreadable_tables_are_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WA_NAPTAN_POLL_ENABLED", "true")
    monkeypatch.delenv("WA_FUEL_POLL_ENABLED", raising=False)
    monkeypatch.delenv("WA_CODEPOINT_POLL_ENABLED", raising=False)
    get_settings.cache_clear()
    try:
        ctx = {"engine": FakeEngine(set()), "redis": FakeRedis()}
        assert await worker.first_loads(ctx) == ["naptan_sync"]
        monkeypatch.setenv("WA_CODEPOINT_POLL_ENABLED", "true")
        get_settings.cache_clear()
        ctx = {"engine": FakeEngine({"missing"}), "redis": FakeRedis()}
        assert await worker.first_loads(ctx) == ["naptan_sync"]  # the postcode table could not be read
    finally:
        get_settings.cache_clear()
