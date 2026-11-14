"""An outside module's releases on GitHub: the newest release, the commit its tag points at, its manifest at that
commit, its release lock (which image digest it built) and the repository's licence. Public repositories only,
through GitHub's REST API (no token; 60 requests an hour from one address is plenty for a daily check).

Nothing here is trusted on its own: the image's signature and build attestation, checked by the runner, are what
tie the digest to this repository and tag.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx

API = "https://api.github.com"
REPO = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
TAG = re.compile(r"^v?\d+\.\d+\.\d+$")


class GitHubError(Exception):
    pass


def owner_repo(url: str) -> tuple[str, str]:
    m = REPO.match(url.strip())
    if not m:
        raise GitHubError("give the repository's address, like https://github.com/owner/name")
    return m[1], m[2]


async def _get(client: httpx.AsyncClient, path: str, accept: str = "application/vnd.github+json") -> httpx.Response:
    r = await client.get(API + path, headers={"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}, timeout=30)
    if r.status_code == 404:
        raise GitHubError(f"not found on GitHub ({path}): is the repository public, and does it have a release?")
    if r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0":
        raise GitHubError("GitHub's limit for this address is used up for the hour: it is tried again later")
    r.raise_for_status()
    return r


def lock(body: dict[str, Any], tag: str) -> dict[str, str]:
    """The release's wa-module.lock.json: {image, digest, tag, commit}, written by the module's release workflow."""
    image, digest = str(body.get("image") or ""), str(body.get("digest") or "")
    if not DIGEST.fullmatch(digest):
        raise GitHubError("wa-module.lock.json: digest is sha256:… (64 hex characters)")
    if body.get("tag") not in (None, tag):
        raise GitHubError(f"wa-module.lock.json is for {body.get('tag')}, not {tag}")
    return {"image": image, "digest": digest, "commit": str(body.get("commit") or "")}


async def release(client: httpx.AsyncClient, url: str, tag: str | None = None) -> dict[str, Any]:
    """The newest release (or a given tag): its tag, date, commit, manifest text, lock and the repository's licence."""
    owner, name = owner_repo(url)
    rel = (await _get(client, f"/repos/{owner}/{name}/releases/" + (f"tags/{tag}" if tag else "latest"))).json()
    tag = str(rel.get("tag_name") or "")
    if not TAG.fullmatch(tag):
        raise GitHubError(f"its release tag {tag!r} is not a version like v1.2.0")
    if rel.get("draft") or rel.get("prerelease"):
        raise GitHubError(f"{tag} is a draft or a pre-release")
    ref = (await _get(client, f"/repos/{owner}/{name}/git/ref/tags/{tag}")).json()
    obj = ref.get("object") or {}
    sha = obj.get("sha")
    if obj.get("type") == "tag":  # an annotated tag: the commit it points at
        sha = ((await _get(client, f"/repos/{owner}/{name}/git/tags/{sha}")).json().get("object") or {}).get("sha")
    if not sha or not re.fullmatch(r"[0-9a-f]{40}", str(sha)):
        raise GitHubError(f"the commit for {tag} could not be found")
    manifest = (await _get(client, f"/repos/{owner}/{name}/contents/wa-module.toml?ref={sha}", "application/vnd.github.raw")).text
    asset = next((a for a in rel.get("assets") or [] if a.get("name") == "wa-module.lock.json"), None)
    if asset is None:
        raise GitHubError(f"{tag} has no wa-module.lock.json (the module's release workflow attaches it)")
    lk = lock((await _get(client, f"/repos/{owner}/{name}/releases/assets/{asset['id']}", "application/octet-stream")).json(), tag)
    if lk["commit"] and lk["commit"] != sha:
        raise GitHubError(f"wa-module.lock.json was built from {lk['commit'][:7]}, but {tag} is {sha[:7]}")
    repo = (await _get(client, f"/repos/{owner}/{name}")).json()
    published = rel.get("published_at")
    return {"tag": tag, "commit": sha, "manifest": manifest, "lock": lk, "licence": ((repo.get("license") or {}).get("spdx_id")),
            "published_at": datetime.fromisoformat(published) if published else None,
            "archived": bool(repo.get("archived"))}  # fmt: skip
