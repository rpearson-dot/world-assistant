"""Corridor helpers and a command line for managing them.

The draft corridor is a stand-in until corridors are derived from routes between
your saved places. It strings together the stored VMS signs along M25 (west side,
clockwise), M40 and M42 northbound and sorts them south to north, which follows
those three roads closely enough to tag events while we build the real thing.

    python -m app.integrations.nationalhighways.corridors list
    python -m app.integrations.nationalhighways.corridors seed-draft
    python -m app.integrations.nationalhighways.corridors rematch
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.integrations.nationalhighways.match import Corridor
from app.integrations.nationalhighways.store import load_corridors, rematch_all, upsert_corridor

DRAFT_ID = "draft-m25-m40-m42"
# (road, direction, lat range, lon range): a rough box that picks one carriageway
# of each stretch from M25 J10 to M25 J16, M40 J1 to J16, and M42 from J3A.
DRAFT_PARTS = [
    ("M25", "clockwise", (51.30, 51.585), (-0.60, -0.40)),
    ("M40", "northBound", (51.58, 52.34), (-1.90, -0.45)),
    ("M42", "northBound", (52.35, 52.52), (-1.85, -1.65)),
]


async def draft_centreline(engine: AsyncEngine) -> tuple[tuple[float, float], ...]:
    points: list[tuple[float, float]] = []
    async with engine.connect() as conn:
        for road, direction, (lat0, lat1), (lon0, lon1) in DRAFT_PARTS:
            rows = await conn.execute(
                text(
                    """
                    SELECT lat, lon FROM nh_sign
                    WHERE road = :road AND direction = :direction AND lat IS NOT NULL
                      AND lat BETWEEN :lat0 AND :lat1 AND lon BETWEEN :lon0 AND :lon1
                    ORDER BY lat
                    """
                ),
                {"road": road, "direction": direction, "lat0": lat0, "lat1": lat1,
                 "lon0": lon0, "lon1": lon1},
            )  # fmt: skip
            points.extend((r[0], r[1]) for r in rows)
    return tuple(points)


async def seed_draft(engine: AsyncEngine) -> int:
    """Create or refresh the draft corridor. Returns the number of centreline points."""
    centreline = await draft_centreline(engine)
    if len(centreline) < 10:
        return len(centreline)
    corridor = Corridor(
        DRAFT_ID, "Draft: M25 J10 to M42 via M40", centreline, 1000.0,
        frozenset({"M25", "M40", "M42"}),
    )  # fmt: skip
    await upsert_corridor(engine, corridor, source="draft-signs")
    return len(centreline)


async def _cli(args: argparse.Namespace) -> int:
    engine = create_async_engine(get_settings().database_url)
    try:
        if args.command == "list":
            for c in await load_corridors(engine, only_enabled=False):
                print(f"{c.id:<24} {len(c.centreline):>4} points  buffer={c.buffer_m:g} m  {c.name}")
            return 0
        if args.command == "seed-draft":
            count = await seed_draft(engine)
            if count < 10:
                print(f"Only {count} signs stored on those roads. Run a live poll first.")
                return 1
            print(f"Draft corridor saved with {count} points; re-tagging events...")
        pairs = await rematch_all(engine)
        print(f"{pairs} event/corridor matches stored.")
        return 0
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage National Highways corridors.")
    parser.add_argument("command", choices=["list", "seed-draft", "rematch"])
    return asyncio.run(_cli(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
