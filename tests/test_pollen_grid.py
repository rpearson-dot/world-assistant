from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Self

import pytest

from app import ha_publish, pollen_grid
from app.pollen_grid import fingerprint, grid_points, nearest, on_land

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def test_land_mask_keeps_britain_and_ni_and_drops_the_sea_and_neighbours() -> None:
    for place in [(51.5, -0.12), (55.95, -3.19), (51.48, -3.18), (54.6, -5.93), (57.48, -4.22), (50.12, -5.54)]:
        assert on_land(*place), place
    for place in [(48.85, 2.35), (53.35, -6.26), (55.0, -10.0), (59.5, -3.0)]:
        assert not on_land(*place), place


def test_lattice_size_follows_spacing_and_stays_on_land() -> None:
    one, half = grid_points(1.0), grid_points(0.5)
    assert 40 <= len(one) <= 65  # about what the provider's own map shows
    assert len(half) > 2.5 * len(one)
    assert len(set(one)) == len(one)
    assert grid_points(0.01) == grid_points(0.25) and grid_points(99) == grid_points(3.0)  # clamped
    assert all(49.5 < a < 59.5 and -9 < b < 2.5 for a, b in one)


def _dates(first: str, n: int = 4) -> list[str]:
    from datetime import date

    d = date.fromisoformat(first)
    return [(d + timedelta(days=i)).isoformat() for i in range(n)]


def _pt(lat: float, lon: float, age_days: int = 0) -> dict[str, Any]:
    first = (NOW - timedelta(days=age_days)).date().isoformat()
    return {"lat": lat, "lon": lon, "fetched_at": NOW - timedelta(days=age_days), "changed_at": NOW,
            "forecast": {"dates": _dates(first), "allergy_risk": [1, 2, 3, 4], "allergy_risk_label": ["a", "b", "c", "d"],
                         "allergens": [{"name": "Birch", "levels": [0, 1, 2, 3], "labels": ["n", "l", "m", "h"]}]}}  # fmt: skip


def test_nearest_picks_the_closest_recent_point_within_range() -> None:
    pts = [_pt(51.5, -0.5), _pt(52.5, -1.5), _pt(55.5, -3.5)]
    p, d = nearest(pts, 52.4, -1.4, NOW) or ({}, 0)
    assert p["lat"] == 52.5 and d < 20
    assert nearest(pts, 56.9, -3.5, NOW) is None or nearest(pts, 56.9, -3.5, NOW)[1] <= 150  # type: ignore[index]
    assert nearest(pts, 40.0, 10.0, NOW) is None  # too far from anything
    assert nearest([_pt(51.5, -0.5, age_days=4)], 51.5, -0.5, NOW) is None  # no longer covers today
    assert nearest([], 51.5, -0.5, NOW) is None


def test_a_forecast_fetched_days_ago_is_read_from_today_onwards() -> None:
    p, _ = nearest([_pt(51.5, -0.5, age_days=2)], 51.5, -0.5, NOW) or ({}, 0)
    f = p["forecast"]
    assert f["dates"][0] == "2026-10-21" and len(f["dates"]) == 2  # two of its four days are left
    assert f["allergy_risk"] == [3, 4] and f["allergy_risk_label"] == ["c", "d"]
    assert f["allergens"][0]["levels"] == [2, 3] and f["allergens"][0]["labels"] == ["m", "h"]
    assert nearest([_pt(51.5, -0.5, age_days=3)], 51.5, -0.5, NOW) is not None  # the last day still counts
    future = {**_pt(51.5, -0.5), "forecast": {"dates": ["2026-10-30"], "allergy_risk": [1], "allergens": []}}
    assert nearest([future], 51.5, -0.5, NOW) is None


def test_fingerprint_changes_only_with_the_forecast() -> None:
    a = {"dates": ["d"], "allergens": [{"id": 1, "levels": [1, 2]}], "allergy_risk": [3.0], "allergy_risk_hourly": [[1]]}
    assert fingerprint(a) == fingerprint({**a, "allergy_risk_hourly": [[9]]})  # hourly is not kept
    assert fingerprint(a) != fingerprint({**a, "allergy_risk": [4.0]})


def test_home_assistant_pollen_tiles_carry_positions_and_no_home() -> None:
    pts = [
        {"lat": 51.5, "lon": -0.5, "dates": ["d"], "allergy_risk": [3], "allergy_risk_label": ["Low"],
         "allergens": [{"name": "Birch", "levels": [1], "labels": ["low"]}], "changed_at": "t"},
        {"lat": 51.5, "lon": -1.5, "dates": ["d"], "allergy_risk": [2], "allergy_risk_label": ["Low"], "allergens": []},
        {"lat": 56.5, "lon": -3.5, "dates": ["d"], "allergy_risk": [1], "allergy_risk_label": ["None"], "allergens": []},
    ]  # fmt: skip
    d = ha_publish.pollen_device(pts)
    tiles = [s for s in d.sensors if s.key.startswith("tile_")]
    assert d.id == "pollen" and sorted(s.state for s in tiles) == [1, 2]
    assert len(d.sensors) == len(tiles) + 3 and all("-" not in s.key for s in d.sensors)  # plus one sensor per point
    rows = [r for s in tiles for r in s.attributes["points"]]
    assert {(r["lat"], r["lon"]) for r in rows} == {(51.5, -0.5), (51.5, -1.5), (56.5, -3.5)}
    birch = next(r for r in rows if r["allergens"])
    assert birch["allergens"] == [{"name": "Birch", "levels": [1]}] and birch["risk"] == [3]


