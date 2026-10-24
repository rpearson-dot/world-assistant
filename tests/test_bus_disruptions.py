"""Bus disruptions module: which ones are kept (current, near a place), and the module's declaration."""

from __future__ import annotations

from datetime import UTC, datetime

from app.integrations.bods.disruptions import Situation
from app.integrations.bods.disruptions_sync import current, select

NOW = datetime(2026, 10, 23, 15, tzinfo=UTC)
HOME = ("home", 52.684, -1.540)  # Appleby Magna


def sit(number: str, **kw: object) -> Situation:
    return Situation(number=number, publisher="Leics", summary="s " + number, **kw)  # type: ignore[arg-type]


def test_only_current_ones_count() -> None:
    assert current(sit("a", starts="2026-10-20T00:00:00Z", ends="2026-11-01T00:00:00Z"), NOW, 90)
    assert not current(sit("b", ends="2026-10-01T00:00:00Z"), NOW, 90)  # ended
    assert not current(sit("c", progress="closed"), NOW, 90)
    assert current(sit("d", starts="2026-09-01T00:00:00.000Z"), NOW, 90)  # open-ended, recent
    assert not current(sit("e", starts="2025-08-23T11:34:00.000Z"), NOW, 90)  # open-ended since 2025: stale
    assert current(sit("f"), NOW, 90)  # no dates at all: kept


def test_kept_when_a_stop_or_place_is_near_a_saved_place() -> None:
    found = [
        sit("near-stop", stops=["2600A"], lines=["19A"]),
        sit("near-place", places=[(52.70, -1.55)]),
        sit("far", stops=["3800FAR"]),
        sit("unplaced", lines=["7"]),
        sit("ended", stops=["2600A"], ends="2026-01-01T00:00:00Z"),
    ]
    coords = {"2600A": (52.686, -1.536), "3800FAR": (53.48, -2.24)}
    rows = select(found, [HOME, ("work", 51.37, -0.25)], coords, NOW, 25.0, 90)
    by = {r["id"]: r for r in rows}
    assert set(by) == {"Leics:near-stop", "Leics:near-place"}
    assert by["Leics:near-stop"]["near_place"] == "home" and by["Leics:near-stop"]["distance_km"] < 1
    assert by["Leics:near-stop"]["lines"] == ["19A"] and (by["Leics:near-stop"]["lat"], by["Leics:near-stop"]["lon"]) == (52.686, -1.536)
    assert select(found, [("work", 51.37, -0.25)], coords, NOW, 25.0, 90) == []


def test_the_module_is_declared_and_off_by_default() -> None:
    from app.config import get_settings
    from app.modules.builtin import BUILTIN

    m = next(m for m in BUILTIN if m.id == "busdisruptions")
    assert m.keys == ("bods_api_key",) and m.switch == "bus_disruptions_poll_enabled"
    assert "/v1/bus-disruptions" in m.prefixes and m.worker
    assert get_settings().bus_disruptions_poll_enabled is False
