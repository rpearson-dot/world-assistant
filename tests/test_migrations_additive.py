"""Migrations only add (docs/migrations.md): during a staged update the running version works on the new database,
so upgrade() may not remove, rename or tighten anything unless the line is marked `# contract:` (a later release
removing what an earlier one stopped using)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parent.parent / "migrations" / "versions"
FROM = 48  # migrations before the staged updates came in are not held to it
REMOVING = {"drop_column", "drop_table", "rename_table", "drop_table_comment"}
SQL_WORDS = ("DROP TABLE", "DROP COLUMN", "RENAME", " TYPE ", "SET NOT NULL", "DROP VIEW", "DROP SCHEMA")


def problems(source: str) -> list[str]:
    """What in a migration's upgrade() the version before could trip over, line by line."""
    tree = ast.parse(source)
    lines = source.splitlines()
    up = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"), None)
    out: list[str] = []
    if up is None:
        return out
    for node in ast.walk(up):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        name, line = node.func.attr, node.lineno
        span = "\n".join(lines[node.lineno - 1 : (node.end_lineno or node.lineno)])
        if "# contract:" in span:
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        why = None
        if name in REMOVING:
            why = f"{name} removes something the running version may use"
        elif name == "alter_column":
            if "new_column_name" in kw:
                why = "renaming a column"
            elif "type_" in kw:
                why = "changing a column's type"
            elif isinstance(kw.get("nullable"), ast.Constant) and kw["nullable"].value is False:
                why = "making a column NOT NULL"
        elif name == "add_column":
            col = next((a for a in node.args if isinstance(a, ast.Call)), None)
            ck = {k.arg: k.value for k in col.keywords if k.arg} if col else {}
            not_null = isinstance(ck.get("nullable"), ast.Constant) and ck["nullable"].value is False
            if not_null and "server_default" not in ck:
                why = "a NOT NULL column without a server default (the running version's inserts would fail)"
        elif name == "execute":
            sql = " ".join(a.value for a in ast.walk(node) if isinstance(a, ast.Constant) and isinstance(a.value, str)).upper()
            hit = next((w.strip() for w in SQL_WORDS if w in f" {sql} "), None)
            if hit:
                why = f"SQL with {hit}"
        if why:
            out.append(f"line {line}: {why}")
    return out


FILES = sorted(p for p in VERSIONS.glob("[0-9][0-9][0-9][0-9]_*.py") if int(p.name[:4]) >= FROM)


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_a_migration_only_adds(path: Path) -> None:
    assert problems(path.read_text()) == [], f"{path.name}: see docs/migrations.md"


def test_the_check_catches_what_it_should() -> None:
    bad = '''
def upgrade():
    op.drop_column("bike", "x")
    op.alter_column("bike", "y", new_column_name="z")
    op.alter_column("bike", "w", nullable=False)
    op.add_column("bike", sa.Column("v", sa.Text(), nullable=False))
    op.execute("ALTER TABLE bike RENAME COLUMN a TO b")
    op.drop_table("old")  # contract: unused since 0.52.0

def downgrade():
    op.drop_column("bike", "v")
'''
    got = problems(bad)
    assert len(got) == 5 and "line 3" in got[0] and not any("line 8" in g for g in got)
    good = '''
def upgrade():
    op.add_column("bike", sa.Column("v", sa.Text(), nullable=False, server_default="x"))
    op.add_column("bike", sa.Column("u", sa.Text()))
    op.alter_column("bike", "w", nullable=True)
    op.create_index("ix", "bike", ["v"])
    op.execute("UPDATE bike SET v = 'y'")
'''
    assert problems(good) == []
