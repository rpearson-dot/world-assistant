"""The project files say which release they are, in pyproject.toml, and deploy.sh chooses the release image by it.
It must be the newest version in the changelog, or servers stay on an old release without a word (0.31.0 to 0.36.0)."""

import re
from pathlib import Path


def test_pyproject_version_is_the_newest_changelog_version() -> None:
    project = re.search(r'^version = "([^"]+)"', Path("pyproject.toml").read_text(), re.MULTILINE)
    newest = re.search(r"^## \[(\d+\.\d+\.\d+)\]", Path("CHANGELOG.md").read_text(), re.MULTILINE)
    assert project and newest
    assert project.group(1) == newest.group(1), (
        f"pyproject.toml says {project.group(1)} but the newest changelog heading is {newest.group(1)}: "
        "a release PR bumps both")
