"""Boundary-Line and Open USRN: reading a GeoPackage, loading it safely, and asking which area a point is in."""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import struct
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.integrations import osgpkg
from app.integrations.boundaryline import sync as boundary
from app.integrations.usrn import sync as usrn

URL = os.environ.get("WA_TEST_DATABASE_URL")
VERSIONS = Path(__file__).parent.parent / "migrations" / "versions"
needs_db = pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")

# Kingston upon Thames, on the Ordnance Survey grid (about 51.41 N, 0.31 W), and a square 4 km across.
E, N = 518000, 168000


def square(e: float, n: float, size: float = 2000.0) -> bytes:
    """A polygon as little-endian WKB."""
    ring = [(e, n), (e + size, n), (e + size, n + size), (e, n + size), (e, n)]
    body = struct.pack("<I", len(ring)) + b"".join(struct.pack("<dd", x, y) for x, y in ring)
    return b"\x01" + struct.pack("<II", 3, 1) + body


def line(e: float, n: float) -> bytes:
    return b"\x01" + struct.pack("<II", 2, 2) + struct.pack("<dddd", e, n, e + 300, n + 100)


def blob(wkb: bytes, srs: int = 27700, envelope: int = 0) -> bytes:
    """A GeoPackage geometry: the 'GP' header (little-endian, optional envelope), then the WKB."""
    flags = 0x01 | (envelope << 1)
    return b"GP\x00" + bytes([flags]) + struct.pack("<i", srs) + b"\x00" * {0: 0, 1: 32}[envelope] + wkb


def make_gpkg(path: Path, layers: dict[str, list[tuple[dict[str, object], bytes]]]) -> Path:
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE gpkg_contents (table_name TEXT, data_type TEXT)")
    db.execute("CREATE TABLE gpkg_geometry_columns (table_name TEXT, column_name TEXT, geometry_type_name TEXT, srs_id INT)")
    for table, feats in layers.items():
        cols = list(feats[0][0]) if feats else ["name"]
        db.execute(f'CREATE TABLE "{table}" (fid INTEGER PRIMARY KEY, geom BLOB, ' + ", ".join(f'"{c}" TEXT' for c in cols) + ")")
        db.execute("INSERT INTO gpkg_contents VALUES (?, 'features')", (table,))
        db.execute("INSERT INTO gpkg_geometry_columns VALUES (?, 'geom', 'MULTIPOLYGON', 27700)", (table,))
        for attrs, geom in feats:
            db.execute(f'INSERT INTO "{table}" (geom, ' + ", ".join(f'"{c}"' for c in attrs) + ") VALUES (?, "
                       + ", ".join("?" for _ in attrs) + ")", (geom, *attrs.values()))
    db.commit()
    db.close()
    return path


def zipped(gpkg: Path) -> Path:
    z = gpkg.with_suffix(".zip")
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("Doc/licence.txt", "OGL")
        f.write(gpkg, f"Data/{gpkg.name}")
    return z


def test_a_geometry_blob_gives_its_wkb_and_srs() -> None:
    w = square(E, N)
    assert osgpkg.wkb_of(blob(w)) == (w, 27700)
    assert osgpkg.wkb_of(blob(w, 4326, envelope=1)) == (w, 4326)  # an envelope in the header is skipped
    assert osgpkg.wkb_of(b"") is None and osgpkg.wkb_of(b"XX" + b"\x00" * 20) is None
    assert osgpkg.wkb_of(b"GP\x00\x11" + struct.pack("<i", 27700) + w) is None  # flagged empty


