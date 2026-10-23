"""Reading an Ordnance Survey GeoPackage with nothing but the standard library.

A GeoPackage is an SQLite file. Each feature table has a geometry column holding the geometry as
well-known binary (WKB) behind a small header, which PostGIS can read directly. So there is no GIS library
to install: this finds the feature tables, names their columns and hands back (attributes, WKB, SRS).

Not run against the real OS downloads from where this was written; tested on GeoPackages built to the
specification (OGC 12-128r18).
"""

from __future__ import annotations

import sqlite3
import struct
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ENVELOPE_BYTES = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}  # by the envelope indicator in the header flags
MAX_UNPACKED = 6 * 1024**3  # a national boundary or street file is a few GB unpacked; more is not one


class GpkgError(Exception):
    """The file is not a GeoPackage this can read."""


@dataclass(frozen=True)
class Layer:
    table: str
    geom: str
    srs: int
    columns: tuple[str, ...]


def unpack(zip_path: Path, dest: Path) -> Path:
    """The .gpkg inside the zip, extracted into `dest`."""
    with zipfile.ZipFile(zip_path) as z:
        members = [i for i in z.infolist() if i.filename.lower().endswith(".gpkg")]
        if not members:
            raise GpkgError("no .gpkg file in the download")
        if members[0].file_size > MAX_UNPACKED:
            raise GpkgError("the GeoPackage unpacks larger than expected")
        z.extract(members[0], dest)
        return dest / members[0].filename


def wkb_of(blob: bytes) -> tuple[bytes, int] | None:
    """(WKB, srs id) from a GeoPackage geometry blob, or None for an empty or unreadable one."""
    if len(blob) < 9 or blob[:2] != b"GP":
        return None
    flags = blob[3]
    if flags & 0x10:  # the empty-geometry flag
        return None
    envelope = ENVELOPE_BYTES.get((flags >> 1) & 0x07)
    if envelope is None:
        return None
    order = "<" if flags & 0x01 else ">"
    srs = struct.unpack(f"{order}i", blob[4:8])[0]
    return blob[8 + envelope:], srs


def layers(db: sqlite3.Connection) -> list[Layer]:
    """The feature tables, with their geometry column, SRS and attribute columns."""
    try:
        rows = db.execute("SELECT table_name, column_name, srs_id FROM gpkg_geometry_columns").fetchall()
    except sqlite3.DatabaseError as exc:
        raise GpkgError(f"not a GeoPackage: {exc}") from exc
    out = []
    for table, geom, srs in rows:
        cols = tuple(r[1] for r in db.execute(f'PRAGMA table_info("{table}")') if r[1] != geom)
        out.append(Layer(table, geom, int(srs or 27700), cols))
    return out


def pick(columns: tuple[str, ...], *wanted: str) -> str | None:
    """The first column whose name matches one of `wanted`, ignoring case."""
    lower = {c.lower(): c for c in columns}
    return next((lower[w] for w in wanted if w in lower), None)


def features(db: sqlite3.Connection, layer: Layer, columns: tuple[str, ...]) -> Iterator[tuple[dict[str, Any], bytes, int]]:
    """(attributes, WKB, SRS) of every feature with a geometry."""
    names = ", ".join(f'"{c}"' for c in (*columns, layer.geom))
    for row in db.execute(f'SELECT {names} FROM "{layer.table}"'):
        blob = row[-1]
        got = wkb_of(bytes(blob)) if blob else None
        if got is None:
            continue
        yield dict(zip(columns, row[:-1], strict=True)), got[0], got[1] if got[1] > 0 else layer.srs


def open_db(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.text_factory = lambda b: b.decode("utf-8", "replace")
    return db
