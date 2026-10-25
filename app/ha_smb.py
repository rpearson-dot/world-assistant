"""Copying the Home Assistant files straight into its config folder over SMB (the Samba share add-on).

From the Home Assistant page: give the share (for example \\\\homeassistant.local\\config), a user and a password
once, then Copy writes the ticked packages to packages/, the dashboards to world_assistant/dashboards/ and,
if asked, adds the missing wa_* lines to secrets.yaml. Files that have not changed are left alone, nothing
else in the share is touched, and secrets.yaml is backed up before a line is added (existing lines never change).

The share and user are kept in the database; the password in the keys folder (never shown again).
"""

from __future__ import annotations

import asyncio
import errno
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app import secretstore
from app.auth import require_token
from app.config import SECRETS_DIR, get_settings
from app.version import VERSION

KEY, LAST, SECRET = "ha_smb", "ha_smb_last", "ha_smb_password"
PACKAGES_DIR, DASHBOARDS_DIR = "packages", "world_assistant/dashboards"
SHARE_RE = re.compile(r"^(?:smb:)?[\\/]{2}([A-Za-z0-9.\-\[\]:]+)[\\/]+([^\\/]+)((?:[\\/][^\\/]+)*)[\\/]*$")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

router = APIRouter(prefix="/v1/ha/smb", tags=["home assistant"], dependencies=[Depends(require_token)])


class SmbError(RuntimeError):
    """Could not reach or write the share; the message says why in words."""


@dataclass(frozen=True)
class Share:
    server: str
    share: str
    path: str  # inside the share, "" for its top
    port: int = 445

    def unc(self, *parts: str) -> str:
        bits = [p for p in (self.path, *parts) if p]
        rest = "\\".join(b.replace("/", "\\").strip("\\") for b in bits)
        return f"\\\\{self.server}\\{self.share}" + (f"\\{rest}" if rest else "")


def parse_share(address: str) -> Share:
    """\\\\host\\share, //host/share or smb://host/share, optionally with a folder after the share."""
    m = SHARE_RE.match(address.strip())
    if not m:
        raise ValueError(r"write the share like \\homeassistant.local\config or //192.168.1.20/config")
    folder = m.group(3).replace("\\", "/").strip("/")
    if ".." in folder.split("/"):
        raise ValueError("the folder may not contain ..")
    host, port = m.group(1), 445
    if host.count(":") == 1:  # host:port (an IPv6 address has more colons and no port here)
        host, raw = host.split(":")
        if not raw.isdigit() or not 0 < int(raw) < 65536:
            raise ValueError("the port after the server name must be a number")
        port = int(raw)
    return Share(host.strip("[]"), m.group(2), folder, port)


# --- what goes where -----------------------------------------------------------------------------------


def secret_lines(names: set[str], base_url: str, token: str, paths: dict[str, str]) -> dict[str, str]:
    """secrets.yaml lines (name -> line) for the secrets the chosen packages use."""
    out = {}
    for n in sorted(names):
        if n == "wa_authorization":
            out[n] = f'{n}: "Bearer {token}"'
        elif n in paths:
            out[n] = f'{n}: "{base_url}{paths[n]}"'
    return out


EXISTING = re.compile(r"^([A-Za-z0-9_]+)\s*:", re.MULTILINE)


def differing(current: str, wanted: dict[str, str]) -> list[str]:
    """Names already in secrets.yaml with a different value: left alone, but worth a look."""
    lines = {m.group(1): m.group(0).strip() for m in re.finditer(r"^([A-Za-z0-9_]+)\s*:.*$", current, re.MULTILINE)}
    return [n for n, line in wanted.items() if n in lines and lines[n].replace("'", '"') != line]


def merge_secrets(current: str, wanted: dict[str, str]) -> tuple[str, list[str]]:
    """Append the lines whose names are missing. Existing lines are never changed. (new text, names added)"""
    have = set(EXISTING.findall(current))
    add = [name for name in wanted if name not in have]
    if not add:
        return current, []
    block = "\n# Added by World Assistant " + datetime.now(UTC).strftime("%Y-%m-%d") + "\n" + "\n".join(wanted[n] for n in add) + "\n"
    return (current if current.endswith("\n") or not current else current + "\n") + block, add


# --- talking SMB (blocking: run in a thread) -----------------------------------------------------------


def _session(share: Share, user: str, password: str, cache: dict[str, Any]) -> None:
    import smbclient

    try:
        smbclient.register_session(share.server, username=user or None, password=password or None, port=share.port,
                                   connection_timeout=10, connection_cache=cache)  # fmt: skip
    except Exception as exc:
        raise SmbError(_words(exc, share)) from exc