def test_layers_are_found_and_named_by_kind(tmp_path: Path) -> None:
    g = make_gpkg(tmp_path / "bd.gpkg", {"district_borough_unitary": [({"Name": "A", "Code": "E1"}, blob(square(E, N)))]})
    db = osgpkg.open_db(g)
    (layer,) = osgpkg.layers(db)
    assert layer.table == "district_borough_unitary" and layer.geom == "geom" and layer.srs == 27700
    assert osgpkg.pick(layer.columns, "name") == "Name" and osgpkg.pick(layer.columns, "nope") is None
    ((attrs, wkb, srs),) = osgpkg.features(db, layer, ("Name", "Code"))
    assert attrs == {"Name": "A", "Code": "E1"} and wkb == square(E, N) and srs == 27700
    kinds = {t: boundary.kind_of(t) for t in (
        "district_borough_unitary", "district_borough_unitary_ward", "county", "english_region", "parish",
        "westminster_const", "county_electoral_division", "boundary_line_ceremonial_counties", "community_ward",
        "scotland_and_wales_region", "something_else")}  # fmt: skip
    assert kinds == {"district_borough_unitary": "district", "district_borough_unitary_ward": "ward", "county": "county",
                     "english_region": "region", "parish": "parish", "westminster_const": "constituency",
                     "county_electoral_division": None, "boundary_line_ceremonial_counties": None,
                     "community_ward": None, "scotland_and_wales_region": None, "something_else": None}  # fmt: skip
    with pytest.raises(osgpkg.GpkgError):
        osgpkg.layers(sqlite3.connect(":memory:"))  # an SQLite file that is not a GeoPackage
    db.close()


def test_names_lose_their_ordnance_survey_suffixes() -> None:
    assert boundary.clean_name("Kingston upon Thames London Boro") == "Kingston upon Thames"
    assert boundary.clean_name("Sutton London Boro") == "Sutton"
    assert boundary.clean_name("Surrey County") == "Surrey" and boundary.clean_name("Tandridge District") == "Tandridge"
    assert boundary.clean_name("Bath and North East Somerset") == "Bath and North East Somerset"
    assert boundary.area_id("district", "E09000021", "x") == "district:E09000021"
    assert boundary.area_id("district", None, "Kingston upon Thames") == "district:kingston-upon-thames"
    assert boundary.wanted_kinds("region, County,bogus") == {"region", "county"}


def test_unpacking_takes_the_gpkg_from_the_zip(tmp_path: Path) -> None:
    g = make_gpkg(tmp_path / "bd.gpkg", {"county": [({"Name": "X"}, blob(square(E, N)))]})
    out = osgpkg.unpack(zipped(g), tmp_path / "out")
    assert out.name == "bd.gpkg" and out.exists()
    empty = tmp_path / "e.zip"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("readme.txt", "x")
    with pytest.raises(osgpkg.GpkgError):
        osgpkg.unpack(empty, tmp_path / "o2")


@pytest.fixture
def schema() -> Iterator[None]:
    engine = sa.create_engine(URL)  # type: ignore[arg-type]
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS boundary, usrn CASCADE"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        with Operations.context(MigrationContext.configure(conn)):
            spec = importlib.util.spec_from_file_location("m0027", VERSIONS / "0027_boundary_usrn.py")
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.upgrade()
    yield
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS boundary, usrn CASCADE"))
    engine.dispose()


