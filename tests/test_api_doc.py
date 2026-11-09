"""docs/api.md is written from the app's routes; it must not fall behind them (python scripts/api-doc.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_api_reference_is_current() -> None:
    spec = importlib.util.spec_from_file_location("api_doc", ROOT / "scripts" / "api-doc.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert (ROOT / "docs" / "api.md").read_text() == mod.build(), "run: python scripts/api-doc.py"
