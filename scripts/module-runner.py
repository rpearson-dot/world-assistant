#!/usr/bin/env python3
"""The outside-module runner: the only part of World Assistant that starts, checks and removes outside modules'
containers. It runs in the updater container (which drives Docker) and takes its work from World Assistant's API:

    GET  /v1/outside/runner/next          the next job, or nothing
    POST /v1/outside/runner/{id}/done     what happened

Jobs:
  check   an image: pulled by digest; its signature, build attestation and SBOM checked with cosign against the
          repository's own release workflow for that tag; scanned with Trivy for high and critical vulnerabilities.
  start   a module's container: its own network (internal: it reaches only World Assistant's module gate), its own
          volume, not root, read-only, no extra privileges, memory, CPU and process limits.
  stop    its container.
  usage   its volume's size and its memory now.
  remove  its container, volume and images, then a search for anything left with its label or name.

Standard library only (the updater image adds python3). `python3 scripts/module-runner.py once` does every waiting
job and stops; scripts/updater.sh calls it each minute.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

API = os.environ.get("AUTO_DEPLOY_API", "http://wa-proxy:8000").rstrip("/")
SECRETS = Path(os.environ.get("WA_SECRETS_DIR", "/mnt/user/appdata/world-assistant/secrets"))
COSIGN = os.environ.get("AUTO_DEPLOY_COSIGN_IMAGE", "ghcr.io/sigstore/cosign/cosign:v3.0.6")
TRIVY = os.environ.get("WA_TRIVY_IMAGE", "aquasec/trivy:0.66.0")
NETWORK = "wa-modules"
ISSUER = "https://token.actions.githubusercontent.com"
ID = re.compile(r"^[a-z][a-z0-9_]{1,23}$")
REF = re.compile(r"^ghcr\.io/[a-z0-9_.-]+/[a-z0-9_.-]+@sha256:[0-9a-f]{64}$")


# --- pure: what to run --------------------------------------------------------------------------------------------


def identity(repository: str, tag: str) -> str:
    """The signing identity a module's image must carry: its own repository's workflow, run for this tag."""
    m = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", repository)
    if not m or not re.fullmatch(r"v?\d+\.\d+\.\d+", tag):
        raise ValueError("repository or tag")
    return rf"^https://github\.com/{re.escape(m[1])}/{re.escape(m[2])}/\.github/workflows/[^@]+@refs/tags/{re.escape(tag)}$"


def name(module: str) -> str:
    if not ID.fullmatch(module):
        raise ValueError("module id")
    return f"wa-x-{module.replace('_', '-')}"


def run_args(spec: dict[str, Any]) -> list[str]:
    """`docker run` for a module: isolated, limited, labelled. Keys and the gate's address come as environment."""
    ref, module = spec["ref"], spec["module"]
    if not REF.fullmatch(ref):
        raise ValueError("image reference must be ghcr.io/owner/name@sha256:…")
    n = name(module)
    args = ["docker", "run", "-d", "--name", n, "--hostname", n, "--network", NETWORK,
            "--label", f"wa.module={module}", "--label", f"wa.module.version={spec.get('version', '')}",
            "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "--user", "10001:10001",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "256",
            "--memory", f"{int(spec['memory_mb'])}m", "--memory-swap", f"{int(spec['memory_mb'])}m", "--cpus", "1",
            "--restart", "unless-stopped", "--log-opt", "max-size=5m", "--log-opt", "max-file=2",
            "-v", f"{n}:/data"]  # fmt: skip
    for k, v in sorted((spec.get("env") or {}).items()):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,60}", k):
            raise ValueError(f"environment name {k}")
        args += ["-e", f"{k}={v}"]
    return [*args, ref]


def trivy_counts(report: dict[str, Any]) -> dict[str, Any]:
    """High and critical findings from `trivy image --format json`, with the worst few named."""
    found = []
    for res in report.get("Results") or []:
        for v in res.get("Vulnerabilities") or []:
            if v.get("Severity") in ("HIGH", "CRITICAL"):
                found.append({"id": v.get("VulnerabilityID"), "package": v.get("PkgName"), "severity": v.get("Severity"),
                              "fixed_in": v.get("FixedVersion")})  # fmt: skip
    found.sort(key=lambda f: (f["severity"] != "CRITICAL", f["id"] or ""))
    return {"critical": sum(f["severity"] == "CRITICAL" for f in found), "high": sum(f["severity"] == "HIGH" for f in found),
            "found": found[:10]}  # fmt: skip


# --- doing it -----------------------------------------------------------------------------------------------------


def sh(args: list[str], timeout: int = 600) -> tuple[int, str]:
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    return p.returncode, (p.stdout + p.stderr)[-4000:]


def cosign(*args: str) -> bool:
    home = os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker"))
    code, _ = sh(["docker", "run", "--rm", "-v", f"{home}:/root/.docker:ro", COSIGN, *args], timeout=180)
    return code == 0