@needs_db
async def test_boundaries_load_simplified_and_a_point_finds_its_district(
    schema: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(boundary, "MIN_AREAS", {"district": 2})
    g = make_gpkg(tmp_path / "bd.gpkg", {
        "district_borough_unitary": [
            ({"Name": "Kingston upon Thames London Boro", "Code": "E09000021"}, blob(square(E, N))),
            ({"Name": "Elmbridge District (B)", "Code": "E07000207"}, blob(square(E + 2000, N))),  # to its east
            ({"Name": "Kingston upon Thames London Boro", "Code": "E09000021"}, blob(square(E, N + 2000))),  # a detached part
        ],
        "county": [({"Name": "Surrey County", "Code": "E10000030"}, blob(square(E, N, 9000)))],
        "parish": [({"Name": "Some Parish", "Code": "E04000001"}, blob(square(E, N)))],  # not asked for
    })  # fmt: skip
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        out = await boundary.load(engine, g, {"district", "county"}, 20.0)
        assert out["areas"] == {"district": 2, "county": 1}  # the two Kingston pieces are one area
        async with engine.connect() as conn:
            rows = (await conn.execute(text("SELECT id, kind, name FROM boundary ORDER BY id"))).all()
            assert [(r.id, r.name) for r in rows] == [("county:E10000030", "Surrey"), ("district:E07000207", "Elmbridge"),
                                                      ("district:E09000021", "Kingston upon Thames")]  # fmt: skip
            # Kingston's two squares were joined, so a point in the detached part is still in Kingston
            inside = text("SELECT name FROM boundary WHERE kind = 'district' AND ST_Contains(geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))")
            lat, lon = (await conn.execute(text(
                "SELECT ST_Y(p), ST_X(p) FROM (SELECT ST_Transform(ST_SetSRID(ST_MakePoint(:e, :n), 27700), 4326) AS p) q"),
                {"e": E + 1000, "n": N + 3000})).one()
            assert (await conn.execute(inside, {"lat": lat, "lon": lon})).scalar() == "Kingston upon Thames"
            assert 51.3 < lat < 51.5 and -0.4 < lon < -0.2  # the grid reference became latitude and longitude
            geom = (await conn.execute(text("SELECT GeometryType(geom), ST_SRID(geom) FROM boundary LIMIT 1"))).one()
            assert tuple(geom) == ("MULTIPOLYGON", 4326)
        # a second load replaces rather than duplicates, and removes what is no longer in the file
        g2 = make_gpkg(tmp_path / "bd2.gpkg", {"district_borough_unitary": [
            ({"Name": "Kingston upon Thames London Boro", "Code": "E09000021"}, blob(square(E, N))),
            ({"Name": "Sutton London Boro", "Code": "E09000029"}, blob(square(E, N - 4000)))]})  # fmt: skip
        out = await boundary.load(engine, g2, {"district"}, 20.0)
        assert out["removed"] == 1  # Elmbridge is gone; the county, not reloaded here, stays
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT count(*) FROM boundary"))).scalar() == 3
        assert not await boundary.due(engine, {"district", "county"})
        assert await boundary.due(engine, {"district", "ward"})  # a kind that was never loaded
    finally:
        await engine.dispose()


@needs_db
async def test_a_short_file_removes_nothing(schema: None, tmp_path: Path) -> None:
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        async with engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO boundary (id, kind, name, seen, geom) VALUES ('district:old', 'district', 'Old', now(), "
                "ST_Multi(ST_GeomFromText('POLYGON((0 51, 1 51, 1 52, 0 51))', 4326)))"))
        g = make_gpkg(tmp_path / "short.gpkg", {"district_borough_unitary": [({"Name": "A", "Code": "E1"}, blob(square(E, N)))]})
        with pytest.raises(boundary.IncompleteError):  # one district, where Britain has hundreds
            await boundary.load(engine, g, {"district"}, 20.0)
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT count(*) FROM boundary WHERE id = 'district:old'"))).scalar() == 1
    finally:
        await engine.dispose()


@needs_db
async def test_streets_load_by_number_and_a_short_file_is_refused(
    schema: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    g = make_gpkg(tmp_path / "usrn.gpkg", {"openUSRN_street": [
        ({"usrn": "11700123"}, blob(line(E, N))), ({"usrn": "11700124"}, blob(line(E + 500, N))),
        ({"usrn": "not a number"}, blob(line(E, N + 500)))]})  # fmt: skip
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        with pytest.raises(usrn.IncompleteError):
            await usrn.load(engine, g)
        monkeypatch.setattr(usrn, "MIN_STREETS", 2)
        assert (await usrn.load(engine, g))["streets"] == 2
        async with engine.connect() as conn:
            row = (await conn.execute(text("SELECT usrn, GeometryType(geom) FROM usrn ORDER BY usrn LIMIT 1"))).one()
        assert tuple(row) == (11700123, "MULTILINESTRING")
        assert not await usrn.due(engine)
        assert usrn.describe(g)[0]["table"] == "openUSRN_street"
    finally:
        await engine.dispose()
