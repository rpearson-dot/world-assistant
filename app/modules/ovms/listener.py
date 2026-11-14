"""Listening to the car's OVMS module on the MQTT broker World Assistant uses (Settings → Home Assistant), from the
api. Only one api process listens at a time (a Redis lease), so during an update nothing is written twice.

Values are kept as they arrive and written every 10 s; the history of the metrics in parse.KEEP is added to when
they change (at most once a minute while one keeps moving). Each alert or error notification is kept and sent to
the phone; warnings worked out from the metrics (parse.warnings) are sent once each time they start.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from app.modules.ovms import parse, store
from app.mqtt_config import client, load

log = logging.getLogger("wa.ovms")
LEASE = "wa:ovms:listener"
LEASE_S = 60
FLUSH_S = 10


class Listener:
    def __init__(self) -> None:
        self.engine: Any = None
        self.redis: Any = None
        self.me = uuid.uuid4().hex[:8]
        self.task: asyncio.Task[Any] | None = None
        self.latest: dict[tuple[str, str], tuple[str, datetime]] = {}
        self.logged: dict[tuple[str, str], tuple[str, float]] = {}
        self.listening = False

    async def start(self, app: Any) -> None:
        self.engine, self.redis = app.state.engine, app.state.redis
        self.task = asyncio.create_task(self._run(), name="ovms")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(BaseException):
                await self.task
        with contextlib.suppress(Exception):
            if await self.redis.get(LEASE) == self.me:
                await self.redis.delete(LEASE)

    async def _lease(self) -> bool:
        cur = await self.redis.get(LEASE)
        if cur == self.me:
            await self.redis.set(LEASE, self.me, ex=LEASE_S)
            return True
        return bool(await self.redis.set(LEASE, self.me, ex=LEASE_S, nx=True))

    async def _run(self) -> None:
        while True:
            try:
                if not await self._lease():
                    await asyncio.sleep(LEASE_S / 2)
                    continue
                async with self.engine.connect() as conn:
                    st = await store.settings(conn)
                conf, _ = await load(self.engine)
                if not st.get("enabled", True) or not conf.active:
                    await asyncio.sleep(60)
                    continue
                await self._listen(conf, st["prefix"])
            except asyncio.CancelledError:
                raise
            except Exception:
                log.info("ovms: listener reconnecting", exc_info=True)
                self.listening = False
                await asyncio.sleep(30)

    async def _listen(self, conf: Any, prefix: str) -> None:
        base = prefix.rstrip("/") + "/"
        flusher = asyncio.create_task(self._flusher())
        try:
            async with client(conf, timeout=15) as c:
                await c.subscribe(base + "metric/#", qos=0)
                await c.subscribe(base + "notify/#", qos=1)
                await c.subscribe(base + "event", qos=0)
                self.listening = True
                async for msg in c.messages:
                    await self._message(str(msg.topic), msg.payload, base)
        finally:
            self.listening = False
            flusher.cancel()
            await self._flush()

    async def _message(self, topic: str, payload: Any, prefix: str) -> None:
        got = parse.topic(topic, prefix)
        if got is None:
            return
        vehicle, kind, name = got
        value = payload.decode(errors="replace") if isinstance(payload, (bytes, bytearray)) else str(payload)
        now = datetime.now(UTC)
        if kind == "metric":
            self.latest[(vehicle, name)] = (value[:2000], now)
            return
        if kind == "notify":
            ntype, _, sub = name.partition("/")
            if ntype not in parse.NOTIFY_KINDS:
                return
            async with self.engine.begin() as conn:
                nid = await store.notice(conn, vehicle, ntype, sub, value[:2000])
            if ntype in ("alert", "error"):
                await _phone(self.engine, f"ovms:notice:{nid}", f"Car {ntype}", f"{vehicle}: {value[:300]}")
            return
        if kind == "event":
            async with self.engine.begin() as conn:
                await store.notice(conn, vehicle, "event", value[:120], value[:500])

    async def _flusher(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_S)
            with contextlib.suppress(Exception):
                await self.redis.set(LEASE, self.me, ex=LEASE_S)
            await self._flush()

    async def _flush(self) -> None:
        if not self.latest:
            return
        batch, self.latest = self.latest, {}
        mono = time.monotonic()
        logs = []
        for (veh, metric), (value, at) in batch.items():
            if metric not in parse.KEEP:
                continue
            last = self.logged.get((veh, metric))
            if last is None or (last[0] != value and mono - last[1] >= (0 if parse.truthy(value) is not None else 60)):
                logs.append((veh, metric, value, at))
                self.logged[(veh, metric)] = (value, mono)
        try:
            async with self.engine.begin() as conn:
                await store.latest(conn, [(v, m, val, at) for (v, m), (val, at) in batch.items()])
                await store.history(conn, logs)
        except Exception:
            log.exception("ovms: could not record")
        for veh in {v for v, _ in batch}:
            await check(self.engine, veh)


async def _phone(engine: Any, key: str, title: str, body: str) -> None:
    from app import notify

    with contextlib.suppress(Exception):
        await notify.send(engine, "home", key, title, body, tags=["car"], click="/vehicles")


async def check(engine: Any, vehicle: str) -> list[dict[str, Any]]:
    """The car's warnings now; a notification for each that has just started (sent once per start)."""
    async with engine.connect() as conn:
        m = await store.metrics(conn, vehicle)
    now = datetime.now(UTC)
    ws = parse.warnings(m, now)
    async with engine.begin() as conn:
        started = await store.warnings_now(conn, vehicle, [w["key"] for w in ws])
    for w in ws:
        if w["key"] in started and w["level"] != "info":
            await _phone(engine, f"ovms:warn:{vehicle}:{w['key']}:{started[w['key']]}", "Car warning", f"{vehicle}: {w['text']}")
    return ws


LISTENER = Listener()


async def startup(app: Any) -> None:
    await LISTENER.start(app)


async def shutdown() -> None:
    await LISTENER.stop()
