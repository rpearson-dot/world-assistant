"""The events sync: provider events become stored rows, and ones that cannot be placed are skipped."""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.modules.events.sources.localevents.sync import to_row

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

    from app.modules.events.sources.localevents import sync as ls

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


def test_an_event_knows_its_venue_and_when_it_ends() -> None:
    from app.modules.events.sources.localevents.sync import venue_id

    row = to_row("skiddle", {**GOOD, "postcode": "kt1 1aa", "end_date": "2026-11-01T23:00:00+00:00"}, NOW)
    assert row and row["venue_id"] == "hall:KT11AA" and row["ends"] == datetime(2026, 11, 1, 23, tzinfo=UTC)
    assert to_row("skiddle", {**GOOD, "end_date": "2026-11-01"}, NOW)["ends"] is None  # type: ignore[index]  # no time: unknown
    # the same venue however a source spells it; without a postcode, where it is to about 100 m
    assert venue_id("The Rose  Theatre", "KT1 1HL", 51.4, -0.3) == venue_id("the rose theatre", "kt11hl", 0, 0)
    assert venue_id("Rose Theatre", None, 51.40871, -0.30659) == "rose-theatre:51.409,-0.307"
    assert venue_id("Rose Theatre", "not a postcode", 51.40871, -0.30659) == "rose-theatre:51.409,-0.307"
    assert venue_id("", "KT1 1HL", 51.4, -0.3) is None


DB = __import__("os").environ.get("WA_TEST_DATABASE_URL")


@__import__("pytest").mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_venues_are_saved_kept_and_dropped_after_a_year() -> None:
    import importlib.util
    from datetime import timedelta
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app.modules.events.sources.localevents import sync as ls

    eng = sa.create_engine(DB)  # type: ignore[arg-type]
    with eng.begin() as conn:
        for t in ("event_venue", "local_event"):
            conn.execute(sa.text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        with Operations.context(MigrationContext.configure(conn)):
            for f in ("0028_local_event", "0043_event_venue"):
                spec = importlib.util.spec_from_file_location(f, Path("migrations/versions") / f"{f}.py")
                m = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
                spec.loader.exec_module(m)  # type: ignore[union-attr]
                m.upgrade()
    try:
        row = ls.to_row("skiddle", {**GOOD, "postcode": "KT1 1AA", "end_date": "2026-11-01T23:00:00+00:00"}, NOW)
        with eng.begin() as conn:
            conn.execute(ls.UPSERT, [row])
            v = {"id": row["venue_id"], "name": "Hall", "town": None, "postcode": "KT1 1AA", "lat": 51.4, "lon": -0.3}  # type: ignore[index]
            conn.execute(ls.VENUES, [{**v, "seen": NOW - timedelta(days=400)}, {**v, "id": "old:KT99ZZ", "seen": NOW - timedelta(days=400)}])
            conn.execute(ls.VENUES, [{**v, "seen": NOW}])  # seen again: kept, first seen stays
            conn.execute(sa.text("DELETE FROM event_venue WHERE last_seen < :before"),
                         {"before": NOW - timedelta(days=ls.VENUE_DAYS)})
            got = conn.execute(sa.text("SELECT id, first_seen < last_seen, geog IS NOT NULL FROM event_venue")).all()
            ev = conn.execute(sa.text("SELECT venue_id, ends FROM local_event")).one()
        assert [tuple(r) for r in got] == [("hall:KT11AA", True, True)]  # the year-old one is gone
        assert ev.venue_id == "hall:KT11AA" and ev.ends == datetime(2026, 11, 1, 23, tzinfo=UTC)
    finally:
        with eng.begin() as conn:
            for t in ("event_venue", "local_event"):
                conn.execute(sa.text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        eng.dispose()
