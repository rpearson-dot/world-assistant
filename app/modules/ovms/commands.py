"""Sending a car's OVMS module a command over MQTT and reading its reply.

OVMS v3 runs a command published to <prefix>client/<app>/command/<id> and publishes the output to
<prefix>client/<app>/response/<id> (https://docs.openvehicles.com/en/latest/userguide/commands.html). <prefix> is the
car's own topic prefix (ovms/<user>/<vehicle id>/), remembered by the listener as the car reports. The payload is a
shell command, the same text you would type in the OVMS console.

This needs the broker on TLS with World Assistant's login allowed to read and write ovms/#, so it does nothing until
the broker move is done.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import Any

import aiomqtt

from app.modules.ovms import store
from app.mqtt_config import client, explain, load

log = logging.getLogger("wa.ovms.cmd")
APP_ID = "wa"  # the client/<app> segment; the response comes back under the same one


async def run(engine: Any, vehicle: str, command: str, *, timeout: float = 20.0) -> dict[str, Any]:
    """Send one command to a car and return {ok, response} or {ok: False, error}. The command is the OVMS console text
    (e.g. "charge start"). The car must have reported at least once (so its prefix is known) and be reachable."""
    async with engine.connect() as conn:
        base = await store.prefix(conn, vehicle)
    if not base:
        return {"ok": False, "error": "no topic prefix known for this car yet — wait until it has reported once"}
    conf, _ = await load(engine)
    if not conf.active:
        return {"ok": False, "error": "no MQTT broker is set (Settings → Home Assistant)"}
    req = uuid.uuid4().hex[:10]
    ctopic = f"{base}client/{APP_ID}/command/{req}"
    rtopic = f"{base}client/{APP_ID}/response/{req}"
    try:
        async with asyncio.timeout(timeout):
            async with client(conf, timeout=10) as c:
                await c.subscribe(rtopic, qos=1)
                await c.publish(ctopic, command.encode(), qos=1)
                parts: list[str] = []
                async for msg in c.messages:
                    payload = msg.payload
                    parts.append(payload.decode(errors="replace") if isinstance(payload, (bytes, bytearray)) else str(payload))
                    # the short confirmations we send fit one message; wait briefly for any continuation, then stop
                    with contextlib.suppress(TimeoutError):
                        async with asyncio.timeout(1.5):
                            async for more in c.messages:
                                mp = more.payload
                                parts.append(mp.decode(errors="replace") if isinstance(mp, (bytes, bytearray)) else str(mp))
                    break
        return {"ok": True, "response": "".join(parts).strip()}
    except TimeoutError:
        return {"ok": False, "error": "the car did not answer — it may be asleep (turning on the A/C wakes the CAN bus)"}
    except (aiomqtt.MqttError, OSError) as exc:
        return {"ok": False, "error": explain(exc, conf)}
    except Exception as exc:  # noqa: BLE001 - surfaced to the page
        return {"ok": False, "error": str(exc)[:200]}


# High-level actions → the OVMS console command they send. charge/lock syntax is standard across OVMS vehicles;
# the climate command is confirmed live with the generic command before the button is relied on.
def charge_cmd(on: bool) -> str:
    return "charge start" if on else "charge stop"


def climate_cmd(on: bool) -> str:
    return "climatecontrol on" if on else "climatecontrol off"


def lock_cmd(lock: bool, pin: str) -> str:
    return f"{'lock' if lock else 'unlock'} {pin}"
