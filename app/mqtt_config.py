"""The MQTT broker World Assistant publishes to: shown and changed on the Settings page.

Settings saved there (server, port, user, encrypted or not) are kept in the database and win over .env; the
password goes to the keys folder like the data-source keys (never shown again). "Use .env" forgets both.
The worker reads them on every publish, so a change applies within a few minutes without a restart.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import ssl
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import aiomqtt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app import secretstore
from app.auth import require_token
from app.config import SECRETS_DIR, Settings, get_settings

KEY = "mqtt"
BUNDLED = "wa-mqtt"
HOST_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$|^\[?[0-9A-Fa-f:.]+\]?$")

router = APIRouter(prefix="/v1/settings/mqtt", tags=["settings"], dependencies=[Depends(require_token)])


@dataclass(frozen=True)
class MqttConf:
    host: str
    port: int
    user: str
    tls: bool
    ca: str = ""
    enabled: bool = True

    @property
    def active(self) -> bool:
        return self.enabled and bool(self.host.strip())

    @property
    def bundled(self) -> bool:
        return self.host == BUNDLED


def from_env(s: Settings | None = None) -> MqttConf:
    s = s or get_settings()
    ca = s.mqtt_tls_ca
    if not ca:  # the CA scripts/mqtt-ca.sh made, when .env does not name one
        made = secretstore.keys_dir() / "mqtt-ca.pem"
        ca = str(made) if made.is_file() else ""
    return MqttConf(s.mqtt_host.strip(), s.mqtt_port, s.mqtt_user, s.mqtt_tls, ca, s.mqtt_enabled)


def merge(base: MqttConf, saved: dict[str, Any] | None) -> MqttConf:
    if not saved:
        return base
    return replace(base, host=str(saved.get("host", base.host)).strip(), port=int(saved.get("port", base.port)),
                   user=str(saved.get("user", base.user)), tls=bool(saved.get("tls", base.tls)))  # fmt: skip


async def _saved(engine: Any) -> tuple[dict[str, Any] | None, str | None]:
    async with engine.connect() as conn:
        row = (await conn.execute(text("SELECT value, updated_at FROM app_setting WHERE key = :k"), {"k": KEY})).first()
    if row is None:
        return None, None
    value = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    return value, row[1].isoformat()


async def load(engine: Any) -> tuple[MqttConf, str]:
    """The broker to use, and where that came from ("settings" or "env"). .env if the database cannot say."""
    base = from_env()
    try:
        saved, _ = await _saved(engine)
    except Exception:  # noqa: BLE001 - no table yet, database down: publishing must not stop
        return base, "env"
    return merge(base, saved), ("settings" if saved else "env")


def tls_context(enabled: bool, ca_file: str = "") -> ssl.SSLContext | None:
    """TLS for the broker connection: verified against the system's trusted roots, or your own CA file."""
    if not enabled:
        return None
    ctx = ssl.create_default_context()  # the public roots (Let's Encrypt and the like) ...
    if ca_file:
        ctx.load_verify_locations(cafile=ca_file)  # ... and your own CA as well, not instead
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def password() -> str:
    return get_settings().mqtt_password


def client(conf: MqttConf, secret: str | None = None, timeout: float = 8) -> aiomqtt.Client:
    pw = password() if secret is None else secret
    return aiomqtt.Client(hostname=conf.host, port=conf.port, username=conf.user or None, password=pw or None,
                          timeout=timeout, tls_context=tls_context(conf.tls, conf.ca))  # fmt: skip


def explain(exc: BaseException, conf: MqttConf) -> str:
    """A connection failure in words someone setting this up can act on."""
    text_ = f"{type(exc).__name__}: {exc}"
    low = text_.lower()
    if isinstance(exc, ssl.SSLCertVerificationError) or "certificate verify failed" in low:
        if "hostname" in low or "ip address mismatch" in low:
            return f"the certificate is not for {conf.host}: use the name it was issued for. ({text_[:160]})"
        return f"the certificate is not trusted (self-signed or a private CA? set WA_MQTT_TLS_CA). ({text_[:160]})"
    if isinstance(exc, ssl.SSLError) or "wrong version number" in low or "unexpected eof" in low:
        return f"TLS failed: is port {conf.port} the broker's TLS port? (8883 usually; 1883 is unencrypted) ({text_[:120]})"
    if "not authori" in low or "bad user name or password" in low or "code:5" in low or "code:4" in low:
        return "the broker refused the user name or password"
    if "name or service not known" in low or "nodename nor servname" in low or "temporary failure in name" in low:
        return f"{conf.host} could not be found: check the name, or use the broker's IP"
    if "refused" in low:
        return f"nothing is listening on {conf.host}:{conf.port}"
    if "timed out" in low or isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return f"no answer from {conf.host}:{conf.port} (a firewall, or the wrong address)"
    return text_[:200]


