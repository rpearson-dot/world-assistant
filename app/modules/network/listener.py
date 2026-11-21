"""The syslog listener (the wa-syslog service): receives what the UniFi gateway and the car charger send on port 5514
in the container (the host's port is WA_SYSLOG_PORT, 5514 by default), over **both UDP and TCP** — devices differ, and
a plain "log server: IP + port" field often means TCP — reads each line and writes them in batches every two seconds.
A new device sends a notification ("a new device joined the network") when that is switched on. Nothing of the UniFi
stream is kept while the UniFi gateway log module is switched off.

The car charger can be pointed at this same port. Its lines are not UniFi events, so they are told apart — by the
address the charger last connected to World Assistant's OCPP server from, and, should that address have changed, by the
charge-point ID it puts in the syslog host field — and kept apart, in the charger's own log (ocpp_log). A line that
matches no charger still goes the ordinary way, so nothing is lost. The charger map is refreshed every half minute, so a
new or moved charger is picked up soon after it reconnects.

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
from app.modules.ocpp import store as ocpp_store
from app.modules.ocpp import syslog as ocpp_syslog

log = logging.getLogger("wa.syslog")
PORT = 5514
MAX_PER_SECOND = 5000
REFRESH_S = 30  # how often the charger map (address and id) is reloaded


class Syslog(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.queue: list[dict[str, Any]] = []
        self.charger_queue: list[dict[str, Any]] = []
        self.charger_ips: dict[str, str] = {}  # private address -> charge-point ID
        self.charger_hosts: dict[str, str] = {}  # lower-cased host it names itself -> charge-point ID
        self.dropped = 0
        self._second, self._count = 0, 0

    def _charger(self, ip: str, raw: str) -> str | None:
        """The charge-point ID this line belongs to, or None for an ordinary (UniFi) line."""
        cid = self.charger_ips.get(ip)
        if cid is None and self.charger_hosts:
            host = ocpp_syslog.hostname(raw)
            if host:
                cid = self.charger_hosts.get(host)
        return cid

    @staticmethod
    def _is_private(ip: str) -> bool:
        try:
            return ipaddress.ip_address(ip).is_private
        except ValueError:
            return False

    def _allow(self) -> bool:
        """The per-second rate limit and the queue ceiling, shared by UDP and TCP. A flood is dropped and counted."""
        now = int(time.monotonic())
        if now != self._second:
            self._second, self._count = now, 0
        self._count += 1
        if self._count > MAX_PER_SECOND or len(self.queue) + len(self.charger_queue) > 50_000:
            self.dropped += 1
            return False
        return True

    def ingest(self, ip: str, raw: str) -> None:
        """One line from one device: to the charger's log when it belongs to a charger, otherwise the UniFi stream."""
        if not raw.strip() or not self._allow():
            return
        cid = self._charger(ip, raw)
        if cid is not None:
            s = ocpp_syslog.read(raw)
            self.charger_queue.append({"charger": cid, "facility": s["facility"], "severity": s["severity"],
                                       "message": s["message"], "raw": s["raw"], "from_ip": ip})  # fmt: skip
            return
        ev = parse.line(raw)
        ev["detail"] = {**(ev.get("detail") or {}), "from": ip}
        self.queue.append(ev)

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if not self._is_private(addr[0]):
            return
        for raw in data.decode("utf-8", "replace").splitlines():
            self.ingest(addr[0], raw)

    async def handle_tcp(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """A TCP syslog connection: read newline-delimited lines (the common framing) and feed them in. A very long
        line with no newline is flushed so the buffer can't grow without bound; a final unterminated line is kept."""
        peer = writer.get_extra_info("peername")
        ip = peer[0] if peer else ""
        if not self._is_private(ip):
            writer.close()
            return
        buf = b""
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self.ingest(ip, line.decode("utf-8", "replace"))
                if len(buf) > 65536:
                    self.ingest(ip, buf.decode("utf-8", "replace"))
                    buf = b""
        except (OSError, asyncio.IncompleteReadError):
            log.debug("tcp syslog read ended for %s", ip)
        finally:
            if buf.strip():
                self.ingest(ip, buf.decode("utf-8", "replace"))
            try:
                writer.close()
            except OSError:
                pass


async def refresh_chargers(engine: Any, proto: Syslog) -> None:
    """Reload which private addresses (and host names) belong to chargers."""
    try:
        async with engine.connect() as conn:
            rows = await ocpp_store.peers(conn)
    except Exception:  # noqa: BLE001 - a hiccup reading the map must not stop the listener; keep the last set
        log.debug("could not read charger addresses; keeping the last set")
        return
    ips: dict[str, str] = {}
    hosts: dict[str, str] = {}
    for r in rows:  # newest first: the first id seen for an address or host wins
        cid, peer = r["id"], r.get("peer")
        if peer:
            try:
                if ipaddress.ip_address(peer).is_private:
                    ips.setdefault(peer, cid)
            except ValueError:
                pass
        hosts.setdefault(cid.lower(), cid)
    proto.charger_ips, proto.charger_hosts = ips, hosts


async def _keep_charger(engine: Any, batch: list[dict[str, Any]]) -> None:
    try:
        async with engine.begin() as conn:
            await ocpp_store.log(conn, batch)
    except Exception:
        log.exception("could not keep %d charger log lines", len(batch))


async def flush(engine: Any, proto: Syslog) -> None:
    from app import notify

    since_refresh = 0.0
    await refresh_chargers(engine, proto)
    while True:
        await asyncio.sleep(2)
        since_refresh += 2
        if since_refresh >= REFRESH_S:
            since_refresh = 0.0
            await refresh_chargers(engine, proto)

        charger_batch, proto.charger_queue = proto.charger_queue, []
        if charger_batch:
            await _keep_charger(engine, charger_batch)

        batch, proto.queue = proto.queue, []
        if not batch:
            continue
        try:
            from app.modules import gate

            if not await gate.is_enabled("unifi", engine):  # the UniFi gateway log switched off: nothing kept
                continue
        except Exception:  # noqa: BLE001 - the choice cannot be read: keep, as everything is on then
            log.debug("could not read whether the UniFi gateway log is on; keeping")
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
    server = await asyncio.start_server(proto.handle_tcp, "0.0.0.0", PORT)
    log.info("listening for syslog on UDP and TCP %s", PORT)
    async with server:
        await flush(engine, proto)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