def _words(exc: BaseException, share: Share) -> str:
    low = f"{type(exc).__name__}: {exc}".lower()
    if "logon_failure" in low or "logon failure" in low or "access_denied" in low or "status_access_denied" in low:
        return "the share refused the user name or password (in the Samba share add-on, they are its own username and password)"
    if "bad_network_name" in low or "0xc0000225" in low or "0xc00000cc" in low:
        return f"there is no share called {share.share} on {share.server} (the Samba add-on's is called config)"
    if "timed out" in low or "timeout" in low:
        return f"no answer from {share.server} on port {share.port}: is the Samba share add-on running?"
    if "refused" in low:
        return f"{share.server} refused the connection on port {share.port}: is the Samba share add-on running?"
    if "name or service not known" in low or "getaddrinfo" in low or "nodename nor servname" in low:
        return f"{share.server} could not be found: use Home Assistant's IP address"
    return f"{type(exc).__name__}: {exc}"[:240]


def _close(cache: dict[str, Any]) -> None:
    """Drop the connection; a server that objects to the goodbye must not hide what happened before it."""
    import smbclient

    try:
        smbclient.reset_connection_cache(connection_cache=cache)
    except Exception:  # noqa: BLE001, S110 - the work is done (or has already failed) by now
        pass


def _read(path: str, cache: dict[str, Any], port: int = 445) -> str | None:
    import smbclient

    try:
        with smbclient.open_file(path, mode="r", encoding="utf-8", newline="", connection_cache=cache, port=port) as f:
            return f.read()
    except OSError as exc:  # smbclient's "not found" is an OSError with errno ENOENT, not FileNotFoundError
        if exc.errno == errno.ENOENT or "0xc0000034" in str(exc) or "0xc000003a" in str(exc):
            return None
        raise


def _write(path: str, body: str, cache: dict[str, Any], port: int = 445) -> None:
    import smbclient

    with smbclient.open_file(path, mode="w", encoding="utf-8", newline="", connection_cache=cache, port=port) as f:
        f.write(body)


def check_share(share: Share, user: str, password: str) -> dict[str, Any]:
    import smbclient

    cache: dict[str, Any] = {}
    try:
        _session(share, user, password, cache)
        try:
            names = set(smbclient.listdir(share.unc(), connection_cache=cache, port=share.port))
        except Exception as exc:
            raise SmbError(_words(exc, share)) from exc
        return {"ok": True, "error": "", "configuration_yaml": "configuration.yaml" in names,
                "packages_folder": "packages" in names, "secrets_yaml": "secrets.yaml" in names}  # fmt: skip
    except SmbError as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        _close(cache)


def copy_files(share: Share, user: str, password: str, files: dict[str, str], secrets: dict[str, str] | None) -> dict[str, Any]:
    """Write files ({path in the share: text}); add missing secrets lines. Returns what happened to each."""
    import smbclient

    cache: dict[str, Any] = {}
    done: list[dict[str, str]] = []
    try:
        _session(share, user, password, cache)
        for folder in sorted({p.rsplit("/", 1)[0] for p in files if "/" in p}):
            smbclient.makedirs(share.unc(folder), exist_ok=True, connection_cache=cache, port=share.port)
        for rel, body in sorted(files.items()):
            target = share.unc(rel)
            if _read(target, cache, share.port) == body:
                done.append({"path": rel, "result": "unchanged"})
                continue
            _write(target, body, cache, share.port)
            done.append({"path": rel, "result": "written"})
        added: list[str] = []
        differ: list[str] = []
        if secrets:
            path = share.unc("secrets.yaml")
            current = _read(path, cache, share.port) or ""
            new, added = merge_secrets(current, secrets)
            differ = differing(current, secrets)
            if added:
                if current:
                    _write(share.unc("secrets.yaml.world-assistant.bak"), current, cache, share.port)
                _write(path, new, cache, share.port)
            done.append({"path": "secrets.yaml", "result": f"added {', '.join(added)}" if added else "unchanged"})
        return {"ok": True, "error": "", "files": done, "secrets_added": added, "secrets_differ": differ}
    except SmbError as exc:
        return {"ok": False, "error": str(exc), "files": done}
    except Exception as exc:  # noqa: BLE001 - a write refused half way: say what was done and what failed
        return {"ok": False, "error": _words(exc, share), "files": done}
    finally:
        _close(cache)


# --- settings and the API ------------------------------------------------------------------------------


