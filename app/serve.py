"""Run the API under uvicorn with the WebSocket settings an OCPP charger needs.

Two uvicorn defaults stop World Assistant talking to a charger over OCPP, so they are turned off here — the same
choices Home Assistant's OCPP integration makes (it calls websockets.serve with ping_interval=None and the websockets
library's small-window deflate):

- **permessage-deflate compression off.** uvicorn's default compresses outbound frames with a ~32 KB window
  (server_max_window_bits=None). Some chargers (the Sync EV) cannot decompress that and silently drop every frame we
  send, while their own frames still arrive — so data worked but no command was ever answered. OCPP-J messages are
  tiny, so compression gains nothing; off is simplest.
- **automatic WebSocket pings off.** uvicorn sends a ping every 20 s and drops the link if no pong comes in 20 s; a
  charger that mishandles ping control frames or is slow to pong is disrupted. Liveness is handled at the OCPP layer
  (Heartbeat) and by World Assistant's own holder keepalive instead.

Only the API uses this; the worker and migrate containers override the command in compose.
"""

from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        proxy_headers=True,
        ws_per_message_deflate=False,  # no WebSocket compression (see module docstring)
        ws_ping_interval=None,  # no automatic WebSocket pings (see module docstring)
        ws_ping_timeout=None,
    )


if __name__ == "__main__":
    main()
