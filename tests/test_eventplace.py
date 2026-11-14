"""Event listings placed somewhere other than their postcode."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.exc import DBAPIError

from app.modules.events import eventplace as E

HOME = (51.37, -0.247)
POINTS = {"SA11NW": (51.6195, -3.9436), "KT11EU": (51.4093, -0.3062)}  # Swansea Arena; Kingston


def test_a_show_placed_near_home_but_with_a_swansea_postcode_is_moved_there_and_dropped() -> None:
    events = [
        {"id": "a", "name": "Swansea show", "postcode": "SA1 1NW", "lat": 51.38, "lon": -0.25, "distance_km": 1.1},
        {"id": "b", "name": "Kingston gig", "postcode": "kt1 1eu", "lat": 51.4095, "lon": -0.3065, "distance_km": 5.9},
        {"id": "c", "name": "No postcode", "postcode": None, "lat": 51.38, "lon": -0.25},
        {"id": "d", "name": "Unknown postcode", "postcode": "ZZ9 9ZZ", "lat": 51.38, "lon": -0.25},
    ]
    out, moved, dropped = E.correct(events, POINTS, HOME, 8)
    assert [e["id"] for e in out] == ["b", "c", "d"] and (moved, dropped) == (1, 1)
    assert out[0]["distance_km"] == 5.9  # within 10 km of its postcode: left alone

    wide, moved, dropped = E.correct(events, POINTS, HOME, 400)  # a wide search keeps it, where it really is
    swansea = next(e for e in wide if e["id"] == "a")
    assert (moved, dropped) == (1, 0) and swansea["lat"] == 51.6195 and swansea["distance_km"] > 250
    assert "km from its postcode SA1 1NW" in swansea["moved"]

    no_point, *_ = E.correct([{"id": "e", "postcode": "KT1 1EU", "lat": None, "lon": None}], POINTS, HOME, 8)
    assert no_point[0]["lat"] == 51.4093  # a listing without a position gets its postcode's


class _Broken:
    def connect(self) -> Any:
        raise DBAPIError("SELECT", {}, Exception("no table"))


def test_without_the_postcode_table_listings_are_left_as_they_came() -> None:
    events = [{"id": "a", "postcode": "SA1 1NW", "lat": 51.38, "lon": -0.25}]
    assert asyncio.run(E.check(_Broken(), events, HOME, 8)) == (events, 0, 0)
    assert asyncio.run(E.check(None, events, HOME, 8)) == (events, 0, 0)


def test_a_listing_without_a_postcode_is_checked_against_its_towns_centre() -> None:
    towns = {"kingston upon thames": (51.41, -0.30)}
    events = [
        {"id": "f", "name": "Placed in Swansea", "town": "Kingston  upon Thames", "lat": 51.62, "lon": -3.94},
        {"id": "g", "name": "Near the centre", "town": "Kingston upon Thames", "lat": 51.40, "lon": -0.29},
        {"id": "h", "name": "Unknown town", "town": "Nowhere", "lat": 51.62, "lon": -3.94},
        {"id": "i", "name": "No position", "town": "Kingston upon Thames", "lat": None, "lon": None},
    ]
    out, moved, dropped = E.correct(events, {}, HOME, 20, towns)
    assert [e["id"] for e in out] == ["f", "g", "h", "i"] and (moved, dropped) == (1, 0)
    assert out[0]["lat"] == 51.41 and "from Kingston  upon Thames; shown at the town's centre" in out[0]["moved"]
    assert out[3]["lat"] is None  # a town centre is too rough to give a position
