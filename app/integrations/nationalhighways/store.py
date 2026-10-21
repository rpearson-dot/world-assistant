"""Postgres storage for National Highways events, signs and corridors."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.integrations.nationalhighways.match import Corridor, event_on_corridor
from app.integrations.nationalhighways.normalise import RoadEvent, Segment, VmsSign

_EVENT_UPSERT = text(
    """
    INSERT INTO nh_event
      (record_id, scope, kind, situation_id, status, start_time, end_time, cause, detail,
       limit_mph, roads, segments, version_time, last_seen, cleared_at)
    VALUES
      (:record_id, :scope, :kind, :situation_id, :status, :start_time, :end_time, :cause,
       :detail, :limit_mph, :roads, CAST(:segments AS jsonb), :version_time, :seen, NULL)
    ON CONFLICT (record_id) DO UPDATE SET
      scope = EXCLUDED.scope, kind = EXCLUDED.kind, situation_id = EXCLUDED.situation_id,
      status = EXCLUDED.status, start_time = EXCLUDED.start_time, end_time = EXCLUDED.end_time,
      cause = EXCLUDED.cause, detail = EXCLUDED.detail, limit_mph = EXCLUDED.limit_mph,
      roads = EXCLUDED.roads, segments = EXCLUDED.segments,
      version_time = EXCLUDED.version_time, last_seen = EXCLUDED.last_seen, cleared_at = NULL
    """
)


def segments_to_json(segments: Sequence[Segment]) -> str:
    return json.dumps(
        [
            {
                "road": s.road,
                "direction": s.direction,
                "description": s.description,
                "points": [list(p) for p in s.points],
            }
            for s in segments
        ]
    )


def segments_from_json(data: Any) -> tuple[Segment, ...]:
    items = json.loads(data) if isinstance(data, str) else data
    return tuple(
        Segment(
            road=i.get("road"),
            direction=i.get("direction"),
            description=i.get("description"),
            points=tuple((p[0], p[1]) for p in i.get("points", [])),
        )
        for i in items
    )


def _event_params(e: RoadEvent, scope: str, seen: datetime) -> dict[str, Any]:
    return {
        "record_id": e.record_id,
        "scope": scope,
        "kind": e.kind,
        "situation_id": e.situation_id,
        "status": e.status,
        "start_time": e.start,
        "end_time": e.end,
        "cause": e.cause,
        "detail": e.detail,
        "limit_mph": e.limit_mph,
        "roads": sorted(e.roads),
        "segments": segments_to_json(e.segments),
        "version_time": e.version_time,
        "seen": seen,
    }


async def load_corridors(engine: AsyncEngine, *, only_enabled: bool = True) -> list[Corridor]:
    query = "SELECT id, name, centreline, buffer_m, roads FROM nh_corridor"
    if only_enabled:
        query += " WHERE enabled"
    async with engine.connect() as conn:
        rows = (await conn.execute(text(query + " ORDER BY id"))).mappings().all()
    return [
        Corridor(
            id=r["id"],
            name=r["name"],
            centreline=tuple((p[0], p[1]) for p in r["centreline"]),
            buffer_m=r["buffer_m"],
            roads=frozenset(r["roads"] or []),
        )
        for r in rows
    ]


async def upsert_corridor(
    engine: AsyncEngine, corridor: Corridor, *, source: str = "manual", enabled: bool = True
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO nh_corridor (id, name, centreline, buffer_m, roads, enabled, source)
                VALUES (:id, :name, CAST(:centreline AS jsonb), :buffer_m, :roads, :enabled, :source)
                ON CONFLICT (id) DO UPDATE SET
                  name = EXCLUDED.name, centreline = EXCLUDED.centreline,
                  buffer_m = EXCLUDED.buffer_m, roads = EXCLUDED.roads,
                  enabled = EXCLUDED.enabled, source = EXCLUDED.source, updated_at = now()
                """
            ),
            {
                "id": corridor.id,
                "name": corridor.name,
                "centreline": json.dumps([list(p) for p in corridor.centreline]),
                "buffer_m": corridor.buffer_m,
                "roads": sorted(corridor.roads),
                "enabled": enabled,
                "source": source,
            },
        )


