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


async def test_sync_reads_every_place_a_page_at_a_time_until_there_is_no_more(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.integrations.localevents import sync as ls

    asked: list[tuple[str, str, int]] = []

    def events(source: str, place: str, n: int, start: int) -> list[dict]:
        return [{"id": f"{place}-{start + i}", "name": "Gig", "date": "2026-10-30", "lat": 51.4, "lon": -0.3}
                for i in range(n)]  # fmt: skip

    class Client:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a) -> None:
            return None

    class Skiddle(Client):
        async def search(self, lat, lon, *, offset=0, **k):
            asked.append(("skiddle", f"{lat}", offset))
            # London has 250; Epsom has 40
            total = 250 if lat == 51.5 else 40
            return {"total": total, "events": events("s", f"{lat}", min(100, total - offset), offset)}

    class Tm(Client):
        async def search(self, lat, lon, *, page=0, **k):
            asked.append(("ticketmaster", f"{lat}", page))
            return {"total": 100, "events": events("t", f"tm{lat}", 100, page * 100)}

    async def spots(engine, n):
        return [("london", 51.5, -0.1), ("epsom", 51.33, -0.27)][:n]

    class Conn:
        async def execute(self, *a, **k):
            return SimpleNamespace(rowcount=0)

    class Begin:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *a) -> None:
            return None

    async def nosleep(_s: float) -> None:
        return None

    monkeypatch.setattr(ls, "get_settings", lambda: SimpleNamespace(
        skiddle_api_key="k", ticketmaster_api_key="k", skiddle_base_url="", ticketmaster_base_url="",
        events_sync_places=20, events_sync_pages=3, events_sync_days=14, events_sync_radius_km=10.0,
        local_tz="Europe/London"))  # fmt: skip
    monkeypatch.setattr(ls, "SkiddleClient", Skiddle)
    monkeypatch.setattr(ls, "TicketmasterClient", Tm)
    monkeypatch.setattr(ls, "places", spots)
    monkeypatch.setattr(ls.asyncio, "sleep", nosleep)
    monkeypatch.setattr(ls, "to_row", lambda source, e, seen: {"id": f"{source}:{e['id']}"})
    out = await ls.sync(SimpleNamespace(begin=Begin))
    assert ("skiddle", "51.5", 200) in asked and ("skiddle", "51.5", 300) not in asked  # three pages, then stop
    assert [a for a in asked if a[0] == "skiddle" and a[1] == "51.33"] == [("skiddle", "51.33", 0)]  # 40: one page
    assert [a[2] for a in asked if a[0] == "ticketmaster" and a[1] == "51.5"] == [0]  # total 100: one page
    assert out["events"] == 250 + 40 + 100 + 100 and out["places"] == 2
