"""NaPTAN: reading the register, loading it safely, and asking for stops. DB tests need Postgres."""

from __future__ import annotations

import csv
import importlib.util
import io
import os
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.integrations.naptan import sync
from app.integrations.naptan.normalise import KINDS, STATION_TYPES, parse_row
from app.main import app

URL = os.environ.get("WA_TEST_DATABASE_URL")
VERSIONS = Path(__file__).parent.parent / "migrations" / "versions"

HEADER = ["ATCOCode", "NaptanCode", "CommonName", "Street", "Indicator", "LocalityName", "Longitude",
          "Latitude", "StopType", "Status"]  # fmt: skip


def row(**kw: str) -> dict[str, str]:
    base = {"ATCOCode": "490001346C", "NaptanCode": "bbdgmwp", "CommonName": "Home Road", "Street": "High St",
            "Indicator": "Stop C", "LocalityName": "Hackney", "Longitude": "-0.0553", "Latitude": "51.5455",
            "StopType": "BCT", "Status": "active"}  # fmt: skip
    return {**base, **kw}


# --- the rules, with no database ------------------------------------------------------------------


def test_a_good_row_is_a_stop() -> None:
    stop = parse_row(row())
    assert stop is not None
    assert stop["atco"] == "490001346C" and stop["kind"] == "bus_stop"
    assert (stop["lat"], stop["lon"]) == (51.5455, -0.0553)
    assert stop["indicator"] == "Stop C" and stop["locality"] == "Hackney"


@pytest.mark.parametrize("bad", [
    row(ATCOCode=""), row(CommonName="  "), row(StopType="TXR"), row(StopType=""),   # nothing a bus calls at
    row(Status="inactive"), row(Status="pending"),                                    # not in service
    row(Latitude=""), row(Longitude="nope"), row(Latitude="0", Longitude="0"),        # no usable position
    row(Latitude="40.0"), row(Longitude="30.0"),                                      # outside the UK
])  # fmt: skip
def test_a_row_that_cannot_be_trusted_is_left_out(bad: dict[str, str]) -> None:
    assert parse_row(bad) is None


def test_a_blank_status_counts_as_active_and_case_does_not_matter() -> None:
    assert parse_row(row(Status="")) is not None
    assert parse_row(row(Status="Active")) is not None
    assert parse_row(row(StopType="bct")) is not None


def test_every_station_type_is_a_known_stop_type() -> None:
    assert set(STATION_TYPES) <= set(KINDS)
    assert {KINDS[t] for t in STATION_TYPES} == {"bus_station", "rail", "metro", "ferry", "air"}


# --- loading it -----------------------------------------------------------------------------------


def write_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:  # NaPTAN files carry a BOM
        writer = csv.DictWriter(handle, fieldnames=HEADER, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def many(n: int, prefix: str = "S") -> list[dict[str, str]]:
    return [row(ATCOCode=f"{prefix}{i}", CommonName=f"Stop {i}") for i in range(n)]


def test_read_rows_handles_a_bom_a_zip_and_skips_junk(tmp_path: Path) -> None:
    plain = write_csv(tmp_path / "a.csv", [row(), row(ATCOCode="X", StopType="TXR"), row(ATCOCode="Y")])
    assert [s["atco"] for s in sync.read_rows(plain)] == ["490001346C", "Y"]

    zipped = tmp_path / "b.zip"
    with zipfile.ZipFile(zipped, "w") as z:
        z.writestr("naptan.csv", plain.read_bytes())
    assert [s["atco"] for s in sync.read_rows(zipped)] == ["490001346C", "Y"]


@pytest.fixture
def schema() -> Iterator[None]:
    engine = sa.create_engine(URL)  # type: ignore[arg-type]
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS naptan_stop CASCADE"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        with Operations.context(MigrationContext.configure(conn)):
            spec = importlib.util.spec_from_file_location("m0020", VERSIONS / "0020_naptan.py")
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.upgrade()
    yield
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS naptan_stop CASCADE"))
    engine.dispose()


needs_db = pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")


@needs_db
async def test_a_load_stores_stops_and_a_later_load_replaces_them(
    schema: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sync, "MIN_STOPS", 3)
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        first = write_csv(tmp_path / "1.csv", many(3) + [row(ATCOCode="S0", CommonName="duplicate")])
        assert await sync.load(engine, first) == {"stops": 3, "removed": 0}  # the repeat is ignored

        # next week: S0 renamed, S1 and S2 gone, two new stops
        second = write_csv(tmp_path / "2.csv", [row(ATCOCode="S0", CommonName="Renamed")] + many(2, "N"))
        later = datetime.now(UTC) + timedelta(seconds=5)
        assert await sync.load(engine, second, started=later) == {"stops": 3, "removed": 2}
        async with engine.connect() as conn:
            names = dict((await conn.execute(text("SELECT atco, name FROM naptan_stop"))).all())
        assert names == {"S0": "Renamed", "N0": "Stop 0", "N1": "Stop 1"}
    finally:
        await engine.dispose()


@needs_db
async def test_a_short_file_removes_nothing(schema: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A truncated download must not wipe the register: it can add and update, never delete."""
    monkeypatch.setattr(sync, "MIN_STOPS", 3)
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        await sync.load(engine, write_csv(tmp_path / "1.csv", many(3)))
        with pytest.raises(sync.IncompleteError):
            await sync.load(engine, write_csv(tmp_path / "2.csv", many(1, "Z")),
                            started=datetime.now(UTC) + timedelta(seconds=5))
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT count(*) FROM naptan_stop"))).scalar_one() == 4
    finally:
        await engine.dispose()


@needs_db
def test_asking_for_stops_near_a_point(schema: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "tok")
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    here = {"Latitude": "51.5455", "Longitude": "-0.0553"}
    rows = [row(ATCOCode="near", **here), row(ATCOCode="rail", StopType="RLY", **here),
            row(ATCOCode="far", Latitude="52.4862", Longitude="-1.8904")]

    import asyncio
    monkeypatch.setattr(sync, "MIN_STOPS", 1)
    asyncio.run(sync.load(engine, write_csv(tmp_path / "x.csv", rows)))
    asyncio.run(engine.dispose())

    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        app.state.engine = engine
        c, h = TestClient(app), {"Authorization": "Bearer tok"}
        assert c.get("/v1/stops/near?lat=51.5455&lon=-0.0553").status_code == 401

        got = c.get("/v1/stops/near?lat=51.5455&lon=-0.0553&radius_km=1", headers=h).json()["stops"]
        assert {s["id"] for s in got} == {"near", "rail"}  # Birmingham is out
        assert all(s["km"] < 0.01 for s in got)

        only_rail = c.get("/v1/stops/near?lat=51.5455&lon=-0.0553&radius_km=1&kind=rail", headers=h).json()
        assert [s["id"] for s in only_rail["stops"]] == ["rail"]
        assert c.get("/v1/stops/near?lat=51.5&lon=-0.1&kind=helipad", headers=h).status_code == 422

        one = c.get("/v1/stops/near", headers=h)
        assert one.status_code == 422  # a position is required
        assert c.get("/v1/stops/far", headers=h).json()["name"] == "Home Road"
        assert c.get("/v1/stops/nothing", headers=h).status_code == 404
    finally:
        asyncio.run(engine.dispose())


def test_the_zip_writer_in_these_tests_really_makes_a_zip() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.csv", "x")
    assert buf.getvalue()[:4] == b"PK\x03\x04"  # read_rows keys on this
