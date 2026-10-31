"""The Tailscale sidecar hands containerboot the key as Tailscale wants it."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _key_given(tmp: Path, content: str | None) -> str:
    svc = yaml.safe_load((ROOT / "docker-compose.tailscale.yml").read_text())["services"]["wa-tailscale"]
    script = svc["entrypoint"][2].replace("$$", "$")
    secret = tmp / "tailscale_authkey"
    if content is not None:
        secret.write_text(content)
    boot = tmp / "containerboot"
    boot.write_text('#!/bin/sh\nprintf "%s" "${TS_AUTHKEY-<unset>}"\n')
    boot.chmod(0o755)
    script = script.replace("/run/secrets/tailscale_authkey", str(secret)).replace("/usr/local/bin/containerboot", str(boot))
    return subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=10, check=True).stdout


def test_an_auth_key_is_passed_as_it_is(tmp_path: Path) -> None:
    assert _key_given(tmp_path, "tskey-auth-abc123-XYZ\n") == "tskey-auth-abc123-XYZ"  # no ?ephemeral=false on it


def test_an_oauth_client_secret_is_asked_for_a_lasting_device(tmp_path: Path) -> None:
    assert _key_given(tmp_path, "tskey-client-k1-s3cr3t") == "tskey-client-k1-s3cr3t?ephemeral=false"


def test_no_key_leaves_it_to_sign_in_by_link(tmp_path: Path) -> None:
    assert _key_given(tmp_path, "") == "<unset>"
    missing = tmp_path / "none"
    missing.mkdir()
    assert _key_given(missing, None) == "<unset>"  # no secret file at all


def test_the_key_is_used_only_to_sign_in_the_first_time() -> None:
    svc = yaml.safe_load((ROOT / "docker-compose.tailscale.yml").read_text())["services"]["wa-tailscale"]
    assert svc["environment"]["TS_AUTH_ONCE"] == "true"  # a used one-off key must not log a signed-in device out