class _Redis:
    """Just enough of Redis for the limiter and the daily counter."""

    def __init__(self, used: int = 0) -> None:
        self.n = used

    def register_script(self, _lua: str) -> Any:
        async def run(*a: Any, **k: Any) -> int:
            return 0

        return run

    async def get(self, k: str) -> str | None:
        return str(self.n) if self.n else None

    async def incr(self, k: str) -> int:
        self.n += 1
        return self.n

    async def decr(self, k: str) -> int:
        self.n -= 1
        return self.n

    async def expire(self, k: str, s: int) -> None: ...


def _patch(monkeypatch: pytest.MonkeyPatch, *, fail: bool = False, have: list[dict[str, Any]] | None = None) -> list[Any]:
    from app import jobs
    from app.integrations.pollen.client import PollenError

    calls: list[Any] = []

    class Fake:
        def __init__(self, api_key: str, base: str = "", limiter: Any = None, **k: Any) -> None:
            self.limiter = limiter

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

        async def forecast(self, lat: float, lon: float, *a: Any, **k: Any) -> dict[str, Any]:
            await self.limiter.acquire()  # the real client does this before each request
            calls.append((lat, lon))
            if fail:
                raise PollenError("HTTP 429")
            return {"dates": ["d"], "allergens": [], "allergy_risk": [1]}

    async def fake_tracked(engine: Any, name: str, run: Any) -> Any:
        return await run()

    async def fake_load(engine: Any) -> list[dict[str, Any]]:
        return have or []

    async def fake_store(*a: Any, **k: Any) -> None: ...

    async def fake_prune(*a: Any, **k: Any) -> None: ...

    monkeypatch.setattr(pollen_grid, "PollenClient", Fake)
    monkeypatch.setattr(pollen_grid, "store", fake_store)
    monkeypatch.setattr(pollen_grid, "load", fake_load)
    monkeypatch.setattr(pollen_grid, "prune", fake_prune)
    monkeypatch.setattr(jobs, "tracked", fake_tracked)
    monkeypatch.setenv("POLLEN_API_KEY", "k")
    return calls


@pytest.mark.asyncio
async def test_sync_gives_up_after_repeated_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch(monkeypatch, fail=True)
    out = await pollen_grid.pollen_grid_sync({"engine": object(), "redis": _Redis()}, pause=0)
    assert out["gave_up"] is True and out["ok"] == 0
    assert len(calls) == pollen_grid.MAX_FAILURES_IN_A_ROW


@pytest.mark.asyncio
async def test_sync_does_a_limited_number_of_the_stalest_points_each_run(monkeypatch: pytest.MonkeyPatch) -> None:
    pts = grid_points(1.0)
    fresh = pts[:3]  # fetched a moment ago; everything else was never fetched
    have = [{"lat": a, "lon": b, "fetched_at": NOW} for a, b in fresh]
    calls = _patch(monkeypatch, have=have)
    monkeypatch.setenv("WA_POLLEN_GRID_CALLS_PER_RUN", "5")
    from app.config import get_settings

    get_settings.cache_clear()
    out = await pollen_grid.pollen_grid_sync({"engine": object(), "redis": _Redis()}, pause=0)
    get_settings.cache_clear()
    assert out["ok"] == 5 and len(calls) == 5
    assert not set(calls) & set(fresh)  # the freshest points wait their turn


@pytest.mark.asyncio
async def test_the_grid_leaves_part_of_the_daily_budget_for_other_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch(monkeypatch)
    monkeypatch.setenv("WA_POLLEN_GRID_CALLS_PER_RUN", "50")
    from app.config import get_settings

    get_settings.cache_clear()
    out = await pollen_grid.pollen_grid_sync({"engine": object(), "redis": _Redis(used=30)}, pause=0)
    get_settings.cache_clear()
    assert out["budget_stop"] is True and len(calls) == 0  # 36 allowed, 30 used, 6 kept back
    redis = _Redis(used=20)
    calls = _patch(monkeypatch)
    out = await pollen_grid.pollen_grid_sync({"engine": object(), "redis": redis}, pause=0)
    assert len(calls) == 10 and redis.n == 30  # stops when only the reserve is left


@pytest.mark.asyncio
async def test_no_pollen_request_is_made_once_the_day_is_used_up() -> None:
    from app.pollen_budget import PollenBudgetError, PollenLimiter

    redis = _Redis(used=36)
    limiter = PollenLimiter(redis, 36)
    with pytest.raises(PollenBudgetError):
        await limiter.acquire()
    assert redis.n == 36  # the refused call is not counted
    redis.n = 35
    await limiter.acquire()
    assert redis.n == 36 and await limiter.remaining() == 0
