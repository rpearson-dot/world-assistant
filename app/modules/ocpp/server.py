"""World Assistant as the charger's central system: the charger connects to ws://<server>:8010/ocpp/<its id> and
stays connected. Everything it says and everything sent to it is kept (store.py), with an event in words for each
connection, boot, fault and status change, so a charger that resets itself can be seen doing it.

Only from the home network (never through Tailscale or another proxy), and only the chargers listed in the
settings once any are listed. Commands from the API or Home Assistant reach whichever api process holds the
charger's connection through Redis, so they work during an update while two versions run.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import text

from app.localnet import private_peer, proxied
from app.modules.ocpp import ha, protocol, store

log = logging.getLogger("wa.ocpp")

ws_router = APIRouter()
CHANNEL = "wa:ocpp:cmd"
HOLDER_S = 120  # how long a "this process holds the charger" mark lasts unless renewed
REPLY_S = 30


def holder_key(cid: str) -> str:
    return f"wa:ocpp:holder:{cid}"


def reply_key(req: str) -> str:
    return f"wa:ocpp:reply:{req}"


class Link:
    """One charger's open connection in this process."""

    def __init__(self, ws: WebSocket, cid: str) -> None:
        self.ws, self.cid = ws, cid
        self.opened = time.monotonic()
        self.booted = False
        self.status: str | None = None
        self.transaction: int | None = None
        self.waiting: dict[str, asyncio.Future[protocol.Frame]] = {}
        self.send_lock = asyncio.Lock()
        self.last_in = time.monotonic()


