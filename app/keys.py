"""Saving data-source keys from the Settings page.

Keys are written to their own folder (WA_KEYS_DIR, mounted from WA_DATA_DIR/keys), never to the secrets
folder, and only the data sources' keys can be written: not the API token, passwords or vault logins.
A saved key takes effect at once for the api and the worker (both read the folder) and wins over the
secret file; deleting it falls back to the file. Values are never returned, only whether one is set.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import secretstore
from app.auth import require_token
from app.config import SECRETS_DIR

router = APIRouter(prefix="/v1/settings/keys", tags=["settings"], dependencies=[Depends(require_token)])


class KeyIn(BaseModel):
    value: str = Field(min_length=1, max_length=1024)


class VaultIn(BaseModel):
    token: str = Field(min_length=1, max_length=512, description="a vault token that can write; used once, not kept")
    remove_local: bool = Field(True, description="forget the copies saved here once the vault has them")


def writable() -> tuple[bool, str]:
    d = secretstore.keys_dir()
    if not d.is_dir():
        return False, f"{d} is not mounted: update the compose files (docker-compose.yml mounts WA_DATA_DIR/keys)"
    if not os.access(d, os.W_OK):
        return False, "the keys folder is not writable by the app: run  chown 10001:10001 <WA_DATA_DIR>/keys"
    return True, ""


@router.get("")
async def list_keys() -> dict[str, Any]:
    """Which keys are set and where each comes from (settings, file, vault, env). Never a value."""
    ok, why = writable()
    rows = []
    for name in sorted(secretstore.WEB_KEYS):
        try:
            _, source = secretstore.resolve(name, SECRETS_DIR)
        except Exception:  # noqa: BLE001 - a vault that cannot be reached: say so, not fail
            source = "unknown"
        rows.append({"name": name, "set": source not in ("missing", "default", "unknown"), "source": source})
    return {"writable": ok, "reason": why, "keys": rows, "backend": secretstore.backend_name()}


@router.post("/to-vault")
async def to_vault(body: VaultIn) -> dict[str, Any]:
    """Copy every key saved here or in a secret file into the vault, then (by default) forget the copies saved here,
    so the vault is where they come from. Each is checked by reading it back through the app's own vault login
    before its copy here is removed. The token is used for this one write and not kept."""
    if secretstore.backend_name() != "vault":
        raise HTTPException(409, "no vault is set up (WA_SECRETS_BACKEND=vault): see docs/openbao.md")
    values: dict[str, str] = {}
    for name in sorted(secretstore.WEB_KEYS):
        value, source = secretstore.resolve(name, SECRETS_DIR)
        if source in ("settings", "file", "env") and value:
            values[name] = value
    if not values:
        return {"copied": [], "removed": [], "version": None, "message": "every key set is already in the vault"}
    try:
        version = await asyncio.to_thread(secretstore.vault_merge, values, body.token.strip())
    except secretstore.SecretError as exc:
        raise HTTPException(403, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"the vault could not be written: {type(exc).__name__}") from exc
    secretstore.clear_cache()
    removed = []
    if body.remove_local:
        for name, value in values.items():
            path = secretstore.keys_dir() / f"{name}.txt"
            if not path.exists():
                continue
            path.unlink()  # the vault's copy is checked next; put back if it is not there
            secretstore.clear_cache()
            got, source = secretstore.resolve(name, SECRETS_DIR)
            if source == "vault" and got == value:
                removed.append(name)
            else:
                await save_key(name, KeyIn(value=value))
    return {"copied": sorted(values), "removed": removed, "version": version,
            "message": f"{len(values)} key(s) copied into the vault" + (f"; {len(removed)} no longer kept here" if removed else "")}  # fmt: skip


def _check(name: str) -> None:
    if name not in secretstore.WEB_KEYS:
        raise HTTPException(404, "not a key that can be set here")
    ok, why = writable()
    if not ok:
        raise HTTPException(409, why)


@router.put("/{name}")
async def save_key(name: str, body: KeyIn) -> dict[str, Any]:
    _check(name)
    value = body.value.strip()
    if not value or any(c in value for c in "\r\n\0"):
        raise HTTPException(422, "a key is one line of text")
    d = secretstore.keys_dir()
    # Write a new file and move it into place, so a reader never sees half a key.
    fd, tmp = tempfile.mkstemp(dir=d, prefix=f".{name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(value)
        os.chmod(tmp, 0o600)
        os.replace(tmp, d / f"{name}.txt")
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return {"name": name, "set": True, "source": "settings"}


@router.delete("/{name}")
async def delete_key(name: str) -> dict[str, Any]:
    """Forget the key saved here; the secret file (if any) applies again."""
    _check(name)
    path = secretstore.keys_dir() / f"{name}.txt"
    if path.exists():
        path.unlink()
    _, source = secretstore.resolve(name, SECRETS_DIR)
    return {"name": name, "set": source not in ("missing", "default"), "source": source}
