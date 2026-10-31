"""Where every API key and password comes from.

One lookup, one place to configure. Order of precedence for a secret called `name`:

0. For the data-source keys only (WEB_KEYS): a key saved from the Settings page, in WA_KEYS_DIR.
1. The configured key vault (WA_SECRETS_BACKEND), if any.
2. A file in the secrets directory: <dir>/<name> or <dir>/<name>.txt.
3. The upper-cased environment variable (local development).

Backends (WA_SECRETS_BACKEND):

* ``files`` (default): nothing extra; steps 2 and 3 only.
* ``vault``: HashiCorp Vault or OpenBao, KV version 2. All secrets live as fields of one document, so
  one request fetches everything. Needs WA_VAULT_ADDR and either a token (secret ``vault_token``) or
  AppRole (secrets ``vault_role_id`` and ``vault_secret_id``).
* ``command``: runs your own command for each secret (WA_SECRETS_COMMAND, with ``{name}`` replaced), so
  any vault with a command line tool works: 1Password ``op read``, Bitwarden ``bw get``, Azure Key
  Vault, AWS Secrets Manager, Google Secret Manager, pass, sops. The command's output is the value.

If a vault cannot be reached the last value it returned is used (up to an hour), then the files, so a
vault outage does not take the service down. Values are never logged. The vault's own sign-in
credentials are always read from files or the environment, never from the vault itself.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("wa.secrets")

NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")
STALE_SECONDS = 3600


@dataclass(frozen=True)
class Known:
    name: str
    required: bool
    used_for: str


# Every secret the application can read. `check` reports on these; it never prints values.
KNOWN: tuple[Known, ...] = (
    Known("api_token", True, "Bearer token for the API, pages, MCP and Siri endpoints"),
    Known("postgres_password", True, "Database password (the database container also reads the file)"),
    Known("nh_subscription_key", False, "National Highways primary key"),
    Known("nh_subscription_key_secondary", False, "National Highways secondary key"),
    Known("fuel_finder_client_id", False, "Fuel Finder login"),
    Known("fuel_finder_client_secret", False, "Fuel Finder login"),
    Known("tfl_app_key", False, "TfL app key (optional, raises the rate limit)"),
    Known("nr_access_token", False, "National Rail (Rail Data Marketplace) consumer key"),
    Known("ticketmaster_api_key", False, "Ticketmaster Discovery API consumer key"),
    Known("skiddle_api_key", False, "Skiddle events API key"),
    Known("octopus_api_key", False, "Octopus Energy API key (finds your tariff from the account number)"),
    Known("bods_api_key", False, "Bus Open Data Service key (live buses outside London)"),
    Known("tfwm_app_id", False, "Transport for West Midlands application ID"),
    Known("tfwm_app_key", False, "Transport for West Midlands application key"),
    Known("ukpn_api_key", False, "UK Power Networks open data portal API key (power cuts, street works)"),
    Known("gridserve_api_key", False, "GRIDSERVE open data API key (their EV chargers and whether they work)"),
    Known("pollen_api_key", False, "Polleninformation (Austrian Pollen Information Service) API key"),
    Known("redis_password", False, "Password of the stack's own Redis"),
    Known("mqtt_password", False, "MQTT broker password"),
)


class SecretError(RuntimeError):
    pass


def _file_value(name: str, directory: Path) -> str | None:
    for candidate in (directory / name, directory / f"{name}.txt"):
        try:
            return candidate.read_text().strip()
        except (FileNotFoundError, PermissionError, IsADirectoryError):
            continue
    return None


def _bootstrap(name: str, directory: Path) -> str:
    """Vault sign-in credentials: files or environment only."""
    value = _file_value(name, directory)
    return value if value is not None else os.environ.get(name.upper(), "").strip()


# ---- backends: a function (name, directory) -> value or None ------------------------------


class VaultBackend:
    """HashiCorp Vault / OpenBao KV v2: one document holding every secret as a field."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._doc: dict[str, str] = {}
        self._fetched = 0.0
        self._good = 0.0
        self._token = ""
        self._token_until = 0.0

    @staticmethod
    def _addr() -> str:
        addr = os.environ.get("WA_VAULT_ADDR", "").rstrip("/")
        if not addr:
            raise SecretError("WA_VAULT_ADDR is not set")
        return addr

    def _client(self) -> httpx.Client:
        ns = os.environ.get("WA_VAULT_NAMESPACE", "")
        verify: Any = os.environ.get("WA_VAULT_CA_FILE") or True
        return httpx.Client(
            timeout=5.0, verify=verify, headers={"X-Vault-Namespace": ns} if ns else {}
        )

    def _login(self, client: httpx.Client, directory: Path) -> str:
        if self._token and time.monotonic() < self._token_until:
            return self._token
        static = _bootstrap("vault_token", directory)
        if static:
            return static
        role, sid = _bootstrap("vault_role_id", directory), _bootstrap("vault_secret_id", directory)
        if not (role and sid):
            raise SecretError("no vault_token, or vault_role_id and vault_secret_id, provided")
        mount = os.environ.get("WA_VAULT_APPROLE_MOUNT", "approle")
        r = client.post(
            f"{self._addr()}/v1/auth/{mount}/login", json={"role_id": role, "secret_id": sid}
        )
        r.raise_for_status()
        auth = r.json()["auth"]
        self._token = auth["client_token"]
        self._token_until = time.monotonic() + max(60, int(auth.get("lease_duration", 600)) * 0.8)
        return self._token

    def _refresh(self, directory: Path, ttl: float) -> None:
        if self._doc and time.monotonic() - self._fetched < ttl:
            return
        mount = os.environ.get("WA_VAULT_MOUNT", "secret").strip("/")
        path = os.environ.get("WA_VAULT_PATH", "world-assistant").strip("/")
        try:
            with self._client() as client:
                token = self._login(client, directory)
                r = client.get(
                    f"{self._addr()}/v1/{mount}/data/{path}", headers={"X-Vault-Token": token}
                )
                r.raise_for_status()
                data = r.json()["data"]["data"]
        except (httpx.HTTPError, KeyError, ValueError, SecretError) as exc:
            if self._doc and time.monotonic() - self._good < STALE_SECONDS:
                self._fetched = time.monotonic() - ttl + 30  # retry in 30 seconds, not every call
                log.warning(
                    "vault unreachable (%s); using the last values it returned", type(exc).__name__
                )
                return
            raise SecretError(f"vault unavailable: {type(exc).__name__}") from exc
        self._doc = {str(k): str(v) for k, v in data.items()}
        self._fetched = self._good = time.monotonic()

    def get(self, name: str, directory: Path, ttl: float) -> str | None:
        with self._lock:
            self._refresh(directory, ttl)
            value = self._doc.get(name)
        return value.strip() if value is not None else None


