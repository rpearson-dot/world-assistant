"""Automatic updates: the merge policy, the next version, and the whole flow against a stand-in gh."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "auto-update.sh"


def msg(*types: str) -> str:
    body = "".join(f"- dependency-name: d{i}\n  update-type: version-update:semver-{t}\n" for i, t in enumerate(types))
    return f"Bump things\n\n---\nupdated-dependencies:\n{body}..."


def sh(cmd: str, **env: str) -> str:
    out = subprocess.run(
        ["bash", str(SCRIPT), cmd], env={**os.environ, **env}, capture_output=True, text=True, check=False
    )
    return out.stdout.strip() or out.stderr.strip()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize(
    ("branch", "title", "types", "want"),
    [
        ("dependabot/pip/requests-2.33.1", "deps: bump requests from 2.33.0 to 2.33.1", ("patch",), "merge"),
        ("dependabot/pip/requests-2.34.0", "deps: bump requests from 2.33.0 to 2.34.0", ("minor",), "merge"),
        ("dependabot/pip/requests-3.0.0", "deps: bump requests from 2.33.0 to 3.0.0", ("major",), "skip"),
        ("dependabot/docker/python-3.12.9-slim", "build: bump python from 3.12.8-slim to 3.12.9-slim", ("patch",), "merge"),
        ("dependabot/docker/python-3.13-slim", "build: bump python from 3.12-slim to 3.13-slim", ("minor",), "skip"),
        ("dependabot/docker_compose/redis-7.4-alpine", "build: bump redis from 7.2-alpine to 7.4-alpine", ("patch",), "skip"),  # 7.2 -> 7.4 is minor, whatever it is called
        ("dependabot/docker_compose/redis-7.4.1", "build: bump redis from 7.4.0 to 7.4.1", ("patch",), "merge"),
        ("dependabot/docker_compose/redis-8-alpine", "build: bump redis from 7-alpine to 8-alpine", ("major",), "skip"),
        # whatever Dependabot calls it, a major change in the tag is never merged
        ("dependabot/docker_compose/redis-8-alpine", "build: bump redis from 7-alpine to 8-alpine", ("patch",), "skip"),
        ("dependabot/docker_compose/postgis-17", "build: bump postgis/postgis from 16-3.4 to 17-3.5", ("minor",), "skip"),
        ("dependabot/docker_compose/postgis-3.5", "build: bump postgis/postgis from 16-3.4 to 16-3.5", ("patch",), "skip"),
        ("dependabot/docker_compose/x", "something odd", ("patch",), "skip"),
        # real Dependabot commits for image tags carry no update type: the tag numbers decide
        ("dependabot/docker/python-3.12.9-slim", "build: bump python from 3.12.8-slim to 3.12.9-slim", (), "merge"),
        ("dependabot/docker/python-3.13-slim", "build: bump python from 3.12-slim to 3.13-slim", (), "skip"),
        ("dependabot/docker_compose/redis-8-alpine", "build: bump redis from 7-alpine to 8-alpine", (), "skip"),
        ("dependabot/docker_compose/redis-7.4-alpine", "build: bump redis from 7.2-alpine to 7.4-alpine", (), "skip"),
        ("dependabot/docker_compose/redis-7.2.5", "build: bump redis from 7.2.4 to 7.2.5", (), "merge"),
        ("dependabot/docker_compose/redis-latest", "build: bump redis from latest to 7.2", (), "skip"),
        ("dependabot/docker_compose/postgis-17", "build: bump postgis/postgis from 16-3.4 to 17-3.5", (), "skip"),
        ("dependabot/pip/group", "deps: bump the group", ("patch", "minor"), "merge"),
        ("dependabot/pip/group", "deps: bump the group", ("patch", "major"), "skip"),
        ("dependabot/github_actions/actions/checkout-7.0.2", "ci: bump actions/checkout from 7.0.1 to 7.0.2", ("patch",), "skip"),
        ("feature/something", "x", ("patch",), "skip"),
        ("dependabot/npm_and_yarn/x", "x", ("patch",), "skip"),
    ],
)
def test_policy(branch: str, title: str, types: tuple[str, ...], want: str) -> None:
    got = sh("decide", BRANCH=branch, TITLE=title, MESSAGE=msg(*types))
    assert (got == "merge") if want == "merge" else got.startswith("skip")


def test_python_libraries_need_update_metadata() -> None:
    assert sh("decide", BRANCH="dependabot/pip/x", TITLE="x", MESSAGE="just a message").startswith("skip: no update type")


@pytest.mark.parametrize(("tag", "want"), [("v0.5.0", "0.5.1"), ("v1.2.9", "1.2.10"), ("v10.0.0", "10.0.1")])
def test_next_version(tag: str, want: str) -> None:
    assert sh("next-version", TAG=tag) == want


# ---- the whole flow, with a stand-in for gh and docker ---------------------------------------

GH = r"""#!/usr/bin/env bash
S="$SCENARIO"; echo "gh $*" >> "$S/calls.log"
if [ "$1" = "workflow" ]; then exit 0; fi
# gh api [-X METHOD] path [-f k=v ...] [--jq expr]
shift; method=GET
[ "$1" = "-X" ] && { method="$2"; shift 2; }
path="$1"; shift; jq_expr=""
while [ $# -gt 0 ]; do case "$1" in --jq) jq_expr="$2"; shift 2 ;; *) shift ;; esac; done
emit() { if [ -n "$jq_expr" ]; then jq -r "$jq_expr" < "$1"; else cat "$1"; fi; }
case "$method $path" in
  "PUT repos/o/r/pulls/"*"/merge") [ ! -e "$S/merge_fails" ] || exit 1; echo '{"merged":true}' ;;
  "GET repos/o/r/pulls?"*) emit "$S/pulls.json" ;;
  "GET repos/o/r/commits/"*"/check-runs"*) sha="${path#repos/o/r/commits/}"; sha="${sha%%/*}"
      f="$S/checks.$sha.json"; [ -e "$f" ] || f="$S/checks.default.json"; emit "$f" ;;
  "GET repos/o/r/commits/"*) sha="${path#repos/o/r/commits/}"; emit "$S/commit.$sha.json" ;;
  "GET repos/o/r/releases/latest") emit "$S/release.json" ;;
  "GET repos/o/r/issues?"*) emit "$S/issues.json" ;;
  "POST repos/o/r/issues") echo '{}' ;;
  "GET repos/o/r/git/ref/heads/main") emit "$S/tip.json" ;;
  *) echo "unexpected: $method $path" >&2; exit 2 ;;
