"""Routes from rides: the same route found across rides (either way round, a little off the line), only from recent
rides, and only once ridden more than the set number of times."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.modules.bikes import ride_routes as RR

NOW = datetime(2026, 11, 14, tzinfo=UTC)
EPSOM, KINGSTON, DORKING = [51.333, -0.267], [51.410, -0.300], [51.232, -0.333]


def _line(a: list[float], b: list[float], wobble: float = 0.0, n: int = 20) -> list[list[float]]:
    return [[a[0] + (b[0] - a[0]) * i / (n - 1) + (wobble if i % 2 else 0), a[1] + (b[1] - a[1]) * i / (n - 1)] for i in range(n)]


def _ride(i: int, route: list[list[float]], days_ago: float, km: float = 9.0, **kw: object) -> dict[str, object]:
    return {"id": f"garmin:{i}", "started": NOW - timedelta(days=days_ago), "km": km, "route": route, "indoor": False,
            "title": "Commute", **kw}


def test_same_route_either_way_round_and_slightly_off() -> None:
    a, b = RR.resample(_line(EPSOM, KINGSTON)), RR.resample(_line(KINGSTON, EPSOM, wobble=0.0008))
    assert RR.same(a, b)
    assert not RR.same(a, RR.resample(_line(EPSOM, DORKING)))
    assert len(RR.resample(_line(EPSOM, KINGSTON), 30)) == 30


def test_common_routes_need_more_than_the_set_number_of_recent_rides() -> None:
    there = _line(EPSOM, KINGSTON)
    rides = [_ride(i, there if i % 2 else list(reversed(there)), days_ago=10 * i) for i in range(6)]
    rides += [_ride(100 + i, _line(EPSOM, DORKING), days_ago=i, km=12, title="Box Hill") for i in range(5)]  # only 5 times
    rides += [_ride(200 + i, there, days_ago=365 * 6 + i) for i in range(10)]  # older than 5 years
    rides.append(_ride(300, there, days_ago=1, indoor=True))
    got = RR.common(rides, NOW)
    assert len(got) == 1 and got[0]["times"] == 6 and got[0]["name"] == "Commute" and got[0]["loop"] is False
    assert RR.common(rides, NOW, more_than=4)[1]["name"] == "Box Hill"
    assert RR.common(rides, NOW, years=10)[0]["times"] == 16
