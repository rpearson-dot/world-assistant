"""The migration history must be one straight line, or `alembic upgrade head` fails on deploy."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parent.parent


def _script() -> ScriptDirectory:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return ScriptDirectory.from_config(cfg)


def test_there_is_exactly_one_head() -> None:
    assert len(_script().get_heads()) == 1


def test_every_revision_has_a_single_parent_and_a_downgrade() -> None:
    for rev in _script().walk_revisions():
        assert not isinstance(rev.down_revision, tuple), f"{rev.revision} merges branches"
        assert hasattr(rev.module, "downgrade"), f"{rev.revision} has no downgrade"
