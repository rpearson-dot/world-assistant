"""MELCloud: reading a Mitsubishi Ecodan air-to-water (ATW) heat pump.

Logging in with the account email and password returns a context key; the device list then carries each unit's full
state. Tracking only here — this module never writes to the heat pump. MELCloud throttles hard, so the caller polls no
more than every 10 minutes.

    POST /Mitsubishi.Wifi.Client/Login/ClientLogin  -> LoginData.ContextKey
    GET  /Mitsubishi.Wifi.Client/User/ListDevices   (header X-MitsContextKey) -> buildings, each with its devices

Field names follow the MELCloud app's JSON (as pymelcloud and Home Assistant's melcloud integration use them). The
parser is tolerant: anything missing simply reads as unavailable, to be refined once a real unit has reported.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger("wa.ecodan")
BASE = "https://app.melcloud.com/Mitsubishi.Wifi.Client"
ATW = 1  # DeviceType for air-to-water (the Ecodan); 0 is air-to-air


class MelcloudError(Exception):
    pass


# one cached context key per process; MELCloud keys are long-lived, re-login hourly to be safe
_key: dict[str, Any] = {"value": "", "until": 0.0}


async def context_key(client: httpx.AsyncClient, email: str, password: str) -> str:
    if _key["value"] and time.monotonic() < _key["until"]:
        return str(_key["value"])
    r = await client.post(f"{BASE}/Login/ClientLogin", json={
        "Email": email, "Password": password, "Language": 0, "AppVersion": "1.23.4.0",
        "Persist": True, "CaptchaResponse": None}, timeout=25)  # fmt: skip
    if r.status_code >= 400:
        raise MelcloudError(f"MELCloud login failed (HTTP {r.status_code})")
    data = r.json()
    if data.get("ErrorId"):
        raise MelcloudError("MELCloud rejected the login — check the email and password on the Keys page")
    key = (data.get("LoginData") or {}).get("ContextKey")
    if not key:
        raise MelcloudError("MELCloud did not return a context key")
    _key["value"] = key
    _key["until"] = time.monotonic() + 3600
    return str(key)


def _devices_in(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Every device under a building Structure (which nests devices under areas and floors)."""
    out = list(node.get("Devices") or [])
    for area in node.get("Areas") or []:
        out += list(area.get("Devices") or [])
    for floor in node.get("Floors") or []:
        out += list(floor.get("Devices") or [])
        for area in floor.get("Areas") or []:
            out += list(area.get("Devices") or [])
    return out


async def devices(client: httpx.AsyncClient, key: str) -> list[dict[str, Any]]:
    r = await client.get(f"{BASE}/User/ListDevices", headers={"X-MitsContextKey": key}, timeout=25)
    if r.status_code == 401:
        _key["value"] = ""  # expired: force a fresh login next time
        raise MelcloudError("MELCloud session expired")
    if r.status_code >= 400:
        raise MelcloudError(f"MELCloud device list failed (HTTP {r.status_code})")
    out: list[dict[str, Any]] = []
    for building in r.json() or []:
        out += _devices_in(building.get("Structure") or {})
    return out


def _num(v: Any) -> float | None:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def heatpump(dev: dict[str, Any]) -> dict[str, Any]:
    """The tracked fields from one ATW device. Pure."""
    d = dev.get("Device") or {}
    return {
        "device": str(dev.get("DeviceID")),
        "name": dev.get("DeviceName") or "Ecodan",
        "tank_c": _num(d.get("TankWaterTemperature")),
        "tank_target_c": _num(d.get("SetTankWaterTemperature")),
        "forced_hot_water": bool(d.get("ForcedHotWaterMode")),
        "flow_c": _num(d.get("FlowTemperature")),
        "return_c": _num(d.get("ReturnTemperature")),
        "outdoor_c": _num(d.get("OutdoorTemperature")),
        "zone1_room_c": _num(d.get("RoomTemperatureZone1")),
        "zone1_set_c": _num(d.get("SetTemperatureZone1")),
        "power": bool(d.get("Power")),
        "operation_mode": d.get("OperationMode"),
        "heating_in_kwh": _num(d.get("DailyHeatingEnergyConsumed")),
        "water_in_kwh": _num(d.get("DailyHotWaterEnergyConsumed")),
        "heating_out_kwh": _num(d.get("DailyHeatingEnergyProduced")),
        "water_out_kwh": _num(d.get("DailyHotWaterEnergyProduced")),
        "offline": bool(d.get("Offline")),
    }


async def read(client: httpx.AsyncClient, email: str, password: str) -> list[dict[str, Any]]:
    """Every Ecodan (ATW) the account has, as tracked-field dicts."""
    key = await context_key(client, email, password)
    return [heatpump(dev) for dev in await devices(client, key) if (dev.get("Device") or {}).get("DeviceType") == ATW]