def check(job: dict[str, Any]) -> dict[str, Any]:
    ref, ident = job["ref"], identity(job["repository"], job["tag"])
    if not REF.fullmatch(ref):
        return {"error": "the image reference is not ghcr.io/owner/name@sha256:…"}
    code, out = sh(["docker", "pull", ref])
    if code:
        return {"error": f"the image could not be pulled: {out.strip().splitlines()[-1] if out.strip() else code}"}
    who = ["--certificate-identity-regexp", ident, "--certificate-oidc-issuer", ISSUER]
    res: dict[str, Any] = {"signed": cosign("verify", *who, ref)}
    res["provenance"] = any(cosign("verify-attestation", "--type", t, *who, ref) for t in ("slsaprovenance1", "slsaprovenance"))
    res["sbom"] = any(cosign("verify-attestation", "--type", t, *who, ref) for t in ("spdxjson", "cyclonedx"))
    code, out = sh(["docker", "run", "--rm", "-v", "/var/run/docker.sock:/var/run/docker.sock", TRIVY, "image", "--quiet",
                    "--severity", "HIGH,CRITICAL", "--format", "json", ref], timeout=900)  # fmt: skip
    try:
        res |= trivy_counts(json.loads(out[out.index("{"):]))
    except (ValueError, KeyError):
        res["error"] = "the vulnerability scan did not finish"
    return res


def start(job: dict[str, Any]) -> dict[str, Any]:
    n = name(job["module"])
    if sh(["docker", "network", "inspect", NETWORK])[0]:  # normally made by compose with the gate on it
        sh(["docker", "network", "create", "--internal", "--label", "wa.modules=1", NETWORK])
    sh(["docker", "rm", "-f", n])
    code, out = sh(run_args(job))
    if code:
        return {"error": out.strip()[-300:]}
    code, out = sh(["docker", "inspect", "-f", "{{.State.Running}}", n])
    return {"running": out.strip() == "true"}


def stop(job: dict[str, Any]) -> dict[str, Any]:
    code, _out = sh(["docker", "rm", "-f", name(job["module"])])
    return {"stopped": code == 0}


def usage(job: dict[str, Any]) -> dict[str, Any]:
    n = name(job["module"])
    code, out = sh(["docker", "run", "--rm", "--network", "none", "-v", f"{n}:/d:ro", "--entrypoint", "du",
                    os.environ.get("WA_UPDATER_IMAGE", "world-assistant-updater:local"), "-sm", "/d"])  # fmt: skip
    storage = int(out.split()[0]) if code == 0 and out.split() and out.split()[0].isdigit() else None
    code, out = sh(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", n])
    m = re.match(r"([\d.]+)\s*(MiB|GiB|KiB)", out.strip())
    mem = None if not m else float(m[1]) * {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024}[m[2]]
    return {"storage_mb": storage, "memory_mb": round(mem) if mem is not None else None}


def remove(job: dict[str, Any]) -> dict[str, Any]:
    """Everything of a module's on this server, then a search for anything left (the report lists each place)."""
    module, n = job["module"], name(job["module"])
    sh(["docker", "rm", "-f", n])
    for c in sh(["docker", "ps", "-aq", "--filter", f"label=wa.module={module}"])[1].split():
        sh(["docker", "rm", "-f", c])
    sh(["docker", "volume", "rm", "-f", n])
    for ref in job.get("refs") or []:
        sh(["docker", "image", "rm", "-f", ref])
    left_c = sh(["docker", "ps", "-aq", "--filter", f"label=wa.module={module}"])[1].split()
    left_v = [v for v in sh(["docker", "volume", "ls", "-q"])[1].split() if v == n]
    left_i = [r for r in job.get("refs") or [] if sh(["docker", "image", "inspect", r])[0] == 0]
    return {"checked": {"containers": left_c, "volumes": left_v, "images": left_i}}


def remove_image(job: dict[str, Any]) -> dict[str, Any]:
    """A version no longer used (after an update)."""
    for ref in job.get("refs") or []:
        if REF.fullmatch(ref):
            sh(["docker", "image", "rm", ref])
    return {"ok": True}


DO = {"check": check, "start": start, "stop": stop, "usage": usage, "remove": remove, "remove_image": remove_image}


def api(method: str, path: str, body: Any = None) -> Any:
    token = (SECRETS / "api_token.txt").read_text().strip()
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})  # fmt: skip
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"null")


def once() -> int:
    done = 0
    for _ in range(20):
        job = api("GET", "/v1/outside/runner/next")
        if not job:
            break
        try:
            result = DO[job["kind"]](job.get("spec") or {})
        except Exception as exc:  # noqa: BLE001 - reported back, never left hanging
            result = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        api("POST", f"/v1/outside/runner/{job['id']}/done", result)
        done += 1
    return done


if __name__ == "__main__":
    if sys.argv[1:] == ["once"]:
        try:
            once()
        except Exception as exc:  # noqa: BLE001 - the updater's loop carries on
            print(f"module runner: {exc}", file=sys.stderr)
    else:
        print(__doc__)
