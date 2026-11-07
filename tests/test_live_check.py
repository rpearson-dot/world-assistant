"""scripts/live-check.sh: its Python compiles, and every module it ties a check to exists (so a switched-off module's
checks are skipped, not failed)."""

import re
from pathlib import Path

SCRIPT = Path("scripts/live-check.sh").read_text()


def test_the_check_compiles_and_names_real_modules() -> None:
    from app.modules import registry

    body = SCRIPT.split("<<'PY'", 1)[1].split("\n", 1)[1].split("\nPY\n", 1)[0]  # after the heredoc line
    compile(body, "live-check", "exec")
    named = set(re.findall(r'module="([a-z0-9_]+)"', body))
    assert named and not named - {m.id for m in registry.modules()}
