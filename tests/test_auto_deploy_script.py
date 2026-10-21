"""scripts/auto-deploy.sh with a pretend docker, curl and deploy script: every decision it can make."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
OLD = "ghcr.io/o/wa@sha256:" + "a" * 64
NEW = "ghcr.io/o/wa@sha256:" + "b" * 64

FAKE_DOCKER = r"""#!/usr/bin/env bash
echo "docker $*" >> "$T/calls"
case "$*" in
  "pull "*) [ -z "${FAKE_PULL_FAIL:-}" ] ;;
  *"RepoDigests"*) echo "$NEW_REF" ;;
  *"Config.Labels"*) echo "$NEW_VER" ;;
  "run --rm --entrypoint cat "*) echo "${NEW_REV:-1}" ;;
  "run --rm "*) [ -z "${FAKE_COSIGN_FAIL:-}" ] ;;
  *"psql"*) n=$(cat "$T/psql_n" 2>/dev/null || echo 0); echo $((n+1)) > "$T/psql_n"
            if [ "$n" = 0 ]; then echo "${SCHEMA_BEFORE:-0013}"; else echo "${SCHEMA_AFTER:-0013}"; fi ;;
  *"app.backup create"*) echo "wrote wa-20261021-040000.tar.gz (12 KB)" ;;
  *"alembic downgrade"*) [ -z "${FAKE_DOWNGRADE_FAIL:-}" ] ;;
esac
"""

FAKE_CURL = r"""#!/usr/bin/env bash
echo "curl $*" >> "$T/calls"
body=""; method=GET
while [ $# -gt 0 ]; do
  case "$1" in -X) method=$2; shift ;; -d) body=$2; shift ;; esac
  shift
