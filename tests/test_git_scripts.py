"""git-setup.sh and sync.sh against a real (local) git remote."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def sh(*cmd: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), cwd=cwd, env=env, capture_output=True, text=True, check=False, timeout=60)


def commit(repo: Path, name: str, text: str, tag: str | None = None) -> str:
    (repo / name).write_text(text)
    sh(*GIT, "add", "-A", cwd=repo)
    sh(*GIT, "commit", "-qm", text, cwd=repo)
    if tag:
        sh(*GIT, "tag", tag, cwd=repo)
    return sh("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()


@pytest.fixture
def world(tmp_path: Path) -> dict[str, object]:
    """A 'GitHub' (bare repo with v0.5.0, v0.6.0 and a newer main) and a project folder with a .env."""
    work = tmp_path / "work"
    work.mkdir()
    sh("git", "init", "-q", "-b", "main", cwd=work)
    (work / "scripts").mkdir()
    for f in ("lib-git.sh", "git-setup.sh", "sync.sh"):
        shutil.copy(ROOT / "scripts" / f, work / "scripts" / f)
    (work / "scripts" / "deploy.sh").write_text("#!/usr/bin/env bash\necho deployed-$(cat VERSION) > ../deployed.txt\n")
    c1 = commit(work, "VERSION", "0.5.0", "v0.5.0")
    c2 = commit(work, "VERSION", "0.6.0", "v0.6.0")
    c3 = commit(work, "VERSION", "0.7.0-dev")
    origin = tmp_path / "origin.git"
    sh("git", "clone", "-q", "--bare", str(work), str(origin), cwd=tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".env").write_text("WA_API_PORT=8010\n")
    (proj / "stray.txt").write_text("mine")
    (proj / "VERSION").write_text("hand-copied")
    (proj / "scripts").mkdir()
    for f in ("lib-git.sh", "git-setup.sh", "sync.sh"):
        shutil.copy(ROOT / "scripts" / f, proj / "scripts" / f)
    env = {**os.environ, "WA_REPO_URL": str(origin), "WA_DATA_DIR": str(tmp_path / "data"),
           "WA_SECRETS_DIR": str(tmp_path / "secrets")}  # fmt: skip
    return {"proj": proj, "env": env, "commits": (c1, c2, c3), "tmp": tmp_path}


def head(proj: Path) -> str:
    return sh("git", "rev-parse", "HEAD", cwd=proj).stdout.strip()


def test_setup_turns_the_folder_into_a_checkout_and_keeps_local_files(world: dict[str, object]) -> None:
    proj, env, (_, _, c3) = world["proj"], world["env"], world["commits"]  # type: ignore[misc]
    r = sh("bash", "scripts/git-setup.sh", cwd=proj, env=env)  # type: ignore[arg-type]
    assert r.returncode == 0, r.stdout + r.stderr
    assert head(proj) == c3 and (proj / "VERSION").read_text() == "0.7.0-dev"
    assert (proj / ".env").read_text() == "WA_API_PORT=8010\n" and (proj / "stray.txt").read_text() == "mine"
    assert list((world["tmp"] / "data").glob("project-before-git-*.tar.gz"))  # type: ignore[operator]
    # run again: harmless
    assert sh("bash", "scripts/git-setup.sh", cwd=proj, env=env).returncode == 0  # type: ignore[arg-type]


def test_setup_that_cannot_reach_the_repository_says_what_to_do_and_leaves_no_checkout(
    world: dict[str, object],
) -> None:
    proj, env = world["proj"], dict(world["env"])  # type: ignore[arg-type]
    env["WA_REPO_URL"] = str(world["tmp"] / "nowhere.git")  # type: ignore[operator]
    r = sh("bash", "scripts/git-setup.sh", cwd=proj, env=env)  # type: ignore[arg-type]
    assert r.returncode == 1 and "github_token.txt" in r.stdout and "Could not fetch" in r.stdout


def test_sync_to_a_tag_a_commit_and_back_to_main(world: dict[str, object]) -> None:
    proj, env, (c1, c2, c3) = world["proj"], world["env"], world["commits"]  # type: ignore[misc]
    sh("bash", "scripts/git-setup.sh", cwd=proj, env=env)  # type: ignore[arg-type]
    r = sh("bash", "scripts/sync.sh", "v0.6.0", cwd=proj, env=env)  # type: ignore[arg-type]
    assert r.returncode == 0, r.stdout + r.stderr
    assert head(proj) == c2 and (proj / "VERSION").read_text() == "0.6.0"
    assert sh("bash", "scripts/sync.sh", c1, cwd=proj, env=env).returncode == 0  # type: ignore[arg-type]
    assert (proj / "VERSION").read_text() == "0.5.0"
    r = sh("bash", "scripts/sync.sh", cwd=proj, env=env)  # type: ignore[arg-type]
    assert head(proj) == c3 and "main" in r.stdout
    assert sh("bash", "scripts/sync.sh", cwd=proj, env=env).stdout.startswith("Already at")  # type: ignore[arg-type]
    assert (proj / ".env").exists() and (proj / "stray.txt").exists()


def test_sync_refuses_an_unknown_release_and_changes_nothing(world: dict[str, object]) -> None:
    proj, env, (_, _, c3) = world["proj"], world["env"], world["commits"]  # type: ignore[misc]
    sh("bash", "scripts/git-setup.sh", cwd=proj, env=env)  # type: ignore[arg-type]
    r = sh("bash", "scripts/sync.sh", "v9.9.9", cwd=proj, env=env)  # type: ignore[arg-type]
    assert r.returncode == 1 and "No such release" in r.stdout and head(proj) == c3


def test_sync_discards_edits_to_tracked_files_but_not_untracked_ones(world: dict[str, object]) -> None:
    proj, env = world["proj"], world["env"]
    sh("bash", "scripts/git-setup.sh", cwd=proj, env=env)  # type: ignore[arg-type]
    (proj / "VERSION").write_text("edited on the server")  # type: ignore[operator]
    assert sh("bash", "scripts/sync.sh", "v0.6.0", cwd=proj, env=env).returncode == 0  # type: ignore[arg-type]
    assert (proj / "VERSION").read_text() == "0.6.0"  # type: ignore[operator]


def test_sync_deploy_runs_the_deploy_script_from_the_new_files(world: dict[str, object]) -> None:
    proj, env = world["proj"], world["env"]
    sh("bash", "scripts/git-setup.sh", cwd=proj, env=env)  # type: ignore[arg-type]
    r = sh("bash", "scripts/sync.sh", "v0.5.0", "--deploy", cwd=proj, env=env)  # type: ignore[arg-type]
    assert r.returncode == 0, r.stdout + r.stderr
    assert (proj.parent / "deployed.txt").read_text().strip() == "deployed-0.5.0"  # type: ignore[attr-defined]


def test_sync_before_setup_says_to_run_setup(world: dict[str, object]) -> None:
    r = sh("bash", "scripts/sync.sh", cwd=world["proj"], env=world["env"])  # type: ignore[arg-type]
    assert r.returncode == 1 and "git-setup.sh" in r.stdout


def test_the_token_never_appears_in_arguments_or_config(world: dict[str, object]) -> None:
    proj, env, tmp = world["proj"], dict(world["env"]), world["tmp"]  # type: ignore[arg-type]
    (tmp / "secrets").mkdir()  # type: ignore[operator]
    (tmp / "secrets" / "github_token.txt").write_text("SECRETSECRETSECRET\n")  # type: ignore[operator]
    assert sh("bash", "scripts/git-setup.sh", cwd=proj, env=env).returncode == 0  # type: ignore[arg-type]
    assert "SECRETSECRET" not in (proj / ".git" / "config").read_text()  # type: ignore[operator]