esac
"""
DOCKER = '#!/usr/bin/env bash\necho "sha256:$BASE_DIGEST"\n'


def checks(state: str = "success") -> dict:
    return {"check_runs": [
        {"name": n, "status": "completed", "conclusion": state, "started_at": "2026-10-21T09:00:00Z"}
        for n in ("test", "secrets", "image", "stack")
    ]}  # fmt: skip


@pytest.fixture
def flow(tmp_path: Path):  # type: ignore[no-untyped-def]
    import json

    if shutil.which("bash") is None or shutil.which("jq") is None:
        pytest.skip("needs bash and jq")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("gh", GH), ("docker", DOCKER)):
        (bindir / name).write_text(body)
        (bindir / name).chmod(0o755)
    sc = tmp_path / "sc"
    sc.mkdir()

    def run(*, prs: list[tuple[int, str, str, str, str]] = (), base_recorded: str | None = "python@sha256:old",  # type: ignore[assignment]
            base_now: str = "old", auto: str = "true", dry: str = "0", merge_fails: bool = False,
            ci: str = "success", release: str = "true", issues: list[dict] | None = None) -> tuple[subprocess.CompletedProcess[str], list[str]]:  # fmt: skip
        pulls = [{"number": n, "title": title, "user": {"login": "dependabot[bot]"}, "head": {"sha": sha, "ref": ref}}
                 for n, sha, ref, _, title in prs]  # fmt: skip
        (sc / "pulls.json").write_text(json.dumps(pulls))
        for _n, sha, _ref, message, _title in prs:
            (sc / f"commit.{sha}.json").write_text(json.dumps({"commit": {"message": message}}))
        (sc / "checks.default.json").write_text(json.dumps(checks(ci)))
        body = "Image: x\n" + (f"Base image: `{base_recorded}`\n" if base_recorded else "")
        (sc / "release.json").write_text(json.dumps({"tag_name": "v0.5.0", "body": body}))
        (sc / "tip.json").write_text(json.dumps({"object": {"sha": "tip123"}}))
        (sc / "issues.json").write_text(json.dumps(issues or []))
        if merge_fails:
            (sc / "merge_fails").write_text("")
        (sc / "calls.log").write_text("")
        env = {
            **os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "SCENARIO": str(sc), "REPO": "o/r",
            "AUTO_UPDATES": auto, "AUTO_RELEASE": release, "DRY_RUN": dry, "BASE_DIGEST": base_now, "POLL_SECONDS": "0",
            "CI_WAIT_SECONDS": "5",
        }  # fmt: skip
        r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60, check=False)
        return r, (sc / "calls.log").read_text().splitlines()

    return run


PR_OK = (7, "sha7", "dependabot/pip/requests-2.33.1", msg("patch"), "deps: bump requests from 2.33.0 to 2.33.1")


def test_off_by_default(flow) -> None:  # type: ignore[no-untyped-def]
    r, calls = flow(prs=[PR_OK], auto="")
    assert r.returncode == 0 and "off" in r.stdout and calls == []


def test_merges_runs_ci_and_releases_next_patch(flow) -> None:  # type: ignore[no-untyped-def]
    r, calls = flow(prs=[PR_OK])
    assert r.returncode == 0, r.stderr
    assert "#7 dependabot/pip/requests-2.33.1: merged" in r.stdout
    assert any("workflow run ci.yml" in c for c in calls)
    assert any("workflow run release.yml" in c and "version=0.5.1" in c for c in calls)
    assert any(c.startswith("gh api -X PUT") and "pulls/7/merge" in c for c in calls)


def test_unsafe_pr_is_left_alone_and_nothing_is_released(flow) -> None:  # type: ignore[no-untyped-def]
    major = (8, "sha8", "dependabot/pip/requests-3.0.0", msg("major"), "deps: bump requests from 2.33.0 to 3.0.0")
    r, calls = flow(prs=[major])
    assert "#8" in r.stdout and "needs a human look" in r.stdout and "Nothing to release" in r.stdout
    assert not any("workflow run" in c or "PUT" in c for c in calls)


def test_pr_without_green_ci_is_left_alone(flow) -> None:  # type: ignore[no-untyped-def]
    r, calls = flow(prs=[PR_OK], ci="failure")
    assert "CI has not passed yet" in r.stdout and not any("PUT" in c for c in calls)


def test_failed_merge_releases_nothing(flow) -> None:  # type: ignore[no-untyped-def]
    r, calls = flow(prs=[PR_OK], merge_fails=True)
    assert "could not be merged" in r.stdout and not any("workflow run" in c for c in calls)


def test_changed_base_image_releases_without_a_merge(flow) -> None:  # type: ignore[no-untyped-def]
    r, calls = flow(base_now="new")
    assert "base image has changed" in r.stdout
    assert not any("workflow run ci.yml" in c for c in calls)  # main already has its own CI
    assert any("workflow run release.yml" in c and "version=0.5.1" in c for c in calls)


def test_unchanged_base_image_and_no_prs_do_nothing(flow) -> None:  # type: ignore[no-untyped-def]
    r, calls = flow()
    assert "unchanged" in r.stdout and "Nothing to release" in r.stdout
    assert not any("workflow run" in c for c in calls)


def test_release_that_did_not_record_its_base_image_is_not_rebuilt(flow) -> None:  # type: ignore[no-untyped-def]
    r, calls = flow(base_recorded=None, base_now="anything")
    assert "did not record its base image" in r.stdout and not any("workflow run" in c for c in calls)


def test_dry_run_changes_nothing(flow) -> None:  # type: ignore[no-untyped-def]
    r, calls = flow(prs=[PR_OK], dry="1")
    assert "[dry run] would merge #7" in r.stdout and "[dry run] would: gh workflow run release.yml" in r.stdout
    assert not any("workflow run" in c or "PUT" in c for c in calls)


def test_without_auto_release_it_merges_and_runs_ci_but_does_not_release(flow) -> None:  # type: ignore[no-untyped-def]
    r, calls = flow(prs=[PR_OK], release="")
    assert r.returncode == 0, r.stderr
    assert "#7 dependabot/pip/requests-2.33.1: merged" in r.stdout and "release when you are ready" in r.stdout
    assert any("workflow run ci.yml" in c for c in calls)
    assert not any("release.yml" in c for c in calls)


def test_without_auto_release_a_changed_base_image_opens_one_issue(flow) -> None:  # type: ignore[no-untyped-def]
    _r, calls = flow(base_now="new", release="")
    assert any(c.startswith("gh api -X POST repos/o/r/issues") and "base image has changed" in c for c in calls)
    assert not any("release.yml" in c or "workflow run" in c for c in calls)
    again, calls2 = flow(
        base_now="new", release="",
        issues=[{"number": 12, "title": "The Python base image has changed: release when ready"}],
    )  # fmt: skip
    assert "already open (#12)" in again.stdout and not any("POST" in c for c in calls2)


def test_workflow_is_opt_in_and_limited_to_dependabot_runs() -> None:
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "auto-update.yml").read_text())
    cond = " ".join(str(wf["jobs"]["update"]["if"]).split())
    assert "vars.AUTO_UPDATES == 'true'" in cond and "dependabot[bot]" in cond
    assert wf["concurrency"]["cancel-in-progress"] is False
    env = wf["jobs"]["update"]["steps"][-1]["env"]
    assert "vars.AUTO_RELEASE" in env["AUTO_RELEASE"]  # releasing is a separate switch
    # never checks out or runs pull-request code: it only runs the script from main
    steps = wf["jobs"]["update"]["steps"]
    assert all("ref" not in (s.get("with") or {}) for s in steps)
