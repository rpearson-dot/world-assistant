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


def _pt(lat: float, lon: float, age_h: float = 1.0) -> dict[str, Any]:
    return {"lat": lat, "lon": lon, "fetched_at": NOW - timedelta(hours=age_h), "changed_at": NOW,
            "forecast": {"dates": ["2026-10-21"], "allergy_risk": [3], "allergens": []}}  # fmt: skip


def test_nearest_picks_the_closest_recent_point_within_range() -> None:
    pts = [_pt(51.5, -0.5), _pt(52.5, -1.5), _pt(55.5, -3.5)]
    p, d = nearest(pts, 52.4, -1.4, NOW) or ({}, 0)
    assert p["lat"] == 52.5 and d < 20
    assert nearest(pts, 56.9, -3.5, NOW) is None or nearest(pts, 56.9, -3.5, NOW)[1] <= 150  # type: ignore[index]
    assert nearest(pts, 40.0, 10.0, NOW) is None  # too far from anything
    assert nearest([_pt(51.5, -0.5, age_h=30)], 51.5, -0.5, NOW) is None  # too old to trust
    assert nearest([], 51.5, -0.5, NOW) is None


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
    assert d.id == "pollen" and sorted(s.state for s in d.sensors) == [1, 2]
    assert all("-" not in s.key and s.key.startswith("tile_") for s in d.sensors)
    rows = [r for s in d.sensors for r in s.attributes["points"]]
    assert {(r["lat"], r["lon"]) for r in rows} == {(51.5, -0.5), (51.5, -1.5), (56.5, -3.5)}
    birch = next(r for r in rows if r["allergens"])
    assert birch["allergens"] == [{"name": "Birch", "levels": [1]}] and birch["risk"] == [3]


@pytest.mark.asyncio
async def test_sync_gives_up_after_repeated_failures_and_stores_nothing_wrong(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.pollen.client import PollenError

    calls = {"n": 0}

    class Fake:
        def __init__(self, *a: Any, **k: Any) -> None: ...

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

        async def forecast(self, *a: Any, **k: Any) -> dict[str, Any]:
            calls["n"] += 1
            raise PollenError("HTTP 429")

    stored: list[Any] = []

    async def fake_store(*a: Any, **k: Any) -> None:
        stored.append(a)

    async def fake_tracked(engine: Any, name: str, run: Any) -> Any:
        return await run()

    from app import jobs

    monkeypatch.setattr(pollen_grid, "PollenClient", Fake)
    monkeypatch.setattr(pollen_grid, "store", fake_store)
    monkeypatch.setattr(jobs, "tracked", fake_tracked)
    monkeypatch.setenv("POLLEN_API_KEY", "k")

    class R:
        def register_script(self, _lua: str) -> Any:
            async def run(*a: Any, **k: Any) -> int:
                return 0

            return run

    out = await pollen_grid.pollen_grid_sync({"engine": object(), "redis": R()}, pause=0)
    assert out["gave_up"] is True and out["ok"] == 0
    assert calls["n"] == pollen_grid.MAX_FAILURES_IN_A_ROW and stored == []