async def _get(engine: Any, key: str) -> dict[str, Any] | None:
    async with engine.connect() as conn:
        row = (await conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": key})).first()
    return None if row is None else (row[0] if isinstance(row[0], dict) else json.loads(row[0]))


async def _put(engine: Any, key: str, value: dict[str, Any]) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"), {"k": key, "v": json.dumps(value)})  # fmt: skip


def _save_password(value: str) -> None:
    d = secretstore.keys_dir()
    if not d.is_dir() or not os.access(d, os.W_OK):
        raise HTTPException(409, "the password cannot be saved: the keys folder is missing or not writable (run scripts/deploy.sh once)")
    fd, tmp = tempfile.mkstemp(dir=d, prefix=f".{SECRET}.")
    with os.fdopen(fd, "w") as f:
        f.write(value)
    os.chmod(tmp, 0o600)
    os.replace(tmp, d / f"{SECRET}.txt")


class SmbIn(BaseModel):
    address: str = Field(..., min_length=5, max_length=300)
    user: str = Field("", max_length=128)
    password: str | None = Field(None, max_length=256)  # empty: keep the saved one


class CopyIn(BaseModel):
    packages: list[str] = Field(default_factory=list, max_length=20)
    dashboards: bool = True
    secrets: bool = False
    base_url: str = Field("", max_length=200)


async def _conf(request: Request) -> tuple[Share, str, str]:
    saved = await _get(request.app.state.engine, KEY)
    if not saved:
        raise HTTPException(409, "set the Home Assistant share first")
    return parse_share(saved["address"]), saved.get("user", ""), secretstore.read_secret(SECRET, SECRETS_DIR)


@router.get("")
async def get_smb(request: Request) -> dict[str, Any]:
    """The share in use (never the password) and the last copy."""
    saved = await _get(request.app.state.engine, KEY) or {}
    _, src = secretstore.resolve(SECRET, SECRETS_DIR)
    return {"address": saved.get("address", ""), "user": saved.get("user", ""),
            "password_set": src not in ("missing", "default"), "last": await _get(request.app.state.engine, LAST)}  # fmt: skip


@router.put("")
async def put_smb(body: SmbIn, request: Request) -> dict[str, Any]:
    try:
        parse_share(body.address)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.password:
        if any(c in body.password for c in "\r\n\0"):
            raise HTTPException(422, "a password is one line of text")
        _save_password(body.password)
    await _put(request.app.state.engine, KEY, {"address": body.address.strip(), "user": body.user.strip()})
    return await get_smb(request)


@router.delete("")
async def forget_smb(request: Request) -> dict[str, Any]:
    async with request.app.state.engine.begin() as conn:
        await conn.execute(text("DELETE FROM app_setting WHERE key IN (:a, :b)"), {"a": KEY, "b": LAST})
    path = secretstore.keys_dir() / f"{SECRET}.txt"
    if path.exists():
        path.unlink()
    return await get_smb(request)


@router.post("/test")
async def test_smb(body: SmbIn) -> dict[str, Any]:
    """Connect to the share with these details (or the saved password) and look at what is there."""
    try:
        share = parse_share(body.address)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    pw = body.password or secretstore.read_secret(SECRET, SECRETS_DIR)
    return await asyncio.to_thread(check_share, share, body.user.strip(), pw)


@router.post("/copy")
async def copy(body: CopyIn, request: Request) -> dict[str, Any]:
    """Copy this version's files into Home Assistant's config folder."""
    from app.ha_setup import HA_DIR, SECRET_PATHS, files, render, saved_names

    share, user, pw = await _conf(request)
    known = {f["path"]: f for f in files()}
    chosen = [p for p in body.packages if p in known and known[p]["kind"] == "package"]
    if len(chosen) != len(body.packages):
        raise HTTPException(422, "only this version's packages can be copied")
    out: dict[str, str] = {}
    names = await saved_names(request.app.state.engine)
    for p in chosen:
        out[f"{PACKAGES_DIR}/{known[p]['name']}"] = render((HA_DIR / p).read_text(), names)
    if body.dashboards:
        for p, f in known.items():
            if f["kind"] == "dashboard":
                out[f"{DASHBOARDS_DIR}/{f['name']}"] = render((HA_DIR / p).read_text(), names)
    secrets = None
    if body.secrets:
        base = body.base_url.rstrip("/")
        if not re.match(r"^https?://[A-Za-z0-9.\-\[\]:]+(/[A-Za-z0-9._~/-]*)?$", base):
            raise HTTPException(422, "the address Home Assistant uses for this server must look like http://192.168.1.10:8010")
        names = {s for p in chosen for s in known[p]["secrets"]}
        secrets = secret_lines(names, base, get_settings().api_token, SECRET_PATHS)
    result = await asyncio.to_thread(copy_files, share, user, pw, out, secrets)
    if result["ok"]:
        await _put(request.app.state.engine, LAST, {"version": VERSION, "at": datetime.now(UTC).isoformat(),
                                                    "files": [f["path"] for f in result["files"]]})  # fmt: skip
    return result
