"""The events sync: provider events become stored rows, and ones that cannot be placed are skipped."""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.integrations.localevents.sync import to_row

NOW = datetime(2026, 10, 23, tzinfo=UTC)
GOOD = {"id": "42", "name": "Gig", "date": "2026-11-01", "lat": "51.4", "lon": -0.3, "venue": "Hall", "doors_open": "19:00"}


def test_an_event_becomes_a_row_with_a_namespaced_id() -> None:
    row = to_row("skiddle", GOOD, NOW)
    assert row and row["id"] == "skiddle:42" and row["day"] == date(2026, 11, 1)
    assert row["lat"] == 51.4 and row["starts"] == "19:00" and row["seen"] == NOW


def test_an_event_without_a_position_date_or_name_is_skipped() -> None:
    assert to_row("skiddle", {**GOOD, "lat": None}, NOW) is None
    assert to_row("skiddle", {**GOOD, "date": "soon"}, NOW) is None
    assert to_row("skiddle", {**GOOD, "name": ""}, NOW) is None
    assert to_row("ticketmaster", {**GOOD, "id": None}, NOW) is None