async def test_connection(conf: MqttConf, secret: str | None = None) -> dict[str, Any]:
    if not conf.host:
        return {"ok": False, "error": "no server given"}
    try:
        async with asyncio.timeout(10):
            async with client(conf, secret, timeout=6):
                pass
    except (aiomqtt.MqttError, OSError, ssl.SSLError, TimeoutError) as exc:
        return {"ok": False, "error": explain(exc, conf)}
    return {"ok": True, "error": ""}


# --- the API ----------------------------------------------------------------------------------------


class MqttIn(BaseModel):
    host: str = Field("", max_length=255)
    port: int = Field(1883, ge=1, le=65535)
    user: str = Field("", max_length=128)
    tls: bool = False
    password: str | None = Field(None, max_length=512)  # None or empty: keep the one there is


def _check(body: MqttIn) -> MqttConf:
    host = body.host.strip()
    if host and not HOST_RE.match(host):
        raise HTTPException(422, "the server must be a host name or an IP address, without a scheme or port")
    if body.password and any(c in body.password for c in "\r\n\0"):
        raise HTTPException(422, "a password is one line of text")
    return replace(from_env(), host=host, port=body.port, user=body.user.strip(), tls=body.tls)


def _save_password(value: str) -> None:
    d = secretstore.keys_dir()
    if not d.is_dir() or not os.access(d, os.W_OK):
        raise HTTPException(409, "the password cannot be saved here: the keys folder is missing or not writable "
                                 "(run scripts/deploy.sh once, or put it in mqtt_password.txt)")  # fmt: skip
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".mqtt_password.")
    with os.fdopen(fd, "w") as f:
        f.write(value)
    os.chmod(tmp, 0o600)
    os.replace(tmp, d / "mqtt_password.txt")


async def describe(request: Request) -> dict[str, Any]:
    engine = request.app.state.engine
    conf, source = await load(engine)
    _, at = (None, None) if source == "env" else await _saved(engine)
    try:
        raw = await request.app.state.redis.get("wa:ha:last")
        last = json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 - the page still shows the settings
        last = None
    _, pw_source = secretstore.resolve("mqtt_password", SECRETS_DIR)
    return {"host": conf.host, "port": conf.port, "user": conf.user, "tls": conf.tls, "ca": bool(conf.ca),
            "active": conf.active, "bundled": conf.bundled, "source": source, "changed_at": at,
            "password_set": pw_source not in ("missing", "default"), "password_source": pw_source,
            "env": {"host": from_env().host, "port": from_env().port, "tls": from_env().tls},
            "last_publish": last, "ca_available": ca_file() is not None}  # fmt: skip


def ca_file() -> Path | None:
    """The CA certificate clients should trust: WA_MQTT_TLS_CA, or the one scripts/mqtt-ca.sh left in the keys folder."""
    for candidate in (from_env().ca, str(secretstore.keys_dir() / "mqtt-ca.pem")):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


@router.get("/ca", response_class=PlainTextResponse)
async def get_ca() -> PlainTextResponse:
    """The CA certificate (public, not a secret), for the OVMS module and Home Assistant to trust."""
    path = ca_file()
    if path is None:
        raise HTTPException(404, "no CA certificate here: run scripts/mqtt-ca.sh, or set WA_MQTT_TLS_CA")
    body = path.read_text()
    if "BEGIN CERTIFICATE" not in body or "PRIVATE KEY" in body:
        raise HTTPException(404, "that file is not a certificate")
    return PlainTextResponse(body, media_type="application/x-pem-file",
                             headers={"Content-Disposition": 'attachment; filename="world-assistant-mqtt-ca.pem"'})  # fmt: skip


@router.get("")
async def get_mqtt(request: Request) -> dict[str, Any]:
    """The broker in use, where the settings came from, and how the last publish went. Never the password."""
    return await describe(request)


@router.put("")
async def put_mqtt(body: MqttIn, request: Request) -> dict[str, Any]:
    """Save the broker settings (they win over .env). An empty server turns publishing off."""
    conf = _check(body)
    if body.password:
        _save_password(body.password)
    async with request.app.state.engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
            ),
            {"k": KEY, "v": json.dumps({"host": conf.host, "port": conf.port, "user": conf.user, "tls": conf.tls})},
        )
    return await describe(request)


@router.delete("")
async def reset_mqtt(request: Request) -> dict[str, Any]:
    """Forget the settings saved here (and the password saved here): .env applies again."""
    async with request.app.state.engine.begin() as conn:
        await conn.execute(text("DELETE FROM app_setting WHERE key = :k"), {"k": KEY})
    path = secretstore.keys_dir() / "mqtt_password.txt"
    if path.exists():
        path.unlink()
    return await describe(request)


@router.post("/test")
async def test_mqtt(body: MqttIn) -> dict[str, Any]:
    """Try to connect with these settings (and this password, or the saved one), without saving anything."""
    return await test_connection(_check(body), body.password or None)
