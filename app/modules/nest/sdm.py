"""Google Smart Device Management (SDM): reading a Nest thermostat.

An access token is refreshed from the stored refresh token (the one-time Google Device Access setup mints it), then the
thermostat's traits are read from the devices endpoint. Tracking only — this module never writes to the thermostat.

    POST https://www.googleapis.com/oauth2/v4/token   (grant_type=refresh_token) -> access_token (~1 hour)
    GET  https://smartdevicemanagement.googleapis.com/v1/enterprises/{project}/devices  (Bearer access_token)

The reads and trait-parsing are the useful surface; the two network calls are access_token() and devices().
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger("wa.nest")
TOKEN_URL = "https://www.googleapis.com/oauth2/v4/token"
SDM = "https://smartdevicemanagement.googleapis.com/v1"
THERMOSTAT = "sdm.devices.types.THERMOSTAT"


class NestError(Exception):
    pass


# one cached access token per process; refreshed when it is near expiry
_token: dict[str, Any] = {"value": "", "until": 0.0}


def _oauth_error(r: httpx.Response) -> str:
    try:
        body = r.json()
        err, desc = body.get("error"), body.get("error_description")
    except ValueError:
        err = desc = None
    if err == "invalid_grant":
        return ("Google rejected the refresh token (invalid_grant). If the OAuth consent screen is still in 'Testing' "
                "the token expires after 7 days — set it to 'In production' and re-authorise to get a fresh one.")
    return f"Google OAuth error: {err or r.status_code}{': ' + desc if desc else ''}"


async def access_token(client: httpx.AsyncClient, client_id: str, client_secret: str, refresh_token: str) -> str:
    if _token["value"] and time.monotonic() < _token["until"]:
        return str(_token["value"])
    r = await client.post(TOKEN_URL, params={"client_id": client_id, "client_secret": client_secret,
                                             "refresh_token": refresh_token, "grant_type": "refresh_token"}, timeout=20)  # fmt: skip
    if r.status_code >= 400:
        raise NestError(_oauth_error(r))
    body = r.json()
    tok = body.get("access_token")
    if not tok:
        raise NestError("Google did not return an access token")
    _token["value"] = tok
    _token["until"] = time.monotonic() + max(60, int(body.get("expires_in", 3599)) * 0.8)
    return str(tok)


async def devices(client: httpx.AsyncClient, token: str, project_id: str) -> list[dict[str, Any]]:
    r = await client.get(f"{SDM}/enterprises/{project_id}/devices",
                         headers={"Authorization": f"Bearer {token}"}, timeout=20)  # fmt: skip
    if r.status_code == 403:
        raise NestError("Google refused the devices list (403): check the project id, that the Smart Device Management "
                        "API is enabled, and that the account has been authorised.")
    if r.status_code >= 400:
        raise NestError(f"SDM devices list failed (HTTP {r.status_code})")
    return list(r.json().get("devices") or [])


def _num(v: Any) -> float | None:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _room(dev: dict[str, Any]) -> str | None:
    for rel in dev.get("parentRelations") or []:
        if rel.get("displayName"):
            return str(rel["displayName"])
    return None


def thermostat(dev: dict[str, Any]) -> dict[str, Any]:
    """The tracked fields pulled out of one device's traits. Pure."""
    traits = dev.get("traits") or {}

    def trait(name: str, key: str, default: Any = None) -> Any:
        return (traits.get(f"sdm.devices.traits.{name}") or {}).get(key, default)

    dev_id = (dev.get("name") or "").split("/")[-1]
    name = trait("Info", "customName") or _room(dev) or dev_id
    mode = trait("ThermostatMode", "mode")  # HEAT / COOL / HEATCOOL / OFF
    hvac = trait("ThermostatHvac", "status")  # HEATING / COOLING / OFF — whether it is calling for heat
    sp = traits.get("sdm.devices.traits.ThermostatTemperatureSetpoint") or {}
    setpoint = _num(sp.get("coolCelsius") if (mode or "").upper() == "COOL" else sp.get("heatCelsius"))
    return {
        "device": dev_id, "name": name,
        "ambient_c": _num(trait("Temperature", "ambientTemperatureCelsius")),
        "humidity": _num(trait("Humidity", "ambientHumidityPercent")),
        "setpoint_c": setpoint, "mode": mode, "hvac": hvac, "eco": trait("ThermostatEco", "mode"),
        "online": (trait("Connectivity", "status") or "ONLINE") == "ONLINE",
    }


async def read(client: httpx.AsyncClient, project_id: str, client_id: str, client_secret: str, refresh_token: str) -> list[dict[str, Any]]:
    """Every thermostat the account exposes, as tracked-field dicts."""
    token = await access_token(client, client_id, client_secret, refresh_token)
    return [thermostat(d) for d in await devices(client, token, project_id) if d.get("type") == THERMOSTAT]
