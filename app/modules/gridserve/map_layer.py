"""The map's EV charger layer, drawn by this module and plugged in through the registry ("map.ev")."""

from __future__ import annotations

from typing import Any

from app.map_layers import Point, near_rows, point_feature


async def ev_features(conn: Any, centre: Point | None, radius_km: float) -> list[dict[str, Any]]:
    """Charging sites, each with how many chargers work and are free now: the map's "ev" layer (provides "map.ev")."""
    from app.modules.gridserve.ev import _details
    from app.modules.gridserve.sources.gridserve.normalise import summary

    res = (await near_rows(conn, "SELECT s.id, s.name, s.network, s.postcode, s.lat, s.lon FROM ev_site s WHERE s.lat IS NOT NULL",
                       centre, radius_km, alias="s", order="name")).all()  # fmt: skip
    by, prices = await _details(conn, [r.id for r in res])
    out = []
    for r in res:
        s = summary(by[r.id], prices)
        out.append(point_feature(r.lat, r.lon, {"id": r.id, "name": r.name, "network": r.network, "postcode": r.postcode,
                                         "total": s["total"], "working": s["working"], "free": s["free"],
                                         "max_kw": s["max_kw"], "chademo": "CHAdeMO" in s["connectors"]}))  # fmt: skip
    return out
