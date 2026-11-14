"""An outside module's manifest (`wa-module.toml` at the top of its repository): who it is, what it does with data,
what it asks for, and its limits. Read and checked here, before anything is fetched or run. Pure.

The design is docs/outside-modules.md. In short: an outside module runs in its own container and reaches World
Assistant only through the module API, so everything it may have is what this file declares and you agree to.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import asdict, dataclass, field
from typing import Any

ID = re.compile(r"^[a-z][a-z0-9_]{1,23}$")
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
HOST = re.compile(r"^(\*\.)?([a-z0-9-]+\.)+[a-z]{2,}$")
KEY = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_*]+$")
IMAGE = re.compile(r"^ghcr\.io/[a-z0-9_.-]+/[a-z0-9_.-]+$")
ROLES = ("source", "processor", "output")
PRECISION = ("none", "postcode district", "exact")
# Open-source licences (SPDX ids) an outside module may use
LICENCES = frozenset({"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MPL-2.0", "GPL-2.0-only", "GPL-2.0-or-later",
                      "GPL-3.0-only", "GPL-3.0-or-later", "LGPL-2.1-only", "LGPL-2.1-or-later", "LGPL-3.0-only",
                      "LGPL-3.0-or-later", "AGPL-3.0-only", "AGPL-3.0-or-later", "Unlicense", "0BSD", "EUPL-1.2"})  # fmt: skip
# The most a module may ask for; World Assistant refuses a manifest over these
CEILING = {"storage_mb": 2000, "memory_mb": 1024, "settings_fields": 20, "pages": 3, "requests_per_minute": 600}
FLOOR = {"storage_mb": 0, "memory_mb": 64, "settings_fields": 0, "pages": 0, "requests_per_minute": 1}
DEFAULT_LIMITS = {"storage_mb": 50, "memory_mb": 256, "settings_fields": 10, "pages": 1, "requests_per_minute": 60}
FIELD_TYPES = ("text", "number", "boolean", "choice")
# Personal data a module may ask for, each agreed (or not) on its own at install
DATA = {
    "home_location": "your home's location",
    "places": "your saved places",
    "vehicles": "your vehicles (registrations)",
    "accounts": "usernames and account ids you give it",
}


class ManifestError(ValueError):
    """The manifest is not acceptable; the message says which field and why, in words."""


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    type: str = "text"
    choices: tuple[str, ...] = ()
    default: Any = None


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    version: str
    sdk: int
    licence: str
    repository: str
    role: str
    image: str
    min_wa: str = "0.0.0"
    reads: tuple[str, ...] = ()
    gives: tuple[str, ...] = ()
    controls: tuple[str, ...] = ()
    keys: dict[str, dict[str, Any]] = field(default_factory=dict)  # name: {label, required}
    hosts: tuple[str, ...] = ()  # outside hosts it may reach
    home_devices: tuple[str, ...] = ()  # devices on the home network it asks you for (by what they are)
    data: dict[str, str] = field(default_factory=dict)  # home_location: precision; places/live_location/...: "yes"
    home_assistant_read: tuple[str, ...] = ()
    home_assistant_write: tuple[str, ...] = ()
    notifications: bool = False
    limits: dict[str, int] = field(default_factory=dict)
    settings: tuple[Field, ...] = ()
    purposes: dict[str, str] = field(default_factory=dict)  # host: why personal data goes there

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _list(v: Any, name: str, pattern: re.Pattern[str] | None = None, most: int = 20) -> tuple[str, ...]:
    if v is None:
        return ()
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ManifestError(f"{name} is a list of words")
    if len(v) > most:
        raise ManifestError(f"{name}: at most {most}")
    out = tuple(x.strip() for x in v if x.strip())
    for x in out:
        if len(x) > 200:
            raise ManifestError(f"{name}: each at most 200 characters")
        if pattern and not pattern.fullmatch(x):
            raise ManifestError(f"{name}: {x!r} is not allowed")
    return out


def _ver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def parse(text: str, wa_version: str, sdk_version: int) -> Manifest:
    """A manifest from wa-module.toml's text, checked. Raises ManifestError with the first problem."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"wa-module.toml is not valid TOML: {exc}") from exc
    known = {"id", "name", "version", "sdk", "min_wa", "licence", "repository", "role", "reads", "gives", "controls",
             "image", "keys", "network", "data", "limits", "settings", "purposes"}  # fmt: skip
    extra = set(raw) - known
    if extra:
        raise ManifestError(f"unknown fields: {', '.join(sorted(extra))}")
    get = lambda k: raw.get(k)
    mid = str(get("id") or "")
    if not ID.fullmatch(mid):
        raise ManifestError("id: 2 to 24 lower-case letters, digits and _, starting with a letter")
    name = str(get("name") or "").strip()
    if not 1 <= len(name) <= 60:
        raise ManifestError("name: 1 to 60 characters")
    version = str(get("version") or "")
    if not VERSION.fullmatch(version):
        raise ManifestError("version: like 1.2.0")
    sdk = get("sdk")
    if not isinstance(sdk, int) or sdk < 3:
        raise ManifestError("sdk: 3 or later (outside modules came with SDK 3)")
    if sdk > sdk_version:
        raise ManifestError(f"it needs module SDK {sdk}; this World Assistant offers {sdk_version}: update World Assistant")
    min_wa = str(get("min_wa") or "0.0.0")
    if not VERSION.fullmatch(min_wa):
        raise ManifestError("min_wa: like 0.55.0")
    if _ver(min_wa) > _ver(wa_version):
        raise ManifestError(f"it needs World Assistant {min_wa} or later; this is {wa_version}: update World Assistant")
    licence = str(get("licence") or "")
    if licence not in LICENCES:
        raise ManifestError(f"licence: an open-source licence's SPDX id (such as MIT or Apache-2.0), not {licence!r}")
    repo = str(get("repository") or "")
    if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ManifestError("repository: https://github.com/owner/name")
    role = str(get("role") or "")
    if role not in ROLES:
        raise ManifestError("role: source, processor or output")
    img = get("image") or {}
    image = str(img.get("name") if isinstance(img, dict) else img or "")
    if not IMAGE.fullmatch(image):
        raise ManifestError("image.name: the module's image on GitHub's registry, ghcr.io/owner/name (no tag: the release says which)")
    owner = repo.split("/")[3].lower()
    if image.split("/")[1] != owner:
        raise ManifestError("image.name must be under the repository's own owner on ghcr.io")
    keys: dict[str, dict[str, Any]] = {}
    for k, v in (get("keys") or {}).items():
        if not KEY.fullmatch(k):
            raise ManifestError(f"keys: {k!r} is lower-case letters, digits and _")
        v = v if isinstance(v, dict) else {}
        keys[k] = {"label": str(v.get("label") or k)[:60], "required": bool(v.get("required", False))}
    if len(keys) > 10:
        raise ManifestError("keys: at most 10")
    net = get("network") or {}
    hosts = _list(net.get("hosts"), "network.hosts", HOST)
    devices = _list(net.get("home_devices"), "network.home_devices", most=8)
    data_raw = get("data") or {}
    data: dict[str, str] = {}
    for k, v in data_raw.items():
        if k in ("home_assistant_read", "home_assistant_write", "notifications"):
            continue
        if k not in DATA:
            raise ManifestError(f"data: {k!r} is not something World Assistant shares (it shares {', '.join(DATA)})")
        if k == "home_location":
            if v not in PRECISION:
                raise ManifestError("data.home_location: none, postcode district or exact")
            if v != "none":
                data[k] = v
        elif v is True:
            data[k] = "yes"
        elif v not in (False, None):
            raise ManifestError(f"data.{k}: true or false")
    ha_read = _list(data_raw.get("home_assistant_read"), "data.home_assistant_read", ENTITY)
    ha_write = _list(data_raw.get("home_assistant_write"), "data.home_assistant_write", ENTITY)
    for e in ha_write:
        if not e.split(".", 1)[1].startswith(f"wa_x_{mid}_"):
            raise ManifestError(f"data.home_assistant_write: {e} must be named wa_x_{mid}_… (a module writes only its own entities)")
    limits = {**DEFAULT_LIMITS}
    for k, v in (get("limits") or {}).items():
        if k not in CEILING:
            raise ManifestError(f"limits: {k!r} is not a limit ({', '.join(CEILING)})")
        if not isinstance(v, int) or not FLOOR[k] <= v <= CEILING[k]:
            raise ManifestError(f"limits.{k}: a whole number from {FLOOR[k]} to {CEILING[k]}")
        limits[k] = v
    fields = []
    for s in get("settings") or []:
        if not isinstance(s, dict) or not KEY.fullmatch(str(s.get("key", ""))):
            raise ManifestError("settings: each has a key of lower-case letters, digits and _")
        t = str(s.get("type", "text"))
        if t not in FIELD_TYPES:
            raise ManifestError(f"settings.{s['key']}: type is {', '.join(FIELD_TYPES)}")
        choices = _list(s.get("choices"), f"settings.{s['key']}.choices", most=20)
        if t == "choice" and not choices:
            raise ManifestError(f"settings.{s['key']}: a choice needs choices")
        fields.append(Field(s["key"], str(s.get("label") or s["key"])[:60], t, choices, s.get("default")))
    if len(fields) > limits["settings_fields"]:
        raise ManifestError(f"settings: {len(fields)} fields, more than its limit of {limits['settings_fields']}")
    purposes = {str(k): str(v)[:200] for k, v in (get("purposes") or {}).items()}
    if data and hosts:
        missing = [h for h in hosts if h not in purposes]
        if missing:
            raise ManifestError(f"purposes: it asks for personal data and reaches {', '.join(missing)}: say why for each host")
    controls = _list(get("controls"), "controls")
    return Manifest(id=mid, name=name, version=version, sdk=sdk, licence=licence, repository=repo, role=role, image=image,
                    min_wa=min_wa, reads=_list(get("reads"), "reads"), gives=_list(get("gives"), "gives"), controls=controls,
                    keys=keys, hosts=hosts, home_devices=devices, data=data, home_assistant_read=ha_read,
                    home_assistant_write=ha_write, notifications=bool(data_raw.get("notifications", False)), limits=limits,
                    settings=tuple(fields), purposes=purposes)  # fmt: skip


