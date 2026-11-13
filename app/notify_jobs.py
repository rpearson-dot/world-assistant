"""Helpers for modules that send notifications (app/notify.py sends them); part of the module SDK. The senders live
with their modules: the bike garage after each Garmin sync (app/modules/bikes/notify.py), the environment every half
hour (app/modules/environment/notify.py).

Each is said once: by battery until it is next charged or changed, by part until it is next replaced or serviced, by
warning until it changes severity, by place and day for air and pollen. Several of the same kind after one sync go in
one message.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import notify

log = logging.getLogger(__name__)


async def send_many(engine: AsyncEngine, kind: str, items: list[tuple[str, str]], one: str, many: str, tags: list[str],
                     click: str | None = None) -> int:  # fmt: skip
    """Items (key, line) not sent before, in one message: its title `one` for one item, `many` (with {n}) for more."""
    fresh = [(k, line) for k, line in items if not await notify.sent(engine, k)]
    if not fresh:
        return 0
    st = await notify.settings(engine)
    if not st["enabled"] or not st["kinds"].get(kind):
        return 0
    title = one if len(fresh) == 1 else many.format(n=len(fresh))
    r = await notify.send(engine, kind, fresh[0][0], title, "\n".join(line for _, line in fresh), tags=tags, click=click)
    if r.get("sent"):  # the others are said too: remembered so they are not said again
        async with engine.begin() as c:
            for k, line in fresh[1:]:
                await c.execute(text("INSERT INTO notify_log (key, kind, title, body, ok) VALUES (:k, :kind, :t, :b, true) "
                                     "ON CONFLICT (key) DO NOTHING"), {"k": k, "kind": kind, "t": title, "b": line})  # fmt: skip
    return len(fresh) if r.get("sent") else 0


def page_link(path: str) -> str | None:
    from app.config import get_settings

    base = get_settings().ntfy_public_url
    return base.rsplit(":", 1)[0] + path if base else None  # the api is on the same device's 443


# the old names, for anything still using them
_send_many = send_many
_page = page_link