class Hub:
    """The links this api process holds, the command listener, and the Home Assistant bridge."""

    def __init__(self) -> None:
        self.links: dict[str, Link] = {}
        self.engine: Any = None
        self.redis: Any = None
        self.tasks: list[asyncio.Task[Any]] = []
        self.process = uuid.uuid4().hex[:8]

    async def start(self, app: Any) -> None:
        self.engine, self.redis = app.state.engine, app.state.redis
        self.tasks = [asyncio.create_task(self._commands(), name="ocpp-commands"),
                      asyncio.create_task(self._keepalive(), name="ocpp-keepalive")]  # fmt: skip

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        for link in list(self.links.values()):
            with contextlib.suppress(Exception):
                await link.ws.close(code=1001)
        self.tasks = []

    # --- keeping ---------------------------------------------------------------------------------------------------

    async def keep(self, cid: str, *, frame: protocol.Frame | None = None, out: tuple[int, str | None, str | None, Any, str] | None = None,
                   events: list[tuple[str, str, dict[str, Any]]] = (), **cols: Any) -> None:  # type: ignore[assignment]
        """Write a frame, events and changes in one go. Never raises: the charger's link must not drop over a
        database hiccup (the failure is logged)."""
        try:
            async with self.engine.begin() as conn:
                if frame is not None:
                    await store.message(conn, cid, "in", frame.kind, frame.unique_id, frame.action,
                                        frame.payload if frame.kind != protocol.ERROR else {"code": frame.error_code, "text": frame.error_text},
                                        frame.raw)  # fmt: skip
                if out is not None:
                    await store.message(conn, cid, "out", *out)
                for kind, words, detail in events:
                    await store.event(conn, cid, kind, words, detail)
                if cols:
                    await store.charger(conn, cid, **cols)
        except Exception:
            log.exception("ocpp: could not record for %s", cid)

    # --- the connection -----------------------------------------------------------------------------------------------

    async def serve(self, ws: WebSocket, cid: str) -> None:
        async with self.engine.connect() as conn:
            st = await store.settings(conn)
        allowed = [c for c in st.get("chargers") or [] if c]
        ok_peer = private_peer(ws) and not proxied(ws)
        if not ok_peer or (allowed and cid not in allowed) or not cid.replace("-", "").replace("_", "").isalnum() or len(cid) > 48:
            await ws.close(code=1008)
            why = "not from the home network" if not ok_peer else "not a listed charger" if allowed and cid not in allowed else "a bad id"
            log.warning("ocpp: refused a charger connection (%s)", why)
            if ok_peer and len(cid) <= 48:
                await self.keep(cid[:48], events=[("refused", f"Refused a connection as {cid[:48]!r}: {why}", {})])
            return
        proto = "ocpp1.6" if "ocpp1.6" in (ws.headers.get("sec-websocket-protocol") or "") else None
        await ws.accept(subprotocol=proto)
        old = self.links.get(cid)
        link = Link(ws, cid)
        self.links[cid] = link
        if old is not None:
            with contextlib.suppress(Exception):
                await old.ws.close(code=1000)
        async with self.engine.connect() as conn:
            link.transaction = await store.open_transaction(conn, cid)
            row = (await conn.execute(text("SELECT status FROM ocpp_charger WHERE id = :c"), {"c": cid})).scalar()
        link.status = row
        peer = ws.headers.get("x-wa-peer") or (ws.client.host if ws.client else "?")
        await self.keep(cid, events=[("connected", f"Connected from {peer}" + ("" if proto else " (no OCPP 1.6 subprotocol asked)"),
                                      {"peer": peer, "subprotocol": proto, "replaced": old is not None})],
                        connected=True, connected_at=_now(), last_seen=_now())  # fmt: skip
        await self.redis.set(holder_key(cid), self.process, ex=HOLDER_S)
        ha.started(self, cid)
        meter_task = asyncio.create_task(self._meter(link, int(st.get("meter_every_s") or 60)))
        close_code, why = None, ""
        try:
            while True:
                raw = await ws.receive_text()
                link.last_in = time.monotonic()
                await self._incoming(link, raw)
        except WebSocketDisconnect as exc:
            close_code, why = exc.code, getattr(exc, "reason", "") or ""
        except Exception as exc:  # noqa: BLE001 - recorded as how the link ended
            close_code, why = None, f"{type(exc).__name__}: {exc}"
        finally:
            meter_task.cancel()
            for f in link.waiting.values():
                if not f.done():
                    f.cancel()
            if self.links.get(cid) is link:
                del self.links[cid]
                with contextlib.suppress(Exception):
                    if await self.redis.get(holder_key(cid)) == self.process:
                        await self.redis.delete(holder_key(cid))
                mins = round((time.monotonic() - link.opened) / 60, 1)
                quiet = round(time.monotonic() - link.last_in)
                words = (f"Disconnected after {mins} min" + (f" (code {close_code}" + (f", {why}" if why else "") + ")" if close_code else f" ({why})" if why else "")
                         + (f"; nothing heard for the last {quiet} s" if quiet > 90 else ""))  # fmt: skip
                await self.keep(cid, events=[("disconnected", words, {"code": close_code, "reason": why, "minutes": mins, "quiet_s": quiet})],
                                connected=False)  # fmt: skip
                ha.changed(self, cid)
            else:
                await self.keep(cid, events=[("replaced", "An older connection closed after the charger connected again", {})])

    async def _incoming(self, link: Link, raw: str) -> None:
        cid = link.cid
        try:
            f = protocol.parse(raw)
        except protocol.FrameError as exc:
            await self.keep(cid, events=[("bad", f"Could not read a message: {exc}", {"raw": raw[:500]})])
            if exc.unique_id:
                await self._send(link, protocol.error(exc.unique_id, exc.code, str(exc)), (protocol.ERROR, exc.unique_id, None, {"code": exc.code}))
            return
        if f.kind in (protocol.RESULT, protocol.ERROR):
            await self.keep(cid, frame=f, last_seen=_now())
            fut = link.waiting.pop(f.unique_id, None)
            if fut is not None and not fut.done():
                fut.set_result(f)
            return
        tid = 0
        if f.action == "StartTransaction":
            async with self.engine.connect() as conn:
                tid = await store.next_transaction(conn, cid)
        eff = protocol.respond(f.action or "", f.payload, connected_s=time.monotonic() - link.opened,
                               booted_before=link.booted, transaction_id=tid, last_status=link.status)  # fmt: skip
        if f.action == "BootNotification":
            link.booted = True
        if eff.error:
            reply, out = protocol.error(f.unique_id, *eff.error), (protocol.ERROR, f.unique_id, f.action, {"code": eff.error[0]})
        else:
            reply, out = protocol.result(f.unique_id, eff.answer or {}), (protocol.RESULT, f.unique_id, f.action, eff.answer or {})
        await self._send(link, reply, None)  # the answer first: a charger waits on it
        cols = dict(eff.charger)
        if "status" in cols:
            link.status = cols["status"]
        try:
            async with self.engine.begin() as conn:
                await store.message(conn, cid, "in", f.kind, f.unique_id, f.action, f.payload, f.raw)
                await store.message(conn, cid, "out", out[0], out[1], out[2], out[3], reply)
                for kind, words, detail in eff.events:
                    await store.event(conn, cid, kind, words, detail)
                await store.charger(conn, cid, last_seen=_now(), **cols)
                if eff.session_start:
                    await store.session_start(conn, cid, eff.session_start)
                    link.transaction = eff.session_start["transaction_id"]
                if eff.session_stop:
                    await store.session_stop(conn, cid, eff.session_stop)
                    if link.transaction == eff.session_stop.get("transaction_id"):
                        link.transaction = None
                if eff.session_meter:
                    await store.session_meter(conn, cid, eff.session_meter)
                    if link.transaction is None and eff.session_meter.get("transaction_id") is not None:
                        link.transaction = int(eff.session_meter["transaction_id"])
        except Exception:
            log.exception("ocpp: could not record %s from %s", f.action, cid)
        if cols or eff.session_start or eff.session_stop:
            ha.changed(self, cid)

    async def _send(self, link: Link, raw: str, record: tuple[int, str | None, str | None, Any] | None) -> None:
        async with link.send_lock:
            await link.ws.send_text(raw)
        if record is not None:
            await self.keep(link.cid, out=(*record, raw))

    async def command(self, cid: str, action: str, payload: dict[str, Any], timeout: float = REPLY_S) -> dict[str, Any]:
        """Send a command on a link this process holds and wait for the charger's answer."""
        link = self.links.get(cid)
        if link is None:
            raise LookupError("the charger is not connected here")
        uid, raw = protocol.call(action, payload)
        fut: asyncio.Future[protocol.Frame] = asyncio.get_running_loop().create_future()
        link.waiting[uid] = fut
        await self._send(link, raw, (protocol.CALL, uid, action, payload))
        try:
            f = await asyncio.wait_for(fut, timeout)
        except TimeoutError:
            link.waiting.pop(uid, None)
            await self.keep(cid, events=[("command", f"{action}: no answer within {int(timeout)} s", {"payload": payload})])
            return {"ok": False, "answer": None, "error": f"no answer within {int(timeout)} s"}
        if f.kind == protocol.ERROR:
            await self.keep(cid, events=[("command", f"{action}: refused ({f.error_code}: {f.error_text})", {"payload": payload})])
            return {"ok": False, "answer": None, "error": f"{f.error_code}: {f.error_text}"}
        status = (f.payload or {}).get("status") if isinstance(f.payload, dict) else None
        await self.keep(cid, events=[("command", f"{action}: {status or 'answered'}", {"payload": payload, "answer": f.payload})])
        if action == "GetConfiguration" and isinstance(f.payload, dict):
            conf = {k.get("key"): k.get("value") for k in f.payload.get("configurationKey") or [] if isinstance(k, dict)}
            await self.keep(cid, config=conf)
        return {"ok": status in (None, "Accepted", "Unlocked", "Scheduled"), "answer": f.payload, "error": None}

    async def _meter(self, link: Link, every: int) -> None:
        """While it charges, ask for meter readings (some chargers, the Sync EV among them, never send them)."""
        await asyncio.sleep(5)
        with contextlib.suppress(Exception):
            await self.command(link.cid, "TriggerMessage", {"requestedMessage": "StatusNotification"}, 20)
        while True:
            await asyncio.sleep(max(15, every))
            if link.status == "Charging":
                with contextlib.suppress(Exception):
                    await self.command(link.cid, "TriggerMessage", {"requestedMessage": "MeterValues", "connectorId": 1}, 20)

    async def _keepalive(self) -> None:
        while True:
            await asyncio.sleep(HOLDER_S / 3)
            for cid in list(self.links):
                with contextlib.suppress(Exception):
                    await self.redis.set(holder_key(cid), self.process, ex=HOLDER_S)

    async def _commands(self) -> None:
        """Commands for chargers, from any api process: the one holding the charger's link sends it and answers."""
        while True:
            try:
                ps = self.redis.pubsub()
                await ps.subscribe(CHANNEL)
                async for m in ps.listen():
                    if m.get("type") != "message":
                        continue
                    try:
                        req = json.loads(m["data"])
                    except (ValueError, TypeError):
                        continue
                    if req.get("charger") in self.links:
                        asyncio.create_task(self._answer(req))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - Redis restarting: listen again shortly
                log.warning("ocpp: command listener lost Redis; retrying")
                await asyncio.sleep(5)

    async def _answer(self, req: dict[str, Any]) -> None:
        try:
            out = await self.command(req["charger"], req["action"], req.get("payload") or {})
        except Exception as exc:  # noqa: BLE001 - goes back to whoever asked
            out = {"ok": False, "answer": None, "error": str(exc)}
        await self.redis.set(reply_key(req["id"]), json.dumps(out, default=str), ex=120)


