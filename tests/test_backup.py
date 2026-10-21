import os
import tarfile
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import backup
from app.main import app

URL = os.environ.get("WA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs WA_TEST_DATABASE_URL")
SCHEMA = "bk_test"


def _conninfo() -> str:
    assert URL
    return URL.replace("postgresql+psycopg://", "postgresql://")


@pytest.fixture
def db():  # type: ignore[no-untyped-def]
    with psycopg.connect(_conninfo(), autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.execute(f"CREATE SCHEMA {SCHEMA}")
        c.execute(f"""
            CREATE TABLE {SCHEMA}.place (id text PRIMARY KEY, name text, extra jsonb);
            CREATE TABLE {SCHEMA}.trip (id serial PRIMARY KEY, from_place text REFERENCES {SCHEMA}.place(id),
                                        made timestamptz DEFAULT now(), note text);
            CREATE TABLE {SCHEMA}.fuel_price (x int);
            CREATE TABLE {SCHEMA}.alembic_version (version_num text);
            INSERT INTO {SCHEMA}.alembic_version VALUES ('0012');
            INSERT INTO {SCHEMA}.fuel_price VALUES (1);
            INSERT INTO {SCHEMA}.place VALUES ('home', 'Home, Epsom', '{{"a": [1, 2]}}'), ('w', E'tab\\there', NULL);
            INSERT INTO {SCHEMA}.trip (from_place, note) VALUES ('home', 'line1' || E'\\n' || 'line2'), ('w', NULL);
        """)
        yield c
        c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


def test_round_trip_restores_data_and_sequences(db, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    info = backup.create(tmp_path, schema=SCHEMA, conninfo=_conninfo())
    assert set(info["tables"]) == {"place", "trip"}  # fuel_price and alembic_version are excluded
    assert info["tables"]["trip"] == 2

    db.execute(f"DELETE FROM {SCHEMA}.trip; DELETE FROM {SCHEMA}.place")
    db.execute(f"INSERT INTO {SCHEMA}.place VALUES ('junk', 'junk', NULL)")
    done = backup.restore(tmp_path / info["name"], schema=SCHEMA, conninfo=_conninfo())
    assert done == {"place": 2, "trip": 2}

    rows = db.execute(f"SELECT id, name, extra FROM {SCHEMA}.place ORDER BY id").fetchall()
    assert rows == [("home", "Home, Epsom", {"a": [1, 2]}), ("w", "tab\there", None)]
    assert db.execute(f"SELECT note FROM {SCHEMA}.trip ORDER BY id").fetchall() == [("line1\nline2",), (None,)]
    # the serial continues after the restored rows, not from 1
    db.execute(f"INSERT INTO {SCHEMA}.trip (from_place) VALUES ('home')")
    assert db.execute(f"SELECT max(id) FROM {SCHEMA}.trip").fetchone() == (3,)
    # excluded tables are untouched
    assert db.execute(f"SELECT count(*) FROM {SCHEMA}.fuel_price").fetchone() == (1,)


def test_restore_refuses_other_schema_version(db, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    info = backup.create(tmp_path, schema=SCHEMA, conninfo=_conninfo())
    db.execute(f"UPDATE {SCHEMA}.alembic_version SET version_num = '0013'")
    with pytest.raises(backup.BackupError, match="schema version differs"):
        backup.restore(tmp_path / info["name"], schema=SCHEMA, conninfo=_conninfo())
    backup.restore(tmp_path / info["name"], schema=SCHEMA, conninfo=_conninfo(), force=True)


def test_failed_restore_changes_nothing(db, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    info = backup.create(tmp_path, schema=SCHEMA, conninfo=_conninfo())
    broken = tmp_path / "wa-20200101-000000.tar.gz"
    with tarfile.open(tmp_path / info["name"]) as src, tarfile.open(broken, "w:gz") as dst:
        for m in src.getmembers():
            if m.name == "trip.copy":
                continue  # a missing table file aborts part way through
            dst.addfile(m, src.extractfile(m))
    with pytest.raises(backup.BackupError, match="missing trip.copy"):
        backup.restore(broken, schema=SCHEMA, conninfo=_conninfo())
    assert db.execute(f"SELECT count(*) FROM {SCHEMA}.place").fetchone() == (2,)
    assert db.execute(f"SELECT count(*) FROM {SCHEMA}.trip").fetchone() == (2,)


def test_retention_keeps_newest(db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    for i in range(5):
        (tmp_path / f"wa-2026010{i}-000000-v0.tar.gz").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("keep me")
    monkeypatch.setenv("WA_BACKUP_KEEP", "3")
    backup.create(tmp_path, schema=SCHEMA, conninfo=_conninfo())
    names = [b["name"] for b in backup.list_backups(tmp_path)]
    assert len(names) == 3 and names[0].startswith("wa-2026") and (tmp_path / "notes.txt").exists()


def test_unwritable_folder_gives_a_clear_error(db, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    blocker = tmp_path / "file"
    blocker.write_text("")
    with pytest.raises(backup.BackupError, match="not writable"):
        backup.create(blocker / "sub", schema=SCHEMA, conninfo=_conninfo())


# ---- no database needed ----------------------------------------------------------------------


@pytest.mark.skipif(False, reason="always")
def test_names_are_validated(tmp_path: Path) -> None:
    for bad in ("../etc/passwd", "wa-1.tar.gz", "a/b.tar.gz", "wa-20260101-000000/../x.tar.gz"):
        with pytest.raises(FileNotFoundError):
            backup.path_for(bad, tmp_path)
    good = tmp_path / "wa-20260101-000000-v0.2.0.tar.gz"
    good.write_bytes(b"x")
    assert backup.path_for(good.name, tmp_path) == good


def test_endpoints_need_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "s3cret")
    c = TestClient(app)
    assert c.get("/v1/backups").status_code == 401
    assert c.post("/v1/backups").status_code == 401
    assert c.get("/v1/backups/wa-20260101-000000.tar.gz").status_code == 401


def test_list_and_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "s3cret")
    monkeypatch.setenv("WA_BACKUP_DIR", str(tmp_path))
    f = tmp_path / "wa-20260101-000000-v0.2.0.tar.gz"
    f.write_bytes(b"data")
    h = {"Authorization": "Bearer s3cret"}
    c = TestClient(app)
    assert [b["name"] for b in c.get("/v1/backups", headers=h).json()["backups"]] == [f.name]
    assert c.get(f"/v1/backups/{f.name}", headers=h).content == b"data"
    assert c.get("/v1/backups/..%2Fsecret", headers=h).status_code == 404


def test_bad_data_mid_restore_rolls_back(db, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    info = backup.create(tmp_path, schema=SCHEMA, conninfo=_conninfo())
    broken = tmp_path / "wa-20200101-000001.tar.gz"
    with tarfile.open(tmp_path / info["name"]) as src, tarfile.open(broken, "w:gz") as dst:
        for m in src.getmembers():
            if m.name == "trip.copy":
                import io

                bad = b"not-a-number\thome\t2026-01-01\tx\n"
                m.size = len(bad)
                dst.addfile(m, io.BytesIO(bad))
            else:
                dst.addfile(m, src.extractfile(m))
    with pytest.raises(backup.BackupError, match="nothing was changed"):
        backup.restore(broken, schema=SCHEMA, conninfo=_conninfo())
    assert db.execute(f"SELECT count(*) FROM {SCHEMA}.trip").fetchone() == (2,)
