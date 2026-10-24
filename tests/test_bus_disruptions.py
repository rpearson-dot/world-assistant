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


# --- end to end: the national file, stored near your places, through the API ---------------------------------------

import importlib.util
import os
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

URL = os.environ.get("WA_TEST_DATABASE_URL")
SX = """<?xml version="1.0"?><Siri xmlns="http://www.siri.org.uk/siri"><ServiceDelivery><SituationExchangeDelivery>
<Situations>
 <PtSituationElement><ParticipantRef>Leics</ParticipantRef><SituationNumber>near</SituationNumber><Progress>open</Progress>
  <ValidityPeriod><StartTime>{start}</StartTime><EndTime>{end}</EndTime></ValidityPeriod>
  <MiscellaneousReason>roadworks</MiscellaneousReason><Planned>true</Planned><Summary>Measham Road closed</Summary>
  <Consequences><Consequence><Affects><Networks><AffectedNetwork><AffectedLine><PublishedLineName>19A</PublishedLineName>
  </AffectedLine></AffectedNetwork></Networks><StopPoints><AffectedStopPoint><StopPointRef>2600A</StopPointRef>
  <Location><Longitude>-1.536</Longitude><Latitude>52.686</Latitude></Location></AffectedStopPoint></StopPoints>
  </Affects></Consequence></Consequences></PtSituationElement>
 <PtSituationElement><ParticipantRef>TfGM</ParticipantRef><SituationNumber>far</SituationNumber>
  <Summary>Manchester</Summary><Consequences><Consequence><Affects><StopPoints><AffectedStopPoint>
  <StopPointRef>1800X</StopPointRef><Location><Longitude>-2.24</Longitude><Latitude>53.48</Latitude></Location>
  </AffectedStopPoint></StopPoints></Affects></Consequence></Consequences></PtSituationElement>
</Situations></SituationExchangeDelivery></ServiceDelivery></Siri>"""


@pytest.fixture
def schema() -> Iterator[None]:
    sync_engine = sa.create_engine(URL)  # type: ignore[arg-type]
    path = Path(__file__).parent.parent / "migrations" / "versions" / "0032_bus_disruption.py"
    spec = importlib.util.spec_from_file_location("m0032", path)
    assert spec and spec.loader
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    with sync_engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        for t in ("bus_disruption", "naptan_stop", "place"):
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        conn.execute(text("CREATE TABLE place (id text primary key, name text, kind text, lat float8, lon float8)"))
        conn.execute(text("INSERT INTO place VALUES ('appleby', 'Appleby Magna', 'other', 52.684, -1.540)"))
        conn.execute(text("CREATE TABLE naptan_stop (atco text primary key, lat float8, lon float8)"))
        with Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()
    yield
    with sync_engine.begin() as conn:
        for t in ("bus_disruption", "naptan_stop", "place"):
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
    sync_engine.dispose()


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_read_stored_and_listed(schema: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings
    from app.integrations.bods import disruptions_sync
    from app.main import app

    monkeypatch.setenv("API_TOKEN", "tok")
    get_settings.cache_clear()
    now = datetime.now(UTC)
    body = SX.format(start=(now - timedelta(days=2)).isoformat(), end=(now + timedelta(days=5)).isoformat()).encode()

    async def fake_fetch(key: str) -> tuple[str, bytes, list[str]]:
        return "https://data.bus-data.dft.gov.uk/api/v1/siri-sx/", body, []

    monkeypatch.setattr(disruptions_sync, "fetch", fake_fetch)
    engine = create_async_engine(URL, poolclass=NullPool)  # type: ignore[arg-type]
    assert await disruptions_sync.sync(engine, "k", now) == {"read": 2, "kept": 1, "publishers": 2}
    assert await disruptions_sync.sync(engine, "k", now) == {"read": 2, "kept": 1, "publishers": 2}  # replaced, not doubled
    app.state.engine = engine
    c = TestClient(app)
    h = {"Authorization": "Bearer tok"}
    got = c.get("/v1/bus-disruptions", headers=h).json()
    assert got["count"] == 1 and got["disruptions"][0]["summary"] == "Measham Road closed"
    assert got["disruptions"][0]["near_place"] == "appleby" and got["disruptions"][0]["lines"] == ["19A"]
    assert c.get("/v1/bus-disruptions?line=19a", headers=h).json()["count"] == 1
    assert c.get("/v1/bus-disruptions?line=7", headers=h).json()["count"] == 0
    await engine.dispose()
    get_settings.cache_clear()