def _now() -> datetime:
    return datetime.now(UTC)


HUB = Hub()


async def ask(redis: Any, cid: str, action: str, payload: dict[str, Any], wait_s: float = REPLY_S + 5) -> dict[str, Any]:
    """Send a command to a charger through whichever api process holds it, and wait for its answer."""
    if HUB.links.get(cid) is not None:
        return await HUB.command(cid, action, payload)
    if not await redis.get(holder_key(cid)):
        raise LookupError("the charger is not connected to World Assistant")
    req = uuid.uuid4().hex
    await redis.publish(CHANNEL, json.dumps({"id": req, "charger": cid, "action": action, "payload": payload}))
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        v = await redis.get(reply_key(req))
        if v:
            return json.loads(v)
        await asyncio.sleep(0.3)
    return {"ok": False, "answer": None, "error": "no answer from the process holding the charger"}


@ws_router.websocket("/ocpp/{cid}")
async def charger_socket(ws: WebSocket, cid: str) -> None:
    from app.modules.gate import is_enabled

    if HUB.engine is None or not await is_enabled("ocpp", HUB.engine):
        await ws.close(code=1008)
        return
    await HUB.serve(ws, cid)


async def startup(app: Any) -> None:
    await HUB.start(app)


async def shutdown() -> None:
    await HUB.stop()
