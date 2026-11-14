"""The syslog listener (the wa-syslog service): receives what the UniFi gateway sends on UDP (port 5514 in the
container; the host's port is WA_SYSLOG_PORT, 5514 by default), reads each line and writes them in batches every two
seconds. A new device sends a notification ("a new device joined the network") when that is switched on.

It accepts lines only from private addresses (the home network), and at most 5,000 a second: a flood is dropped and
counted, never queued without end. Run as `python -m app.modules.network.listener`.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from typing import Any

from app.modules.network import parse, store

log = logging.getLogger("wa.syslog")
PORT = 5514
MAX_PER_SECOND = 5000


class Syslog(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.queue: list[dict[str, Any]] = []
        self.dropped = 0
        self._second, self._count = 0, 0

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            if not ipaddress.ip_address(addr[0]).is_private:
                return
        except ValueError:
            return
        now = int(time.monotonic())
        if now != self._second:
            self._second, self._count = now, 0
        self._count += 1
        if self._count > MAX_PER_SECOND or len(self.queue) > 50_000:
            self.dropped += 1
            return
        for raw in data.decode("utf-8", "replace").splitlines():
            if raw.strip():
                ev = parse.line(raw)
                ev["detail"] = {**(ev.get("detail") or {}), "from": addr[0]}
                self.queue.append(ev)


async def flush(engine: Any, proto: Syslog) -> None:
    from app import notify

    while True:
        await asyncio.sleep(2)
        batch, proto.queue = proto.queue, []
        if not batch:
            continue
        try:
            async with engine.begin() as conn:
                new = await store.keep(conn, batch)
                st = await store.settings(conn)
            for mac in new if st["notify_new"] else []:
                e = next((x for x in batch if x.get("mac") == mac), {})
                await notify.send(engine, "network", f"net:new:{mac}", "A new device joined the network",
                                  f"{e.get('name') or 'A device'} ({mac}{', ' + e['ip'] if e.get('ip') else ''}).", click="/network")  # fmt: skip
        except Exception:
            log.exception("could not keep %d network events", len(batch))


async def main() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.config import get_settings

    engine = create_async_engine(get_settings().database_url, pool_size=2, max_overflow=1)
    proto = Syslog()
    await asyncio.get_running_loop().create_datagram_endpoint(lambda: proto, local_addr=("0.0.0.0", PORT))
    log.info("listening for syslog on UDP %s", PORT)
    await flush(engine, proto)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
