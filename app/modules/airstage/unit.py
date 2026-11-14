"""A Fujitsu Airstage indoor unit's own API on the home network (no cloud): POST /GetParam and /SetParam on its
address, naming the unit by its device id. Worked out from pyairstage (MIT licence, github.com/danielkaldheim/pyairstage),
which Home Assistant's Airstage integration uses.

The pure parts (reading values, checking a change) are separate from the network, so they are tested on their own.
Values are text: on/off "1"/"0"; temperatures tenths of a degree (target) or (x - 5000) / 100 (indoor, outdoor);
"65535" means the unit does not have that feature.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

NOT_AVAILABLE = "65535"
MODES = {"0": "auto", "1": "cool", "2": "dry", "3": "fan_only", "4": "heat"}
MODE_CODES = {v: k for k, v in MODES.items()}
FANS = {"0": "auto", "2": "quiet", "5": "low", "7": "medium_low", "8": "medium", "9": "medium_high", "11": "high"}
FAN_CODES = {v: k for k, v in FANS.items()}
# what is read: no more than 20 names in one request (the unit's limit), with a pause between requests
READ = (["iu_model", "iu_wifi_led", "iu_indoor_tmp", "iu_outdoor_tmp", "iu_pow_cons", "iu_err_code", "iu_demand",
         "iu_fltr_sign_reset"],
        ["iu_onoff", "iu_op_mode", "iu_fan_spd", "iu_set_tmp", "iu_af_swg_vrt", "iu_af_dir_vrt", "iu_af_swg_hrz",
         "iu_economy", "iu_powerful", "ou_low_noise", "iu_fan_ctrl", "iu_min_heat", "iu_hmn_det"])  # fmt: skip
LIMITS = {"heat": (16.0, 30.0), "cool": (18.0, 30.0), "dry": (18.0, 30.0), "auto": (18.0, 30.0)}


class UnitError(Exception):
    pass


def _v(raw: dict[str, Any], name: str) -> str | None:
    v = raw.get(name)
    return None if v in (None, "", NOT_AVAILABLE) else str(v)


def _flag(raw: dict[str, Any], name: str) -> bool | None:
    v = _v(raw, name)
    return None if v is None else v == "1"


def state(raw: dict[str, Any]) -> dict[str, Any]:
    """A unit's values as World Assistant shows them."""

    def temp(name: str, f: Any) -> float | None:
        v = _v(raw, name)
        try:
            return round(f(int(v)), 1) if v is not None else None
        except ValueError:
            return None

    power = _flag(raw, "iu_onoff")
    err = _v(raw, "iu_err_code")
    return {
        "power": power, "mode": MODES.get(_v(raw, "iu_op_mode") or ""), "fan": FANS.get(_v(raw, "iu_fan_spd") or ""),
        "target_c": temp("iu_set_tmp", lambda x: x / 10), "indoor_c": temp("iu_indoor_tmp", lambda x: (x - 5000) / 100),
        "outdoor_c": temp("iu_outdoor_tmp", lambda x: (x - 5000) / 100),
        "energy": float(_v(raw, "iu_pow_cons")) if (_v(raw, "iu_pow_cons") or "").isdigit() else None,
        "swing_vertical": _flag(raw, "iu_af_swg_vrt"), "swing_horizontal": _flag(raw, "iu_af_swg_hrz"),
        "economy": _flag(raw, "iu_economy"), "powerful": _flag(raw, "iu_powerful"), "low_noise": _flag(raw, "ou_low_noise"),
        "minimum_heat": _flag(raw, "iu_min_heat"), "filter_due": _flag(raw, "iu_fltr_sign_reset"),
        "error_code": err if err not in (None, "0", "00") else None, "model": _v(raw, "iu_model"),
    }  # fmt: skip


def changes(want: dict[str, Any], current: dict[str, Any]) -> list[tuple[str, str]]:
    """The parameters to set for a change (power, mode, target_c, fan, swing_vertical, swing_horizontal, economy,
    powerful, low_noise, minimum_heat). Raises ValueError for one the unit would refuse."""
    out: list[tuple[str, str]] = []
    if "power" in want:
        out.append(("iu_onoff", "1" if want["power"] else "0"))
    mode = want.get("mode", current.get("mode"))
    if "mode" in want:
        if want["mode"] not in MODE_CODES:
            raise ValueError("mode is one of " + ", ".join(MODE_CODES))
        out.append(("iu_op_mode", MODE_CODES[want["mode"]]))
    if "target_c" in want:
        if mode == "fan_only":
            raise ValueError("no temperature in fan mode")
        lo, hi = LIMITS.get(mode or "auto", (16.0, 30.0))
        t = round(float(want["target_c"]) * 2) / 2
        if not lo <= t <= hi:
            raise ValueError(f"the temperature in {mode or 'this'} mode is {lo:g} to {hi:g} °C")
        out.append(("iu_set_tmp", str(int(t * 10))))
    if "fan" in want:
        if want["fan"] not in FAN_CODES:
            raise ValueError("fan is one of " + ", ".join(FAN_CODES))
        out.append(("iu_fan_spd", FAN_CODES[want["fan"]]))
    for key, name in (("swing_vertical", "iu_af_swg_vrt"), ("swing_horizontal", "iu_af_swg_hrz"), ("economy", "iu_economy"),
                      ("powerful", "iu_powerful"), ("low_noise", "ou_low_noise"), ("minimum_heat", "iu_min_heat")):  # fmt: skip
        if key in want:
            out.append((name, "1" if want[key] else "0"))
    return out


async def _post(client: httpx.AsyncClient, ip: str, path: str, body: dict[str, Any], tries: int = 3) -> dict[str, Any]:
    last = ""
    for n in range(tries):
        try:
            r = await client.post(f"http://{ip}/{path}", content=json.dumps(body), timeout=8)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            last = f"{type(exc).__name__}: {exc}"[:200]
            if n + 1 < tries:
                await asyncio.sleep(1)
    raise UnitError(f"no answer from {ip} after {tries} tries ({last})")


async def read(client: httpx.AsyncClient, ip: str, device_id: str) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    for i, names in enumerate(READ):
        if i:
            await asyncio.sleep(1)  # back-to-back requests are dropped
        body = {"device_id": device_id, "device_sub_id": 0, "req_id": "", "modified_by": "", "set_level": "03", "list": names}
        r = await _post(client, ip, "GetParam", body)
        if r.get("result") != "OK":
            raise UnitError(f"the unit refused to answer ({r.get('error') or r.get('result')})")
        raw.update(r.get("value") or {})
    return raw


async def write(client: httpx.AsyncClient, ip: str, device_id: str, params: list[tuple[str, str]]) -> None:
    for i, (name, value) in enumerate(params):
        if i:
            await asyncio.sleep(1)
        body = {"device_id": device_id, "device_sub_id": 0, "req_id": "", "modified_by": "", "set_level": "02", "value": {name: value}}
        r = await _post(client, ip, "SetParam", body)
        if r.get("result") not in (None, "OK"):
            raise UnitError(f"the unit refused {name} ({r.get('error') or r.get('result')})")
