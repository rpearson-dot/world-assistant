"""The database stays where it is until scripts/pg-upgrade.sh moves it, and new installs start on PostgreSQL 18."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _settings(script: str) -> dict[str, str]:
    text = (ROOT / "scripts" / script).read_text()
    return dict(re.findall(r"(WA_PG_TAG|WA_PG_DIR|WA_PG_MOUNT)[= ]\"?([\w./-]+)", text))


def test_without_settings_an_existing_install_keeps_postgres_16_and_its_folder() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "image: postgis/postgis:${WA_PG_TAG:-16-3.4}" in compose
    assert "/${WA_PG_DIR:-pgdata}:${WA_PG_MOUNT:-/var/lib/postgresql/data}" in compose


def test_the_upgrade_new_installs_and_ci_agree_on_18() -> None:
    up = (ROOT / "scripts" / "pg-upgrade.sh").read_text()
    want = {"WA_PG_TAG": "18-3.6", "WA_PG_DIR": "pg18", "WA_PG_MOUNT": "/var/lib/postgresql"}
    assert re.search(r"^NEW_TAG=18-3.6$", up, re.MULTILINE) and re.search(r"^NEW_DIR=pg18$", up, re.MULTILINE)
    assert re.search(r"^NEW_MOUNT=/var/lib/postgresql$", up, re.MULTILINE)
    assert _settings("setup.sh") == want and _settings("ci-stack.sh") == want
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "image: postgis/postgis:18-3.6" in ci and "bash scripts/ci-pg-upgrade.sh" in ci


def test_the_scripts_parse() -> None:
    for name in ("pg-upgrade.sh", "ci-pg-upgrade.sh", "setup.sh", "ci-stack.sh"):
        subprocess.run(["bash", "-n", str(ROOT / "scripts" / name)], check=True)


def test_the_upgrade_refuses_to_start_without_a_settings_file(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "pg-upgrade.sh").write_text((ROOT / "scripts" / "pg-upgrade.sh").read_text())
    r = subprocess.run(["bash", "scripts/pg-upgrade.sh", "--yes"], cwd=tmp_path, capture_output=True, text=True,
                       timeout=20, check=False)  # fmt: skip
    assert r.returncode == 1 and "no .env here" in r.stdout