def access(m: Manifest) -> dict[str, Any]:
    """Everything a manifest asks for, as comparable sets and numbers (for the permissions screen and for spotting
    an update that asks for more)."""
    rank = {p: i for i, p in enumerate(PRECISION)}
    return {
        "data": {k: rank.get(v, 1) for k, v in m.data.items()},
        "hosts": set(m.hosts), "home_devices": set(m.home_devices), "keys": set(m.keys),
        "home_assistant_read": set(m.home_assistant_read), "home_assistant_write": set(m.home_assistant_write),
        "notifications": m.notifications, "controls": set(m.controls), "limits": dict(m.limits),
    }  # fmt: skip


def more_access(old: Manifest, new: Manifest) -> list[str]:
    """What a new version asks for that the installed one did not, in words; empty when it asks for nothing more.
    Never agreed automatically."""
    a, b = access(old), access(new)
    out = []
    for k, v in b["data"].items():
        if k not in a["data"]:
            out.append(f"{DATA[k]}" + (f" ({new.data[k]})" if k == "home_location" else ""))
        elif v > a["data"][k]:
            out.append(f"{DATA[k]} more precisely ({new.data[k]}, was {old.data[k]})")
    for k, words in (("hosts", "to reach"), ("home_devices", "a device on your network:"), ("keys", "a key:"),
                     ("home_assistant_read", "to read Home Assistant's"), ("home_assistant_write", "to write Home Assistant's"),
                     ("controls", "to control")):  # fmt: skip
        for x in sorted(b[k] - a[k]):
            out.append(f"{words} {x}")
    if b["notifications"] and not a["notifications"]:
        out.append("to send you notifications")
    for k, v in b["limits"].items():
        if v > a["limits"].get(k, 0):
            out.append(f"a higher limit: {k.replace('_', ' ')} {a['limits'].get(k, 0)} → {v}")
    return out


def from_json(d: dict[str, Any]) -> Manifest:
    """A manifest kept in the database back into its type (already checked when it was parsed)."""
    d = dict(d)
    d["settings"] = tuple(Field(**{**f, "choices": tuple(f.get("choices") or ())}) for f in d.get("settings") or ())
    for k in ("reads", "gives", "controls", "hosts", "home_devices", "home_assistant_read", "home_assistant_write"):
        d[k] = tuple(d.get(k) or ())
    return Manifest(**d)
