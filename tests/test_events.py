

def test_todays_events_that_have_started_are_not_shown() -> None:
    from datetime import datetime

    from app.modules.events.events import upcoming

    now = datetime(2026, 11, 14, 19, 30)
    out = {"events": [
        {"id": "a", "date": "2026-11-14", "doors_open": "19:00"},  # started
        {"id": "b", "date": "2026-11-14", "doors_open": "20:00"},
        {"id": "c", "date": "2026-11-14", "doors_open": None},  # no time: kept
        {"id": "d", "date": "2026-11-14", "end_date": "2026-11-16", "doors_open": "10:00"},  # runs on
        {"id": "e", "date": "2026-11-15", "doors_open": "09:00"},
    ]}
    got = upcoming(out, now)
    assert [e["id"] for e in got["events"]] == ["b", "c", "d", "e"] and got["started"] == 1
    assert upcoming({"events": [{"id": "b", "date": "2026-11-14", "doors_open": "20:00"}]}, now) == {"events": [{"id": "b", "date": "2026-11-14", "doors_open": "20:00"}]}
