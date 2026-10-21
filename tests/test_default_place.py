from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app import assistant, events, pollen
from app.main import app
from app.places import place_or_default

URL = os.environ.get("WA_TEST_DATABASE_URL")
VERSIONS = Path(__file__).parent.parent / "migrations" / "versions"
MIGRATIONS = ["0003_nh_storage", "0006_places_trips"]
TABLES = ["trip", "place", "nh_sign_message", "nh_sign", "nh_event_corridor", "nh_event", "nh_corridor"]


@pytest.fixture
def schema() -> Iterator[None]:
    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn:
        for t in TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        with Operations.context(MigrationContext.configure(conn)):
            for name in MIGRATIONS:
                spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
                assert spec and spec.loader
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.upgrade()
    yield
    with sync.begin() as conn:
        for t in TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
    sync.dispose()


async def _ask(engine: Any, given: str | None = None) -> str:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=engine)))
    return await place_or_default(request, given)  # type: ignore[arg-type]


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_default_prefers_kind_home_then_id_home_then_first(schema: None) -> None:
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        with pytest.raises(HTTPException) as e:
            await _ask(engine)
        assert e.value.status_code == 404
        async with engine.begin() as conn:
            for pid, name, kind in (("aaa", "Aardvark", "other"), ("home", "My place", "other")):
                await conn.execute(
                    text("INSERT INTO place (id, name, kind, lat, lon) VALUES (:i, :n, :k, 51.0, -0.2)"),
                    {"i": pid, "n": name, "k": kind},
                )
        assert await _ask(engine) == "home"  # the id "home" beats the first by name
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO place (id, name, kind, lat, lon) VALUES ('flat', 'Flat', 'home', 51.0, -0.2)")
            )
        assert await _ask(engine) == "flat"  # kind "home" beats everything
        assert await _ask(engine, "aaa") == "aaa"  # an explicit place is never replaced
    finally:
        await engine.dispose()


def test_endpoints_accept_no_place(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "s3cret")
    seen: list[str] = []

    async def fake_default(request: Any, place: str | None) -> str:
        seen.append(place or "")
        return place or "home"

    async def fake_place(request: Any, place: str) -> tuple[str, float, float]:
        assert place == "home"
        return "Home", 51.3, -0.2

    async def fake_history(engine: Any, place: str, today: Any, days: int) -> dict[str, Any]:
        return {"days": [], "total": [], "allergy_risk": [], "allergens": [], "days_recorded": 0}

    for mod in (pollen, events):
        monkeypatch.setattr(mod, "place_or_default", fake_default)
        monkeypatch.setattr(mod, "_place", fake_place)
    monkeypatch.setattr(pollen, "history", fake_history)
    r = TestClient(app).get("/v1/pollen/history", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200 and r.json()["place"] == "Home" and seen == [""]


async def test_assistant_tools_pass_no_place_through(monkeypatch: pytest.MonkeyPatch) -> None:
    got: dict[str, Any] = {}

    async def near(request: Any, **kw: Any) -> dict[str, Any]:
        got.update(kw)
        return {"place": "Home", "allergy_risk_label": [None], "allergens": []}

    monkeypatch.setattr(assistant, "pollen_near", near)
    out = await assistant.run_tool(None, "pollen", {})  # type: ignore[arg-type]
    assert got["place"] is None and out["text"].startswith("Near Home")
    for name in ("pollen", "events_near", "fuel_cheapest"):
        tool = assistant.TOOLS[name]
        assert "required" not in tool.schema