done
if [ "$method" = POST ]; then echo "$body" >> "$T/reports"; exit 0; fi
[ -z "${FAKE_API_DOWN:-}" ] || exit 7
printf '{"enabled":%s,"running":{"version":"%s","commit":"x"},"last_run":null}' "$SWITCH" "$RUNNING"
"""

FAKE_DEPLOY = r"""#!/usr/bin/env bash
echo "deploy WA_IMAGE=${WA_IMAGE:-} env=$(grep '^WA_IMAGE=' .env 2>/dev/null || true) $*" >> "$T/calls"
n=$(cat "$T/deploy_n" 2>/dev/null || echo 0); echo $((n+1)) > "$T/deploy_n"
case ",${DEPLOY_RESULTS:-0,0}," in *",$n:fail,"*) exit 1 ;; esac
[ "$(echo "${DEPLOY_RESULTS:-0,0}" | cut -d, -f$((n+1)))" = 0 ]
"""


FAKE_SYNC = r"""#!/usr/bin/env bash
echo "sync $*" >> "$T/calls"
[ -z "${FAKE_SYNC_FAIL:-}" ]
"""


def _exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class Box:
    def __init__(self, tmp: Path) -> None:
        self.dir = tmp / "proj"
        (self.dir / "scripts").mkdir(parents=True)
        shutil.copy(ROOT / "scripts" / "auto-deploy.sh", self.dir / "scripts")
        _exe(self.dir / "scripts" / "deploy.sh", FAKE_DEPLOY)
        shutil.copy(ROOT / "scripts" / "lib-git.sh", self.dir / "scripts")
        _exe(self.dir / "scripts" / "sync.sh", FAKE_SYNC)
        (self.dir / "COMPOSE_REVISION").write_text("1\n")
        self.data = tmp / "data"
        (tmp / "secrets").mkdir()
        (tmp / "secrets" / "api_token.txt").write_text("tok")
        self.t = tmp / "t"
        self.t.mkdir()
        bin_ = tmp / "bin"
        bin_.mkdir()
        _exe(bin_ / "docker", FAKE_DOCKER)
        _exe(bin_ / "curl", FAKE_CURL)
        self.env = {
            **os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "T": str(self.t),
            "WA_DATA_DIR": str(self.data), "WA_SECRETS_DIR": str(tmp / "secrets"),
            "NEW_REF": NEW, "NEW_VER": "0.6.0", "RUNNING": "0.5.0", "SWITCH": "true",
            "AUTO_DEPLOY_VERIFY": "true",
        }  # fmt: skip
        self.env.pop("WA_IMAGE", None)

    def make_checkout(self) -> str:
        """Turn the project folder into a git checkout; returns its commit."""
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*git, "init", "-q", "-b", "main"], cwd=self.dir, check=True)
        subprocess.run([*git, "add", "scripts", "COMPOSE_REVISION"], cwd=self.dir, check=True)
        subprocess.run([*git, "commit", "-qm", "x"], cwd=self.dir, check=True)
        return subprocess.run([*git, "rev-parse", "HEAD"], cwd=self.dir, capture_output=True, text=True, check=True).stdout.strip()

    def dot_env(self, text: str) -> None:
        (self.dir / ".env").write_text(text)

    def run(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.dir / "scripts" / "auto-deploy.sh"), *args],
            cwd=self.dir, env={**self.env, **env}, capture_output=True, text=True, timeout=60, check=False,
        )  # fmt: skip

    @property
    def calls(self) -> list[str]:
        f = self.t / "calls"
        return f.read_text().splitlines() if f.exists() else []

    @property
    def reports(self) -> list[dict[str, str]]:
        f = self.t / "reports"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []

    @property
    def dot_env_text(self) -> str:
        return (self.dir / ".env").read_text() if (self.dir / ".env").exists() else ""


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


def test_switched_off_does_nothing(box: Box) -> None:
    r = box.run(SWITCH="false")
    assert r.returncode == 0 and "switched off" in r.stdout
    assert not any(c.startswith("docker") for c in box.calls) and box.reports == []


def test_api_down_is_an_error_not_a_silent_skip(box: Box) -> None:
    assert box.run(FAKE_API_DOWN="1").returncode == 1


def test_already_on_the_newest_release_reports_unchanged(box: Box) -> None:
    box.dot_env(f"WA_IMAGE={NEW}\n")
    r = box.run(WA_IMAGE=NEW)
    assert r.returncode == 0 and box.reports[-1]["result"] == "unchanged"
    assert not any(c.startswith("deploy") for c in box.calls)


def test_never_installs_an_older_release(box: Box) -> None:
    r = box.run(NEW_VER="0.4.0", RUNNING="0.5.0")
    assert r.returncode == 0 and box.reports[-1]["result"] == "unchanged"
    assert not any(c.startswith("deploy") for c in box.calls)


def test_a_newer_release_is_backed_up_installed_and_reported(box: Box) -> None:
    box.dot_env(f"WA_API_PORT=8010\nWA_IMAGE={OLD}\n")
    r = box.run(WA_IMAGE=OLD)
    assert r.returncode == 0, r.stdout + r.stderr
    deploys = [c for c in box.calls if c.startswith("deploy")]
    assert len(deploys) == 1 and f"WA_IMAGE={NEW}" in deploys[0] and "--skip-backup" in deploys[0]
    assert any("app.backup create" in c for c in box.calls)
    assert f"WA_IMAGE={NEW}" in box.dot_env_text and "WA_API_PORT=8010" in box.dot_env_text
    rep = box.reports[-1]
    assert rep["result"] == "updated" and rep["from_version"] == "0.5.0" and rep["to_version"] == "0.6.0"


def test_the_signature_check_matches_the_repository_name_in_any_case(box: Box) -> None:
    box.run()
    verify = next(c for c in box.calls if c.startswith("docker run --rm -v"))
    assert "(?i)https://github.com/rpearson-dot/world-assistant/.github/workflows/release.yml@.*" in verify


def test_a_bad_signature_is_refused_before_anything_changes(box: Box) -> None:
    r = box.run(FAKE_COSIGN_FAIL="1")
    assert r.returncode == 0 and box.reports[-1]["result"] == "refused"
    assert "signature" in box.reports[-1]["message"]
    assert not any(c.startswith("deploy") or "app.backup" in c for c in box.calls)


def test_a_release_needing_newer_compose_files_is_refused(box: Box) -> None:
    r = box.run(NEW_REV="2")
    assert r.returncode == 0 and box.reports[-1]["result"] == "refused"
    assert "newer compose files" in box.reports[-1]["message"]
    assert not any(c.startswith("deploy") for c in box.calls)


def test_a_failed_backup_changes_nothing(box: Box) -> None:
    broken = box.t.parent / "bin" / "docker"
    broken.write_text(FAKE_DOCKER.replace('*"app.backup create"*) echo', '*"app.backup create"*) exit 1; echo'))
    r = box.run()
    assert r.returncode == 1 and box.reports[-1]["result"] == "failed"
    assert not any(c.startswith("deploy") for c in box.calls)


def test_a_failed_check_rolls_back_to_the_previous_image(box: Box) -> None:
    box.dot_env(f"WA_IMAGE={OLD}\n")
    r = box.run(WA_IMAGE=OLD, DEPLOY_RESULTS="1,0")
    deploys = [c for c in box.calls if c.startswith("deploy")]
    assert r.returncode == 1 and len(deploys) == 2
    assert f"WA_IMAGE={NEW}" in deploys[0] and f"WA_IMAGE={OLD}" in deploys[1]
    assert box.reports[-1]["result"] == "rolled_back"
    assert box.dot_env_text.strip() == f"WA_IMAGE={OLD}"
    assert not any("alembic downgrade" in c for c in box.calls)  # schema unchanged: no downgrade


def test_rollback_from_a_local_build_removes_the_image_line(box: Box) -> None:
    box.dot_env("WA_API_PORT=8010\n")
    box.run(DEPLOY_RESULTS="1,0")
    deploys = [c for c in box.calls if c.startswith("deploy")]
    assert "WA_IMAGE=" in deploys[0] and f"WA_IMAGE={NEW}" in deploys[0]
    assert deploys[1].startswith("deploy WA_IMAGE= env= ")
    assert "WA_IMAGE" not in box.dot_env_text


def test_a_changed_schema_is_downgraded_before_rolling_back(box: Box) -> None:
    box.dot_env(f"WA_IMAGE={OLD}\n")
    r = box.run(WA_IMAGE=OLD, DEPLOY_RESULTS="1,0", SCHEMA_BEFORE="0013", SCHEMA_AFTER="0014")
    down = [i for i, c in enumerate(box.calls) if "alembic downgrade 0013" in c]
    deploys = [i for i, c in enumerate(box.calls) if c.startswith("deploy")]
    assert r.returncode == 1 and down and deploys[0] < down[0] < deploys[1]
    assert box.reports[-1]["result"] == "rolled_back"


def test_if_the_downgrade_fails_it_stops_and_names_the_backup(box: Box) -> None:
    box.dot_env(f"WA_IMAGE={OLD}\n")
    r = box.run(WA_IMAGE=OLD, DEPLOY_RESULTS="1,0", SCHEMA_BEFORE="0013", SCHEMA_AFTER="0014", FAKE_DOWNGRADE_FAIL="1")
    deploys = [c for c in box.calls if c.startswith("deploy")]
    assert r.returncode == 1 and len(deploys) == 1
    assert box.reports[-1]["result"] == "failed" and "wa-20261021-040000.tar.gz" in box.reports[-1]["message"]


def test_if_the_rollback_also_fails_it_says_so(box: Box) -> None:
    box.dot_env(f"WA_IMAGE={OLD}\n")
    box.run(WA_IMAGE=OLD, DEPLOY_RESULTS="1,1")
    assert box.reports[-1]["result"] == "failed" and "wa-20261021-040000.tar.gz" in box.reports[-1]["message"]


def test_dry_run_changes_and_reports_nothing(box: Box) -> None:
    box.dot_env(f"WA_IMAGE={OLD}\n")
    r = box.run("--dry-run", WA_IMAGE=OLD)
    assert r.returncode == 0 and "Dry run" in r.stdout
    assert box.reports == [] and box.dot_env_text.strip() == f"WA_IMAGE={OLD}"
    assert not any(c.startswith("deploy") or "app.backup" in c for c in box.calls)


def test_a_failed_pull_is_reported(box: Box) -> None:
    r = box.run(FAKE_PULL_FAIL="1")
    assert r.returncode == 1 and box.reports[-1]["result"] == "failed" and "logged in" in box.reports[-1]["message"]


def test_a_checkout_moves_to_the_release_tag_after_the_backup_and_before_the_deploy(box: Box) -> None:
    head = box.make_checkout()
    r = box.run(NEW_REV="9")  # newer compose files are fine: the checkout will bring them
    assert r.returncode == 0, r.stdout + r.stderr
    kinds = [c.split()[0] + (" " + c.split()[1] if c.startswith("sync") else "") for c in box.calls if c.split()[0] in ("sync", "deploy")]
    assert kinds == ["sync v0.6.0", "deploy"]
    backup = next(i for i, c in enumerate(box.calls) if "app.backup create" in c)
    sync = next(i for i, c in enumerate(box.calls) if c.startswith("sync"))
    assert backup < sync
    assert head and box.reports[-1]["result"] == "updated"


def test_a_failed_check_puts_the_project_files_back_before_the_old_image(box: Box) -> None:
    head = box.make_checkout()
    box.dot_env(f"WA_IMAGE={OLD}\n")
    r = box.run(WA_IMAGE=OLD, DEPLOY_RESULTS="1,0")
    syncs = [c for c in box.calls if c.startswith("sync")]
    assert r.returncode == 1 and syncs == ["sync v0.6.0", f"sync {head}"]
    order = [c.split()[0] for c in box.calls if c.split()[0] in ("sync", "deploy")]
    assert order == ["sync", "deploy", "sync", "deploy"]
    assert box.reports[-1]["result"] == "rolled_back"


def test_if_the_files_cannot_be_synced_nothing_is_updated(box: Box) -> None:
    box.make_checkout()
    r = box.run(FAKE_SYNC_FAIL="1")
    assert r.returncode == 1 and box.reports[-1]["result"] == "failed"
    assert not any(c.startswith("deploy") for c in box.calls)


def test_a_dry_run_on_a_checkout_does_not_sync(box: Box) -> None:
    box.make_checkout()
    r = box.run("--dry-run")
    assert "move the project files to v0.6.0" in r.stdout
    assert not any(c.startswith(("sync", "deploy")) for c in box.calls)
