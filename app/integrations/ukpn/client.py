"""UK Power Networks open data portal (Opendatasoft, API v2.1).

    GET {base}/api/explore/v2.1/catalog/datasets?where=search("...")       find a dataset by its title
    GET {base}/api/explore/v2.1/catalog/datasets/{id}/exports/json?where=...  all its records (no paging)

The key (optional for some datasets) goes in an `Authorization: Apikey ...` header, never the URL. Datasets are
found by their published title rather than a hard-wired id, so a renamed id does not break anything; the ids
found are remembered for the life of the process. Records come back flat; a point is {"lon", "lat"} and a shape a
GeoJSON Feature or geometry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Self

import httpx

BASE = "https://ukpowernetworks.opendatasoft.com"
API = "/api/explore/v2.1"

# What this app uses, by the title the portal gives it (first that exists wins).
TITLES: dict[str, tuple[str, ...]] = {
    "faults": ("National Energy Outage API", "Live Faults"),
    "works_open": ("Open Street and Roadworks associated with UK Power Networks' activities",),
    "works_proposed": ("Proposed Street and Roadworks associated with UK Power Networks' activities",),
    "boundary": ("UK Power Networks Boundary", "UK Power Networks Licence Boundaries"),
    "idno": ("Independent Distribution Network Operator areas",),
    "gsp": ("UK Power Networks Licence Area Grid Supply Points Areas",),
    "primary": ("UK Power Networks Primary Substation Distribution Areas",),
    "secondary": ("UK Power Networks Secondary Substation Distribution Areas",),
    "rota": ("Rota Load Disconnection",),
}
# Known ids, tried when the catalogue search finds nothing (the portal's search can be fussy about punctuation).
KNOWN_IDS = {"faults": ("ukpn-national-energy-outage", "ukpn-live-faults"), "works_open": ("ukpn-open-streetworks",)}


class UkpnError(Exception):
    """The portal could not be read, refused the key, or does not have a dataset."""


@dataclass
class Dataset:
    kind: str
    id: str
    title: str
    fields: list[dict[str, Any]] = field(default_factory=list)

    def of_type(self, *types: str) -> list[str]:
        return [f["name"] for f in self.fields if f.get("type") in types]

    @property
    def point(self) -> str | None:
        return next(iter(self.of_type("geo_point_2d")), None)

    @property
    def shape(self) -> str | None:
        return next(iter(self.of_type("geo_shape")), None)


_found: dict[str, Dataset] = {}


class UkpnClient:
    def __init__(self, api_key: str = "", base: str = BASE, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 120.0) -> None:  # fmt: skip
        headers = {"Authorization": f"Apikey {api_key}"} if api_key else {}
        self._http = httpx.AsyncClient(base_url=base, headers=headers, timeout=httpx.Timeout(30.0, read=timeout),
                                       transport=transport, follow_redirects=True)  # fmt: skip

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            r = await self._http.get(API + path, params=params)
        except httpx.HTTPError as exc:
            raise UkpnError(f"the UK Power Networks portal could not be reached: {type(exc).__name__}") from exc
        if r.status_code in (401, 403):
            raise UkpnError("the UK Power Networks portal refused: log in to the portal, make an API key, and save "
                            "it as ukpn_api_key (Settings > Keys)")  # fmt: skip
        if r.status_code == 404:
            raise UkpnError(f"the portal has no {path}")
        if r.status_code != 200:
            raise UkpnError(f"the UK Power Networks portal answered HTTP {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError as exc:
            raise UkpnError("the UK Power Networks portal did not answer with JSON") from exc

    async def _by_id(self, kind: str, dataset_id: str) -> Dataset | None:
        try:
            d = await self._get(f"/catalog/datasets/{dataset_id}")
        except UkpnError:
            return None
        return Dataset(kind, d.get("dataset_id", dataset_id), _title(d), d.get("fields") or [])

    async def dataset(self, kind: str) -> Dataset:
        """The dataset for one of TITLES, found by its title (or a known id)."""
        if kind in _found:
            return _found[kind]
        for title in TITLES[kind]:
            words = title.replace("'", " ").replace('"', " ")
            found = await self._get("/catalog/datasets", {"where": f'search("{words}")', "limit": 20})
            for d in found.get("results") or []:
                if _title(d).strip().lower() == title.lower():
                    _found[kind] = Dataset(kind, d["dataset_id"], _title(d), d.get("fields") or [])
                    return _found[kind]
        for dataset_id in KNOWN_IDS.get(kind, ()):
            if got := await self._by_id(kind, dataset_id):
                _found[kind] = got
                return got
        raise UkpnError(f"the portal has no dataset titled {TITLES[kind][0]!r}")

    async def export(self, ds: Dataset, where: str | None = None, select: str | None = None,
                     limit: int | None = None) -> list[dict[str, Any]]:  # fmt: skip
        params: dict[str, Any] = {}
        if where:
            params["where"] = where
        if select:
            params["select"] = select
        if limit:
            params["limit"] = limit
        rows = await self._get(f"/catalog/datasets/{ds.id}/exports/json", params)
        if not isinstance(rows, list):
            raise UkpnError(f"{ds.title}: the export was not a list")
        return rows

    async def at_point(self, ds: Dataset, lat: float, lon: float) -> list[dict[str, Any]]:
        """The records whose shape contains a point (which substation area, which rota block a place is in)."""
        if not ds.shape:
            raise UkpnError(f"{ds.title} has no shape field to look a place up in")
        return await self.export(ds, where=f"intersects({ds.shape}, geom'POINT({lon} {lat})')", limit=20)

    async def near(self, ds: Dataset, lat: float, lon: float, km: float) -> list[dict[str, Any]]:
        geo = ds.point or ds.shape
        if not geo:
            raise UkpnError(f"{ds.title} has no position field")
        return await self.export(ds, where=f"within_distance({geo}, geom'POINT({lon} {lat})', {km}km)")


def _title(d: dict[str, Any]) -> str:
    metas = d.get("metas") or {}
    default = metas.get("default") if isinstance(metas, dict) else None
    return str((default or {}).get("title") or d.get("title") or d.get("dataset_id") or "")


def forget() -> None:
    """Drop the remembered dataset ids (tests, and a probe that wants to look again)."""
    _found.clear()


def compact(record: dict[str, Any], drop: tuple[str, ...] = ()) -> dict[str, Any]:
    """A record without its (large) shapes, for keeping as `detail`."""
    out = {}
    for k, v in record.items():
        if k in drop or (isinstance(v, dict) and ("geometry" in v or "coordinates" in v)):
            continue
        out[k] = v
    return json.loads(json.dumps(out, default=str))
