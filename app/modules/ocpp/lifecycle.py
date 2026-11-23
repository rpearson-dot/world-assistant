"""Start both charger backends in the api: the built-in OCPP WebSocket server (server.py) and the ocpp2mqtt MQTT
bridge (bridge.py). Both run; the `backend` setting (Charger page) decides which one the charger uses and which one
World Assistant drives, switchable live. Each idles cheaply when it is not the chosen backend."""

from __future__ import annotations

from typing import Any

from app.modules.ocpp import bridge, server


async def startup(app: Any) -> None:
    await server.startup(app)
    await bridge.startup(app)


async def shutdown() -> None:
    await bridge.shutdown()
    await server.shutdown()
