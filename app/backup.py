"""Back up and restore the data that cannot be refetched.

A backup is one `.tar.gz` holding a `manifest.json` and one `<table>.copy` file per table (PostgreSQL
COPY text format). It needs no pg_dump, so it runs inside the app container, from the status page or
from a deploy script. It skips tables the app refills by itself (fuel prices, National Highways
events and signs, raw captures, job history).

  docker compose exec api python -m app.backup create
  docker compose exec api python -m app.backup list
  docker compose exec api python -m app.backup restore wa-20261021-071500.tar.gz

Restoring replaces the contents of the backed-up tables, so stop the worker first (the deploy script
and runbook say how) and only restore into a database migrated to the same schema version.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import sys
import tarfile
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from app.config import get_settings
from app.version import VERSION

log = logging.getLogger("wa.backup")

# Refilled by the pollers, or only of passing interest. Everything else is backed up, so a new
# table is included automatically.
EXCLUDE = frozenset(
    {
        "alembic_version", "raw_capture", "job_run", "adapter_state",
        "fuel_station", "fuel_price", "fuel_price_change",
        "nh_event", "nh_event_corridor", "nh_sign", "nh_sign_message",
    }
)  # fmt: skip
NAME_RE = re.compile(r"^wa-\d{8}-\d{6}(?:-[A-Za-z0-9._]+)?\.tar\.gz$")
FORMAT = 1


class BackupError(RuntimeError):
    pass


def backup_dir() -> Path:
    return Path(os.environ.get("WA_BACKUP_DIR", "/backups"))


def keep_count() -> int:
    try:
        return max(1, int(os.environ.get("WA_BACKUP_KEEP", "14")))
    except ValueError:
        return 14


def _conninfo() -> str:
    url = get_settings().database_url.set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


def _tables(cur: psycopg.Cursor[Any], schema: str, exclude: frozenset[str]) -> list[str]:
    cur.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename", (schema,)
    )
    return [r[0] for r in cur.fetchall() if r[0] not in exclude]


def _columns(cur: psycopg.Cursor[Any], schema: str, table: str) -> list[str]:
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = %s "
        "AND table_name = %s AND is_generated = 'NEVER' ORDER BY ordinal_position",
        (schema, table),
    )
    return [r[0] for r in cur.fetchall()]


def _order(cur: psycopg.Cursor[Any], schema: str, tables: list[str]) -> list[str]:
    """Parents before children, so foreign keys hold while loading."""
    cur.execute(
        """
        SELECT c.conrelid::regclass::text, c.confrelid::regclass::text
        FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
        WHERE c.contype = 'f' AND n.nspname = %s
        """,
        (schema,),
    )
    strip = lambda s: s.split(".")[-1].strip('"')
    wanted = set(tables)
    parents: dict[str, set[str]] = defaultdict(set)
    for child, parent in cur.fetchall():
        child, parent = strip(child), strip(parent)
        if child in wanted and parent in wanted and child != parent:
            parents[child].add(parent)
    done: list[str] = []
    pending = sorted(wanted)
    while pending:
        ready = [t for t in pending if parents[t] <= set(done)]
        if not ready:
            raise BackupError("circular foreign keys between backed-up tables")
        done.extend(ready)
        pending = [t for t in pending if t not in ready]
    return done


def _head(cur: psycopg.Cursor[Any], schema: str) -> str | None:
    cur.execute(
        "SELECT 1 FROM pg_tables WHERE schemaname = %s AND tablename = 'alembic_version'", (schema,)
    )
    if not cur.fetchone():
        return None
    cur.execute(sql.SQL("SELECT version_num FROM {}.alembic_version").format(sql.Identifier(schema)))
    row = cur.fetchone()
    return row[0] if row else None


def create(
    directory: Path | None = None, *, schema: str = "public", exclude: frozenset[str] = EXCLUDE,
    conninfo: str | None = None,
) -> dict[str, Any]:  # fmt: skip
    """Write a backup and return its description. Prunes old backups beyond WA_BACKUP_KEEP."""
    out_dir = directory or backup_dir()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        probe = out_dir / ".write-test"
        probe.write_text("")
        probe.unlink()
    except OSError as exc:
        raise BackupError(
            f"backup folder {out_dir} is not writable ({exc.strerror}); "
            "run scripts/deploy.sh once, or see docs/backup.md"
        ) from exc
    now = datetime.now(UTC)
    name = f"wa-{now:%Y%m%d-%H%M%S}-v{VERSION}.tar.gz"
    counts: dict[str, int] = {}
    with psycopg.connect(conninfo or _conninfo()) as conn:
        conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        with conn.cursor() as cur, tempfile.TemporaryDirectory() as tmp:
            tables = _order(cur, schema, _tables(cur, schema, exclude))
            manifest: dict[str, Any] = {
                "format": FORMAT, "created": now.isoformat(), "app_version": VERSION,
                "schema": schema, "alembic_head": _head(cur, schema), "tables": {},
            }  # fmt: skip
            for table in tables:
                cols = _columns(cur, schema, table)
                stmt = sql.SQL("COPY (SELECT {c} FROM {s}.{t}) TO STDOUT").format(
                    c=sql.SQL(", ").join(map(sql.Identifier, cols)),
                    s=sql.Identifier(schema), t=sql.Identifier(table),
                )  # fmt: skip
                rows = 0
                with open(Path(tmp) / f"{table}.copy", "wb") as fh, cur.copy(stmt) as cp:
                    fh.writelines(bytes(chunk) for chunk in cp)
                cur.execute(
                    sql.SQL("SELECT count(*) FROM {}.{}").format(
                        sql.Identifier(schema), sql.Identifier(table)
                    )
                )
                rows = cur.fetchone()[0]  # type: ignore[index]
                counts[table] = rows
                manifest["tables"][table] = {"columns": cols, "rows": rows}
            final = out_dir / name
            partial = out_dir / (name + ".part")
            with tarfile.open(partial, "w:gz") as tar:
                data = json.dumps(manifest, indent=1).encode()
                info = tarfile.TarInfo("manifest.json")
                info.size = len(data)
                info.mtime = int(now.timestamp())
                tar.addfile(info, io.BytesIO(data))
                for table in tables:
                    tar.add(Path(tmp) / f"{table}.copy", arcname=f"{table}.copy")
            partial.chmod(0o640)
            partial.rename(final)
    _prune(out_dir)
    log.info("backup written: %s (%d tables)", name, len(counts))
    return {**describe(final), "tables": counts}


def describe(path: Path) -> dict[str, Any]:
    st = path.stat()
    return {
        "name": path.name, "size_bytes": st.st_size,
        "created": datetime.fromtimestamp(st.st_mtime, UTC).isoformat(),
    }  # fmt: skip


def list_backups(directory: Path | None = None) -> list[dict[str, Any]]:
    d = directory or backup_dir()
    try:
        files = [p for p in d.iterdir() if NAME_RE.match(p.name)]
    except OSError:
        return []
    return [describe(p) for p in sorted(files, key=lambda p: p.name, reverse=True)]


def _prune(directory: Path) -> None:
    files = sorted((p for p in directory.iterdir() if NAME_RE.match(p.name)), key=lambda p: p.name)
    for old in files[: max(0, len(files) - keep_count())]:
        old.unlink(missing_ok=True)


def path_for(name: str, directory: Path | None = None) -> Path:
    """Resolve a backup name safely (no path tricks); raises FileNotFoundError."""
    if not NAME_RE.match(name):
        raise FileNotFoundError(name)
    p = (directory or backup_dir()) / name
    if not p.is_file():
        raise FileNotFoundError(name)
    return p


def restore(
    file: Path, *, schema: str = "public", conninfo: str | None = None, force: bool = False
) -> dict[str, int]:
    """Replace the backed-up tables with the contents of `file`. One transaction: all or nothing."""
    try:
        tar = tarfile.open(file, "r:gz")  # noqa: SIM115 - closed by the with below
    except (OSError, tarfile.TarError) as exc:
        raise BackupError(f"cannot read {file}: {exc}") from exc
    with tar:
        member = tar.extractfile("manifest.json")
        if member is None:
            raise BackupError("not a World Assistant backup (no manifest)")
        manifest = json.loads(member.read())
        if manifest.get("format") != FORMAT:
            raise BackupError(f"unsupported backup format {manifest.get('format')!r}")
        have = set(tar.getnames())
        for t in manifest["tables"]:
            if f"{t}.copy" not in have:
                raise BackupError(f"backup is missing {t}.copy")
        try:
            return _load(tar, manifest, schema, conninfo, force)
        except psycopg.Error as exc:
            raise BackupError(f"restore failed and nothing was changed: {exc}") from exc


def _load(
    tar: tarfile.TarFile, manifest: dict[str, Any], schema: str, conninfo: str | None, force: bool
) -> dict[str, int]:
    restored: dict[str, int] = {}
    with psycopg.connect(conninfo or _conninfo()) as conn, conn.cursor() as cur:
        head = _head(cur, schema)
        if head != manifest.get("alembic_head") and not force:
            raise BackupError(
                f"schema version differs (database {head}, backup {manifest.get('alembic_head')}). "
                "Run the same release the backup came from, or pass --force if you know the "
                "tables are compatible."
            )
        present = set(_tables(cur, schema, frozenset()))
        names = [t for t in manifest["tables"] if t in present]
        for t in manifest["tables"]:
            if t not in present:
                log.warning("table %s is in the backup but not the database; skipped", t)
        order = _order(cur, schema, names)
        ident = lambda t: sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(t))
        cur.execute(
            sql.SQL("TRUNCATE {} CASCADE").format(sql.SQL(", ").join(ident(t) for t in order))
        )
        for t in order:
            cols = manifest["tables"][t]["columns"]
            stmt = sql.SQL("COPY {} ({}) FROM STDIN").format(
                ident(t), sql.SQL(", ").join(map(sql.Identifier, cols))
            )
            data = tar.extractfile(f"{t}.copy")
            if data is None:
                raise BackupError(f"backup is missing {t}.copy")
            with cur.copy(stmt) as cp:
                while chunk := data.read(1 << 20):
                    cp.write(chunk)
            cur.execute(sql.SQL("SELECT count(*) FROM {}").format(ident(t)))
            restored[t] = cur.fetchone()[0]  # type: ignore[index]
            for col in cols:
                cur.execute("SELECT pg_get_serial_sequence(%s, %s)", (f"{schema}.{t}", col))
                seq = cur.fetchone()[0]  # type: ignore[index]
                if seq:
                    cur.execute(
                        sql.SQL("SELECT setval(%s, coalesce(max({c}), 1), max({c}) IS NOT NULL) FROM {t}")
                        .format(c=sql.Identifier(col), t=ident(t)),
                        (seq,),
                    )
    return restored


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    try:
        if cmd == "create":
            info = create()
            print(f"wrote {info['name']} ({info['size_bytes'] // 1024} KB)")
            for table, n in info["tables"].items():
                print(f"  {table:<20} {n} rows")
            return 0
        if cmd == "list":
            for b in list_backups():
                print(f"{b['name']}  {b['size_bytes'] // 1024} KB  {b['created']}")
            return 0
        if cmd == "restore" and len(argv) >= 2:
            target = Path(argv[1])
            if not target.exists():
                target = backup_dir() / argv[1]
            done = restore(target, force="--force" in argv)
            for table, n in done.items():
                print(f"  {table:<20} {n} rows")
            print("restored. Restart the api and worker: docker compose restart api worker")
            return 0
    except BackupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("usage: python -m app.backup create | list | restore <file> [--force]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
