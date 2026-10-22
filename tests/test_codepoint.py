"""Code-Point Open: reading the file, loading it safely, and looking postcodes up locally."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.geo import Located
from app.integrations.codepoint import sync
from app.main import app
from app.postcodes import local_postcode

URL = os.environ.get("WA_TEST_DATABASE_URL")
VERSIONS = Path(__file__).parent.parent / "migrations" / "versions"
needs_db = pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")

# Buckingham Palace, on the Ordnance Survey grid: about 51.5014 N, 0.1419 W.
PALACE = ("SW1A1AA", "10", "529090", "179645")


def row(pc: str = "SW1A1AA", q: str = "10", e: str = "529090", n: str = "179645") -> list[str]:
    return [pc, q, e, n, "E92000001", "E19000003", "E18000007", "", "E09000033", "E05013806"]


# --- the rules, with no database ------------------------------------------------------------------


def test_a_row_becomes_a_postcode_with_a_spaced_form() -> None:
    p = sync.parse_row(row())
    assert p is not None
    assert (p["pc"], p["postcode"], p["easting"], p["northing"]) == ("SW1A1AA", "SW1A 1AA", 529090, 179645)
    assert p["country"] == "E92000001" and p["district"] == "E09000033" and p["ward"] == "E05013806"


def test_the_postcodes_spacing_in_the_file_does_not_matter() -> None:
    padded = sync.parse_row(row("B1  1AA"))  # short outcodes are padded in some releases
    assert padded is not None and padded["pc"] == "B11AA" and padded["postcode"] == "B1 1AA"
    assert sync.parse_row(row("ab101aa"))["postcode"] == "AB10 1AA"  # type: ignore[index]


@pytest.mark.parametrize("bad", [
    [], ["SW1A1AA"], row("NOTAPOSTCODE"), row("", "10"),
    row(q="90"), row(e="0", n="0"), row(e="x"), row(q="x"),
])  # fmt: skip
def test_a_row_without_a_usable_position_is_left_out(bad: list[str]) -> None:
    assert sync.parse_row(bad) is None


def make_zip(path: Path, files: dict[str, list[list[str]]]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Doc/licence.txt", "licence")
        z.writestr("Doc/Code-Point_Open_Column_Headers.csv", "POSTCODE,QUALITY\n")  # a header file, not data
        for name, rows in files.items():
            z.writestr(f"Data/CSV/{name}.csv", "".join(",".join(f'"{c}"' for c in r) + "\n" for r in rows))
    return path


def test_read_rows_takes_every_data_csv_and_ignores_the_docs(tmp_path: Path) -> None:
    z = make_zip(tmp_path / "x.zip", {"sw": [row()], "ab": [row("AB101AA"), row("AB101AB", q="90")]})
    assert [p["pc"] for p in sync.read_rows(z)] == ["AB101AA", "SW1A1AA"]
    empty = tmp_path / "e.zip"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("Doc/licence.txt", "x")
    with pytest.raises(sync.IncompleteError):
        list(sync.read_rows(empty))


# --- storing and asking ---------------------------------------------------------------------------


@pytest.fixture
def schema() -> Iterator[None]:
    engine = sa.create_engine(URL)  # type: ignore[arg-type]
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS postcode CASCADE"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        with Operations.context(MigrationContext.configure(conn)):
            spec = importlib.util.spec_from_file_location("m0022", VERSIONS / "0022_postcode.py")
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.upgrade()
    yield
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS postcode CASCADE"))
    engine.dispose()


@needs_db
async def test_the_grid_reference_becomes_the_right_latitude_and_longitude(
    schema: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sync, "MIN_POSTCODES", 1)
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        await sync.load(engine, make_zip(tmp_path / "x.zip", {"sw": [row()]}))
        async with engine.connect() as conn:
            lat, lon = (await conn.execute(text("SELECT lat, lon FROM postcode WHERE pc = 'SW1A1AA'"))).one()
        assert lat == pytest.approx(51.5014, abs=0.001) and lon == pytest.approx(-0.1419, abs=0.001)
    finally:
        await engine.dispose()


@needs_db
async def test_a_reload_replaces_and_a_short_file_removes_nothing(
    schema: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sync, "MIN_POSTCODES", 2)
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        first = make_zip(tmp_path / "1.zip", {"a": [row("AB101AA"), row("AB101AB"), row("AB101AB")]})
        assert await sync.load(engine, first) == {"postcodes": 2, "removed": 0}  # the repeat is ignored

        later = datetime.now(UTC) + timedelta(seconds=5)
        second = make_zip(tmp_path / "2.zip", {"a": [row("AB101AA"), row("AB101AC")]})
        assert await sync.load(engine, second, started=later) == {"postcodes": 2, "removed": 1}

        short = make_zip(tmp_path / "3.zip", {"a": [row("ZZ991ZZ")]})
        with pytest.raises(sync.IncompleteError):
            await sync.load(engine, short, started=later + timedelta(seconds=5))
        async with engine.connect() as conn:
            kept = (await conn.execute(text("SELECT count(*) FROM postcode"))).scalar_one()
        assert kept == 3  # AB101AA, AB101AC and the one the short file added; nothing was removed
    finally:
        await engine.dispose()


@needs_db
async def test_the_job_only_downloads_when_the_table_is_empty_or_a_month_old(
    schema: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sync, "MIN_POSTCODES", 1)
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        assert await sync.due(engine) is True  # empty
        await sync.load(engine, make_zip(tmp_path / "x.zip", {"a": [row()]}))
        assert await sync.due(engine) is False  # fresh
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE postcode SET seen = now() - interval '31 days'"))
        assert await sync.due(engine) is True  # stale
    finally:
        await engine.dispose()


@needs_db
async def test_a_local_lookup_answers_or_says_to_ask_someone_else(
    schema: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sync, "MIN_POSTCODES", 1)
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        await sync.load(engine, make_zip(tmp_path / "x.zip", {"a": [row()]}))
        got = await local_postcode(engine, "sw1a 1aa")
        assert isinstance(got, Located) and got.postcode == "SW1A 1AA" and got.lat == pytest.approx(51.5014, abs=0.001)
        assert await local_postcode(engine, "AB10 1AA") is None  # not in the file: postcodes.io's job
        assert await local_postcode(engine, "nonsense") is None  # malformed: the fallback complains
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE postcode"))
        assert await local_postcode(engine, "SW1A 1AA") is None  # no table at all: still just falls back
    finally:
        await engine.dispose()


@needs_db
def test_the_postcode_endpoints(schema: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "tok")
    monkeypatch.setattr(sync, "MIN_POSTCODES", 1)

    async def load() -> None:
        engine = create_async_engine(URL)  # type: ignore[arg-type]
        try:
            rows = [row("SW1A1AA"), row("SW1A1AB", e="529095", n="179650"), row("AB101AA", e="394251", n="806376")]
            await sync.load(engine, make_zip(tmp_path / "x.zip", {"a": rows}))
        finally:
            await engine.dispose()

    asyncio.run(load())
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        app.state.engine = engine
        c, h = TestClient(app), {"Authorization": "Bearer tok"}
        assert c.get("/v1/postcodes/SW1A1AA").status_code == 401
        one = c.get("/v1/postcodes/sw1a 1aa", headers=h).json()
        assert one["postcode"] == "SW1A 1AA" and one["lat"] == pytest.approx(51.5014, abs=0.001)
        assert c.get("/v1/postcodes/ZZ99 9ZZ", headers=h).status_code == 404
        assert c.get("/v1/postcodes/nonsense", headers=h).status_code == 422

        near = c.get(f"/v1/postcodes/near?lat={one['lat']}&lon={one['lon']}&radius_km=1", headers=h).json()
        assert [p["postcode"] for p in near["postcodes"]] == ["SW1A 1AA", "SW1A 1AB"]  # Aberdeen is far
        assert near["postcodes"][0]["km"] < 0.01
    finally:
        asyncio.run(engine.dispose())


def test_the_download_url_and_a_failing_download_are_surfaced(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(403))
    real = httpx.AsyncClient

    monkeypatch.setattr(sync.httpx, "AsyncClient", lambda **kw: real(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}))
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(sync.download("https://example.invalid/codepo", tmp_path / "x.zip"))
