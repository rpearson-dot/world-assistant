"""Removing an outside module, every trace of it, and a report that proves it.

In order: the runner deletes its container, storage volume and images on the server; then here its token and keys,
its Home Assistant entities and retained MQTT topics (an empty message to each one it published), its settings,
jobs, notifications, history and its row. Then every place is searched again for anything with its id, and the
report lists each place with what was found (nothing, when it worked).

Two things stay, and the report says so: backups taken while it was installed (they hold its settings and history
until they age out, or you delete them), and the record that it was installed and removed (no data of its own).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text


async def begin(conn: Any, r: dict[str, Any]) -> dict[str, Any]:
    from app.outside.api import enqueue, event, save

    mid = r["id"]
    refs = sorted({x for x in (r.get("ref"), (r.get("held") or {}).get("ref"), (r.get("candidate") or {}).get("ref")) if x})
    await save(conn, mid, state="removing", message="removing every trace")
    await conn.execute(text("DELETE FROM xmod_job WHERE module = :m AND state = 'waiting'"), {"m": mid})
    await enqueue(conn, mid, "remove", {"module": mid, "refs": refs}, "remove")
    await event(conn, mid, "remove", "removal started")
    return {"state": "removing", "report": f"/v1/outside/removed/{mid}"}


def _key_files(mid: str) -> list[str]:
    from app import secretstore

    d = secretstore.keys_dir()
    return sorted(p.name for p in d.glob(f"x_{mid}_*.txt")) if d.exists() else []


async def _retract(conn: Any, mid: str) -> tuple[int, str | None]:
    """An empty retained message to each topic it published: Home Assistant drops a discovered entity on an empty
    config, and the broker forgets a retained value on an empty one."""
    from app.mqtt import publish
    from app.mqtt_config import load

    topics = (await conn.execute(text("SELECT topic FROM xmod_published WHERE module = :m"), {"m": mid})).scalars().all()
    if not topics:
        return 0, None
    conf, _ = await load(conn.engine)
    ok = await publish([(t, "", True) for t in topics], conf)
    return len(topics), None if ok else "the MQTT broker could not be reached: its entities are taken back when it next can"


async def finish(conn: Any, mid: str, runner: dict[str, Any]) -> None:
    """The database side, once the runner has removed what is on the server; then the search and the report."""
    from app import secretstore

    retracted, mqtt_error = await _retract(conn, mid)
    files = _key_files(mid)
    for name in files:
        try:
            (secretstore.keys_dir() / name).unlink()
        except FileNotFoundError:
            pass
    secretstore.clear_cache()
    if mqtt_error is None:
        await conn.execute(text("DELETE FROM xmod_published WHERE module = :m"), {"m": mid})
    await conn.execute(text("DELETE FROM notify_log WHERE key LIKE :k"), {"k": f"x:{mid}:%"})
    await conn.execute(text("DELETE FROM xmod_job WHERE module = :m"), {"m": mid})
    await conn.execute(text("DELETE FROM xmod_event WHERE module = :m AND kind NOT IN ('install', 'remove')"), {"m": mid})
    await conn.execute(text("DELETE FROM xmod WHERE id = :m"), {"m": mid})
    report = await search(conn, mid, runner)
    report["mqtt_topics_retracted"] = retracted
    if mqtt_error:
        report["note"] = mqtt_error
    await conn.execute(text("INSERT INTO xmod_event (module, kind, text, data) VALUES (:m, 'removed', :t, CAST(:d AS jsonb))"),
                       {"m": mid, "t": "removed: " + ("nothing left" if report["clean"] else "something was left, see the report"),
                        "d": json.dumps(report, default=str)})  # fmt: skip
    from app import notify

    try:
        await notify.send(conn.engine, "modules", f"removed:{mid}:{datetime.now(UTC):%Y%m%d%H%M}", f"{mid} removed",
                          "Nothing of it is left." if report["clean"] else "Something of it was left: see its removal report.",
                          click="/settings#outside")  # fmt: skip
    except Exception:  # noqa: BLE001, S110 - the report is the record
        pass


async def search(conn: Any, mid: str, runner: dict[str, Any]) -> dict[str, Any]:
    """Every place an outside module can leave something, searched for its id, each with what was found."""
    checked = runner.get("checked") or {}
    places: list[dict[str, Any]] = [
        {"place": "containers on the server", "found": checked.get("containers") or []},
        {"place": "storage volumes on the server", "found": checked.get("volumes") or []},
        {"place": "images on the server", "found": checked.get("images") or []},
        {"place": "keys and its token", "found": _key_files(mid)},
    ]
    for label, sql in (
        ("its record and settings", "SELECT id FROM xmod WHERE id = :m"),
        ("jobs for the runner", "SELECT id::text FROM xmod_job WHERE module = :m"),
        ("Home Assistant entities and MQTT topics", "SELECT topic FROM xmod_published WHERE module = :m"),
        ("notifications sent", "SELECT key FROM notify_log WHERE key LIKE :k"),
        ("its history", "SELECT kind FROM xmod_event WHERE module = :m AND kind NOT IN ('install', 'remove', 'removed')"),
        ("settings saved elsewhere", "SELECT key FROM app_setting WHERE key LIKE :s"),
    ):
        rows = (await conn.execute(text(sql), {"m": mid, "k": f"x:{mid}:%", "s": f"x.{mid}.%"})).scalars().all()
        places.append({"place": label, "found": [str(x) for x in rows]})
    if runner.get("error"):
        places.append({"place": "the runner", "found": [str(runner["error"])]})
    return {
        "module": mid, "at": datetime.now(UTC).isoformat(), "places": places,
        "clean": not any(p["found"] for p in places),
        "kept": ["backups taken while it was installed (they age out, or delete them in Settings → Backups and updates)",
                 "the record that it was installed and removed (no data of its own)"],
    }  # fmt: skip
