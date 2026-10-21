"""Changing a bus tracker's times from Home Assistant."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import bus
from app.auth import require_token
from app.integrations.bus import store

CFG = {
    "line": "213",
    "stops": [{"key": "a", "sid": "490000001A", "label": "A"}, {"key": "b", "sid": "490000002B", "label": "B"}],
    "target": {"board": "a", "alight": "b"},
    "earliest": "07:00", "deadline": "08:10",
}  # fmt: skip


class _Engine:
    @asynccontextmanager
    async def begin(self) -> AsyncIterator[object]:
        yield object()


def _client(monkeypatch: pytest.MonkeyPatch, trackers: dict[str, dict[str, Any]]) -> tuple[TestClient, dict[str, Any]]:
    saved: dict[str, Any] = {}

    async def list_trackers(conn: Any, only_enabled: bool = False) -> list[dict[str, Any]]:
        return [{"id": k, "config": v, "enabled": True} for k, v in trackers.items()]

    async def get_tracker(conn: Any, tid: str) -> dict[str, Any] | None:
        return {"id": tid, "config": trackers[tid], "enabled": True} if tid in trackers else None

    async def update_config(conn: Any, tid: str, config: dict[str, Any]) -> None:
        saved[tid] = config

    monkeypatch.setattr(store, "list_trackers", list_trackers)
    monkeypatch.setattr(store, "get_tracker", get_tracker)
    monkeypatch.setattr(store, "update_config", update_config)
    app = FastAPI()
    app.state = SimpleNamespace(engine=_Engine())  # type: ignore[assignment]
    app.include_router(bus.router)
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app), saved


def test_the_deadline_can_be_changed_and_only_the_config_is_touched(monkeypatch: pytest.MonkeyPatch) -> None:
    client, saved = _client(monkeypatch, {"main": dict(CFG)})
    r = client.post("/v1/bus/settings", json={"deadline": "08:30"}).json()
    assert r["ok"] is True and r["tracker"] == "main" and r["deadline"] == "08:30"
    assert saved["main"]["deadline"] == "08:30" and saved["main"]["earliest"] == "07:00" and saved["main"]["line"] == "213"


def test_blank_fields_from_home_assistant_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    client, saved = _client(monkeypatch, {"main": dict(CFG)})
    r = client.post("/v1/bus/settings", json={"deadline": "", "earliest": "06:45", "show_until": ""}).json()
    assert r["ok"] is True and saved["main"]["earliest"] == "06:45" and saved["main"]["deadline"] == "08:10"
    assert client.post("/v1/bus/settings", json={"deadline": "", "earliest": "", "show_until": ""}).json()["ok"] is False


def test_bad_times_and_ambiguous_trackers_get_a_message_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client, saved = _client(monkeypatch, {"main": dict(CFG)})
    assert client.post("/v1/bus/settings", json={"deadline": "06:00"}).json()["message"].startswith("the earliest time")
    assert not saved
    assert client.post("/v1/bus/settings", json={"deadline": "25:99"}).status_code == 422
    client, _ = _client(monkeypatch, {"a": dict(CFG), "b": dict(CFG)})
    r = client.post("/v1/bus/settings", json={"deadline": "08:30"}).json()
    assert r["ok"] is False and "a, b" in r["message"]
    assert client.post("/v1/bus/settings", json={"tracker": "zzz", "deadline": "08:30"}).json()["ok"] is False
    empty, _ = _client(monkeypatch, {})
    assert "no bus tracker" in empty.post("/v1/bus/settings", json={"deadline": "08:30"}).json()["message"]