class CommandBackend:
    """Run WA_SECRETS_COMMAND with {name} replaced; stdout is the value."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def get(self, name: str, directory: Path, ttl: float) -> str | None:
        template = os.environ.get("WA_SECRETS_COMMAND", "").strip()
        if not template:
            raise SecretError("WA_SECRETS_COMMAND is not set")
        if not NAME_RE.match(name):
            return None
        argv = [part.replace("{name}", name) for part in shlex.split(template)]
        try:
            done = subprocess.run(
                argv, capture_output=True, text=True, timeout=15, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SecretError(f"secrets command failed: {type(exc).__name__}") from exc
        if done.returncode != 0:
            return None
        return done.stdout.strip() or None


_VAULT = VaultBackend()
_COMMAND = CommandBackend()
_cache: dict[str, tuple[float, str | None]] = {}
_cache_lock = threading.Lock()


def backend_name() -> str:
    return os.environ.get("WA_SECRETS_BACKEND", "files").strip().lower() or "files"


def _cache_ttl() -> float:
    try:
        return max(0.0, float(os.environ.get("WA_SECRETS_CACHE_SECONDS", "300")))
    except ValueError:
        return 300.0


def _external(name: str, directory: Path) -> str | None:
    kind = backend_name()
    if kind == "files":
        return None
    backend: Any = {"vault": _VAULT, "command": _COMMAND}.get(kind)
    if backend is None:
        log.warning("unknown WA_SECRETS_BACKEND %r (use files, vault or command)", kind)
        return None
    ttl = _cache_ttl()
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(name)
        if hit and now - hit[0] < ttl:
            return hit[1]
    try:
        value = backend.get(name, directory, ttl)
    except SecretError as exc:
        with _cache_lock:
            hit = _cache.get(name)
        if hit and now - hit[0] < STALE_SECONDS:
            return hit[1]
        log.warning("secret backend problem for %s: %s", name, exc)
        return None
    with _cache_lock:
        _cache[name] = (now, value)
    return value


# Keys the Settings page may save: the data sources' own keys. Never the API token, a password or a vault login.
WEB_KEYS = frozenset({
    "nh_subscription_key", "nh_subscription_key_secondary", "fuel_finder_client_id", "fuel_finder_client_secret",
    "tfl_app_key", "nr_access_token", "ticketmaster_api_key", "skiddle_api_key", "octopus_api_key", "bods_api_key",
    "tfwm_app_id", "tfwm_app_key", "pollen_api_key", "ukpn_api_key",
    "gridserve_api_key",
})  # fmt: skip


def keys_dir() -> Path:
    """Where the Settings page keeps the keys saved from the browser (WA_DATA_DIR/keys on the host)."""
    return Path(os.environ.get("WA_KEYS_DIR", "/keys"))


# Saved from the pages too, but not listed with the keys: the MQTT broker's password (app/mqtt_config.py) and
# the password for Home Assistant's config share (app/ha_smb.py) and its long-lived token (app/ha_discover.py).
SETTINGS_SECRETS = WEB_KEYS | {"mqtt_password", "ha_smb_password", "ha_token"}


def web_value(name: str) -> str | None:
    if name not in SETTINGS_SECRETS:
        return None
    value = _file_value(name, keys_dir())
    return value or None


def resolve(name: str, directory: Path, default: str = "") -> tuple[str, str]:
    """Return (value, source) where source is settings, vault, command, file, env or default."""
    saved = web_value(name)
    if saved:
        return saved, "settings"
    if name not in ("vault_token", "vault_role_id", "vault_secret_id"):
        value = _external(name, directory)
        if value:
            return value, backend_name()
    value = _file_value(name, directory)
    if value is not None and value != "":
        return value, "file"
    env = os.environ.get(name.upper(), "").strip()
    if env:
        return env, "env"
    return default.strip(), "default" if default else "missing"


def read_secret(name: str, directory: Path, default: str = "") -> str:
    return resolve(name, directory, default)[0]


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
    _VAULT._doc = {}
    _VAULT._fetched = 0.0


LOCAL_ONLY = frozenset({"postgres_password", "vault_token", "vault_role_id", "vault_secret_id"})


def local_values(directory: Path) -> dict[str, str]:
    """Every known secret that has a value in a file or the environment, for moving into a vault.

    Never asks a vault. Leaves out the database password (the database container reads its own file)
    and the vault's own sign-in credentials."""
    out: dict[str, str] = {}
    for k in KNOWN:
        if k.name in LOCAL_ONLY:
            continue
        value = _file_value(k.name, directory) or os.environ.get(k.name.upper(), "").strip()
        if value:
            out[k.name] = value
    return out


def report(directory: Path) -> list[dict[str, Any]]:
    """Where each known secret comes from. Never includes a value."""
    out = []
    for k in KNOWN:
        _, source = resolve(k.name, directory)
        out.append(
            {"name": k.name, "required": k.required, "source": source,
             "ok": source != "missing" or not k.required, "used_for": k.used_for}
        )  # fmt: skip
    return out

