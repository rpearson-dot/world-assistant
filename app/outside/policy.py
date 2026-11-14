"""When an outside module's new release installs by itself, and when it is held for you: the rules agreed on
14 November. Pure.

- Automatic updates are on by default. A new release that passes every check installs by itself.
- It is held, with a "needs your attention" warning, when it asks for anything more (never agreed automatically),
  its scan finds a high or critical vulnerability, its signature or build proof is missing or wrong, its licence
  changed, or it needs a newer World Assistant.
- Falling behind is a warning, never a stop: a release held over a week, a new vulnerability in the installed
  version, or a long time without a release while its parts have known flaws.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.outside.manifest import Manifest, more_access

HELD_TOO_LONG = timedelta(days=7)
QUIET_TOO_LONG = timedelta(days=365)


@dataclass
class Checks:
    """What the runner found about one release's image."""

    signed: bool = False  # signed by the repository's own release workflow, for this tag
    provenance: bool = False  # a build attestation tying the image to the repository and commit
    sbom: bool = False  # an SPDX or CycloneDX SBOM attested to the image
    high: int = 0  # high-severity vulnerabilities found
    critical: int = 0
    licence: str | None = None  # what GitHub says the repository's licence is
    error: str | None = None  # the checks could not be completed (why)
    found: list[dict[str, Any]] = field(default_factory=list)  # the worst findings, for the warning

    @classmethod
    def from_json(cls, d: dict[str, Any] | None) -> Checks:
        return cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})


def problems(new: Manifest, checks: Checks, allow_vulnerable: bool = False) -> list[str]:
    """Why a release may not be installed at all (first install or update), in words. Empty: it may."""
    out = []
    if checks.error:
        out.append(f"its checks could not be completed: {checks.error}")
        return out
    if not checks.signed:
        out.append("its image is not signed by its repository's release workflow for this release")
    if not checks.provenance:
        out.append("its image has no build attestation tying it to the repository and this release's commit")
    if not checks.sbom:
        out.append("its image has no SBOM (software bill of materials) attached")
    if (checks.high or checks.critical) and not allow_vulnerable:
        out.append(f"its scan found {checks.critical} critical and {checks.high} high-severity vulnerabilities")
    if checks.licence and checks.licence != new.licence:
        out.append(f"its repository's licence ({checks.licence}) is not the one it declares ({new.licence})")
    return out


def update(installed: Manifest, new: Manifest, checks: Checks, auto: bool = True) -> tuple[str, list[str]]:
    """("install", []) when a new release may install by itself; ("hold", reasons) when it needs your attention."""
    reasons = problems(new, checks)
    more = more_access(installed, new)
    if more:
        reasons.append("it asks for more: " + "; ".join(more))
    if new.licence != installed.licence:
        reasons.append(f"its licence changed from {installed.licence} to {new.licence}")
    if new.id != installed.id or new.image != installed.image:
        reasons.append("its id or image changed")
    if reasons:
        return "hold", reasons
    if not auto:
        return "hold", ["automatic updates are off for this module"]
    return "install", []


def warnings(state: dict[str, Any], now: datetime) -> list[str]:
    """Falling behind, from a module's state: {held_since, held_reasons, installed_checks, last_release_at}."""
    out = []
    held = state.get("held_since")
    if held and now - held > HELD_TOO_LONG:
        out.append(f"a newer release has waited for you for {(now - held).days} days")
    inst = Checks.from_json(state.get("installed_checks"))
    if inst.critical or inst.high:
        out.append(f"the version running now has {inst.critical} critical and {inst.high} high-severity vulnerabilities "
                   "found since it was installed")  # fmt: skip
    last = state.get("last_release_at")
    if last and now - last > QUIET_TOO_LONG and (inst.critical or inst.high):
        out.append(f"it has had no release for {(now - last).days // 30} months")
    return out