async def save_events(
    engine: AsyncEngine,
    events: Sequence[RoadEvent],
    corridors: Sequence[Corridor],
    scope: str,
    seen: datetime,
) -> int:
    """Upsert events and refresh their corridor tags. Returns events on any corridor."""
    on_corridor = 0
    async with engine.begin() as conn:
        for e in events:
            if not e.record_id:
                continue
            await conn.execute(_EVENT_UPSERT, _event_params(e, scope, seen))
            await conn.execute(
                text("DELETE FROM nh_event_corridor WHERE record_id = :r"), {"r": e.record_id}
            )
            hits = [c.id for c in corridors if event_on_corridor(e, c)]
            on_corridor += bool(hits)
            for cid in hits:
                await conn.execute(
                    text(
                        "INSERT INTO nh_event_corridor (record_id, corridor_id) VALUES (:r, :c) "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {"r": e.record_id, "c": cid},
                )
    return on_corridor


async def clear_unseen(engine: AsyncEngine, scope: str, kind: str, seen: datetime) -> int:
    """Mark events of a scope and kind that a complete fetch no longer returned as cleared."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE nh_event SET cleared_at = :seen
                WHERE scope = :scope AND kind = :kind AND cleared_at IS NULL AND last_seen < :seen
                """
            ),
            {"scope": scope, "kind": kind, "seen": seen},
        )
        return result.rowcount or 0


async def rematch_all(engine: AsyncEngine) -> int:
    """Recompute corridor tags for every live stored event (after corridors change)."""
    corridors = await load_corridors(engine)
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text("SELECT record_id, segments FROM nh_event WHERE cleared_at IS NULL")
            )
        ).all()
        await conn.execute(text("DELETE FROM nh_event_corridor"))
        pairs = 0
        for record_id, segments in rows:
            probe = RoadEvent(
                "closure", record_id, None, None, None, None, None, None, None,
                segments_from_json(segments),
            )  # fmt: skip
            for c in corridors:
                if event_on_corridor(probe, c):
                    await conn.execute(
                        text(
                            "INSERT INTO nh_event_corridor (record_id, corridor_id) VALUES (:r, :c) "
                            "ON CONFLICT DO NOTHING"
                        ),
                        {"r": record_id, "c": c.id},
                    )
                    pairs += 1
    return pairs


async def save_signs(engine: AsyncEngine, signs: Sequence[VmsSign], seen: datetime) -> int:
    """Upsert signs and record a history row whenever a sign's message changes."""
    changed = 0
    async with engine.begin() as conn:
        for s in signs:
            if not s.sign_id:
                continue
            await conn.execute(
                text(
                    """
                    INSERT INTO nh_sign
                      (sign_id, road, direction, description, lat, lon, working, lines,
                       time_last_set, last_seen)
                    VALUES
                      (:id, :road, :direction, :description, :lat, :lon, :working,
                       CAST(:lines AS jsonb), :set_at, :seen)
                    ON CONFLICT (sign_id) DO UPDATE SET
                      road = EXCLUDED.road, direction = EXCLUDED.direction,
                      description = EXCLUDED.description, lat = EXCLUDED.lat, lon = EXCLUDED.lon,
                      working = EXCLUDED.working, lines = EXCLUDED.lines,
                      time_last_set = EXCLUDED.time_last_set, last_seen = EXCLUDED.last_seen
                    """
                ),
                {
                    "id": s.sign_id,
                    "road": s.road,
                    "direction": s.direction,
                    "description": s.description,
                    "lat": s.lat,
                    "lon": s.lon,
                    "working": s.working,
                    "lines": json.dumps(list(s.lines)),
                    "set_at": s.time_last_set,
                    "seen": seen,
                },
            )
            if s.time_last_set is not None:
                # The feed refreshes "last set" even when the text is unchanged, so
                # only log a row when the lines differ from the sign's latest message.
                result = await conn.execute(
                    text(
                        """
                        INSERT INTO nh_sign_message (sign_id, set_at, lines)
                        SELECT :id, :set_at, CAST(:lines AS jsonb)
                        WHERE NOT EXISTS (
                          SELECT 1 FROM (
                            SELECT lines FROM nh_sign_message
                            WHERE sign_id = :id ORDER BY set_at DESC LIMIT 1
                          ) latest WHERE latest.lines = CAST(:lines AS jsonb)
                        )
                        ON CONFLICT (sign_id, set_at) DO NOTHING
                        """
                    ),
                    {
                        "id": s.sign_id,
                        "set_at": s.time_last_set,
                        "lines": json.dumps(list(s.lines)),
                    },
                )
                changed += result.rowcount or 0
    return changed
