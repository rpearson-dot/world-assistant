"""scripts/lib-release.sh: a pinned release image is kept on the same release as the project files."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD = "ghcr.io/o/wa@sha256:" + "a" * 64
NEW = "ghcr.io/o/wa@sha256:" + "b" * 64

# The pinned image is 0.22.1; the 0.23.0 tag resolves to NEW. FAKE_NO_TAG: no image for the files' version.
FAKE_DOCKER = r"""#!/usr/bin/env bash
echo "docker $*" >> "$T/calls"
case "$*" in
  "pull -q ghcr.io/o/wa:"*) [ -z "${FAKE_NO_TAG:-}" ] ;;
  "pull -q "*) true ;;
  *"inspect ghcr.io/o/wa@sha256:a"*"Config.Labels"*) echo 0.22.1 ;;
  *"Config.Labels"*) echo "${FILES_VER:-0.23.0}" ;;
  *"RepoDigests"*) echo "$NEW_REF" ;;
  "run --rm "*) [ -z "${FAKE_COSIGN_FAIL:-}" ] ;;
esac
"""


def _run(tmp: Path, files_ver: str, image: str | None, checkout: bool = True, **env: str) -> tuple[int, str, str, str]:
    proj = tmp / "proj"
    (proj / "scripts").mkdir(parents=True, exist_ok=True)
    (proj / "scripts" / "lib-release.sh").write_text((ROOT / "scripts" / "lib-release.sh").read_text())
    (proj / "pyproject.toml").write_text(f'[project]\nname = "wa"\nversion = "{files_ver}"\n')
    if checkout:
        (proj / ".git").mkdir(exist_ok=True)
    (proj / ".env").write_text("WA_API_PORT=8010\n" + (f"WA_IMAGE={image}\n" if image else ""))
    bin_ = tmp / "bin"
    bin_.mkdir(exist_ok=True)
    (bin_ / "docker").write_text(FAKE_DOCKER)
    (bin_ / "docker").chmod(0o755 | stat.S_IEXEC)
    t = tmp / "t"
    t.mkdir(exist_ok=True)
    full = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "T": str(t), "NEW_REF": NEW, **env}
    if image:
        full["WA_IMAGE"] = image
    else:
        full.pop("WA_IMAGE", None)
    r = subprocess.run(["bash", "-c", ". scripts/lib-release.sh; wa_align_image"], cwd=proj, env=full,
                       capture_output=True, text=True, timeout=30)  # fmt: skip
    calls = (t / "calls").read_text() if (t / "calls").exists() else ""
    return r.returncode, r.stdout, (proj / ".env").read_text(), calls


def test_a_pin_behind_the_files_moves_to_the_files_signed_release(tmp_path: Path) -> None:
    code, out, env, calls = _run(tmp_path, "0.23.0", OLD)
    assert code == 0, out
    assert "pinned image is 0.22.1 but the project files are 0.23.0" in out
    assert env == f"WA_API_PORT=8010\nWA_IMAGE={NEW}\n"  # one WA_IMAGE line, the new one
    assert "pull -q ghcr.io/o/wa:0.23.0" in calls and "verify " + NEW in calls  # signature checked


def test_a_pin_matching_the_files_is_left_alone(tmp_path: Path) -> None:
    code, out, env, calls = _run(tmp_path, "0.22.1", OLD)
    assert code == 0 and out == "" and f"WA_IMAGE={OLD}" in env and "verify" not in calls


def test_files_newer_than_any_release_stop_the_deploy_and_say_what_to_do(tmp_path: Path) -> None:
    code, out, env, _ = _run(tmp_path, "0.24.0", OLD, FAKE_NO_TAG="1", FILES_VER="0.24.0")
    assert code == 1 and "no 0.24.0 release image" in out and "remove the WA_IMAGE line" in out
    assert f"WA_IMAGE={OLD}" in env  # unchanged


def test_a_bad_signature_changes_nothing(tmp_path: Path) -> None:
    code, out, env, _ = _run(tmp_path, "0.23.0", OLD, FAKE_COSIGN_FAIL="1")
    assert code == 1 and "could not be verified" in out and f"WA_IMAGE={OLD}" in env


def test_building_from_the_folder_or_a_plain_copy_is_not_touched(tmp_path: Path) -> None:
    assert _run(tmp_path, "0.23.0", None)[0] == 0  # no WA_IMAGE: the files are the version
    code, out, env, calls = _run(tmp_path / "copy", "0.23.0", OLD, checkout=False)
    assert code == 0 and out == "" and calls == ""  # a copy's files do not say which release they are
