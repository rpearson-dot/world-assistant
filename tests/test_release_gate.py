"""The release gate script, run against a stand-in for the gh command line."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "scripts" / "release-gate.sh"
SHA = "a" * 40

STUB = r"""#!/usr/bin/env bash
# stand-in for: gh api <path> [--jq expr]
S="$SCENARIO"
path="$2"; jq_expr=""
[ "${3:-}" = "--jq" ] && jq_expr="$4"
emit() { if [ -n "$jq_expr" ]; then jq -r "$jq_expr" < "$1"; else cat "$1"; fi; }
case "$path" in
  repos/*/git/ref/heads/main) emit "$S/tip.json" ;;
  repos/*/git/ref/tags/*) [ -e "$S/tag_exists" ] || exit 1; echo '{}' ;;
  repos/*/releases/tags/*) [ -e "$S/release_exists" ] || exit 1; echo '{}' ;;
  repos/*/commits/*/check-runs*)
    n=$(( $(cat "$S/calls" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$S/calls"
    f="$S/checks.$n.json"; [ -e "$f" ] || f="$(ls "$S"/checks.*.json | sort -V | tail -1)"
    emit "$f" ;;
  *) echo "unexpected: $path" >&2; exit 2 ;;
esac
"""


def runs(**states: str) -> dict:
    """check-runs JSON; each value is 'status/conclusion', e.g. 'completed/success' or 'in_progress/'."""
    out = []
    for i, (name, state) in enumerate(states.items()):
        status, _, conclusion = state.partition("/")
        out.append({"name": name, "status": status, "conclusion": conclusion or None,
                    "started_at": f"2026-10-21T09:0{i}:00Z"})  # fmt: skip
    return {"check_runs": out}


GREEN = {"test": "completed/success", "secrets": "completed/success", "image": "completed/success", "stack": "completed/success"}


@pytest.fixture
def gate(tmp_path: Path):  # type: ignore[no-untyped-def]
    if shutil.which("bash") is None or shutil.which("jq") is None:
        pytest.skip("needs bash and jq")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(STUB)
    (bindir / "gh").chmod(0o755)
    sc = tmp_path / "scenario"
    sc.mkdir()

    def run(*sequence: dict, tip: str = SHA, tag: bool = False, release: bool = False,
            event: str = "dispatch", wait: int = 3) -> subprocess.CompletedProcess[str]:  # fmt: skip
        for old in sc.iterdir():  # every call starts from a clean scenario
            old.unlink()
        (sc / "tip.json").write_text(json.dumps({"object": {"sha": tip}}))
        for i, seq in enumerate(sequence or (runs(**GREEN),), start=1):
            (sc / f"checks.{i}.json").write_text(json.dumps(seq))
        if tag:
            (sc / "tag_exists").write_text("")
        if release:
            (sc / "release_exists").write_text("")
        env = {
            "PATH": f"{bindir}:{os.environ['PATH']}", "SCENARIO": str(sc), "REPO": "o/r", "SHA": SHA,
            "VERSION": "1.2.3", "EVENT": event, "WAIT_SECONDS": str(wait), "POLL_SECONDS": "1",
        }  # fmt: skip
        return subprocess.run(["bash", str(GATE)], env=env, capture_output=True, text=True, timeout=60, check=False)

    return run


def test_passes_when_everything_is_green(gate) -> None:  # type: ignore[no-untyped-def]
    r = gate()
    assert r.returncode == 0 and "Release gate passed" in r.stdout


def test_ignores_checks_that_are_not_required(gate) -> None:  # type: ignore[no-untyped-def]
    r = gate(runs(**GREEN, analyze="completed/skipped", other="completed/failure"))
    assert r.returncode == 0


def test_a_failed_check_stops_at_once(gate) -> None:  # type: ignore[no-untyped-def]
    r = gate(runs(test="completed/failure", secrets="in_progress/", image="completed/success"), wait=60)
    assert r.returncode == 1 and "'test' finished as 'failure'" in r.stderr and "Waiting" not in r.stdout


def test_cancelled_counts_as_failed(gate) -> None:  # type: ignore[no-untyped-def]
    assert gate(runs(**{**GREEN, "image": "completed/cancelled"})).returncode == 1


def test_waits_for_ci_then_passes(gate) -> None:  # type: ignore[no-untyped-def]
    r = gate(runs(test="in_progress/", secrets="completed/success"), runs(**GREEN), wait=30)
    assert r.returncode == 0 and "Waiting for CI: test (in_progress)" in r.stdout


def test_times_out_when_ci_never_finishes(gate) -> None:  # type: ignore[no-untyped-def]
    r = gate(runs(test="in_progress/", secrets="completed/success", image="completed/success"), wait=2)
    assert r.returncode == 1 and "did not finish within 2s" in r.stderr


def test_missing_checks_count_as_not_started(gate) -> None:  # type: ignore[no-untyped-def]
    r = gate(runs(test="completed/success"), wait=2)
    assert r.returncode == 1 and "secrets (not started)" in r.stderr and "image (not started)" in r.stderr


def test_a_rerun_replaces_an_earlier_failure(gate) -> None:  # type: ignore[no-untyped-def]
    both = {"check_runs": [
        {"name": "test", "status": "completed", "conclusion": "failure", "started_at": "2026-10-21T09:00:00Z"},
        {"name": "test", "status": "completed", "conclusion": "success", "started_at": "2026-10-21T09:10:00Z"},
        *runs(secrets="completed/success", image="completed/success", stack="completed/success")["check_runs"],
    ]}  # fmt: skip
    assert gate(both).returncode == 0


def test_refuses_a_commit_that_is_not_the_tip_of_main(gate) -> None:  # type: ignore[no-untyped-def]
    r = gate(tip="b" * 40)
    assert r.returncode == 1 and "not the tip of main" in r.stderr


def test_refuses_an_existing_tag_or_release(gate) -> None:  # type: ignore[no-untyped-def]
    assert "tag v1.2.3 already exists" in gate(tag=True).stderr
    assert "release v1.2.3 already exists" in gate(release=True).stderr
    # a pushed tag exists by definition, so only the release counts then
    assert gate(tag=True, event="tag").returncode == 0


def test_workflow_makes_release_depend_on_the_gate() -> None:
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text())
    assert wf["jobs"]["release"]["needs"] == "gate"
    assert wf["jobs"]["gate"]["permissions"] == {"contents": "read", "checks": "read"}
    steps = " ".join(str(s.get("run", "")) for s in wf["jobs"]["gate"]["steps"])
    assert "scripts/release-gate.sh" in steps
