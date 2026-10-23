"""Bus, tram and ferry disruptions that local transport authorities publish to BODS, as SIRI-SX.

Not every authority publishes, and BODS does not say which do, so the probe (`disruptions_probe`) comes
first: it shows who publishes and what is near you. Nothing here is stored yet.

The address is tried in order (the API first, then the bulk download), because the API path is not in the
part of BODS's guidance that could be read when this was written. The answer may be XML or a zip of XML.
The parser ignores namespaces and any field it does not know, and refuses a DOCTYPE.
"""

from __future__ import annotations

import io
import math
import xml.etree.ElementTree as ET  # a DOCTYPE is refused before parsing, see parse_situations
import zipfile
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.integrations.bods.client import BodsError

URLS = (
    "https://data.bus-data.dft.gov.uk/api/v1/siri-sx/",
    "https://data.bus-data.dft.gov.uk/disruptions/download/",
)
REASONS = ("MiscellaneousReason", "PersonnelReason", "EquipmentReason", "EnvironmentReason", "UndefinedReason")


@dataclass
class Situation:
    number: str
    publisher: str = ""
    summary: str = ""
    description: str = ""
    reason: str = ""
    planned: bool | None = None
    progress: str = ""
    starts: str = ""
    ends: str = ""
    operators: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    stops: list[str] = field(default_factory=list)
    places: list[tuple[float, float]] = field(default_factory=list)  # (lat, lon) given in the message itself

    def brief(self) -> dict[str, Any]:
        return {"publisher": self.publisher, "summary": self.summary[:120], "reason": self.reason,
                "planned": self.planned, "from": self.starts, "to": self.ends, "lines": self.lines[:8],
                "operators": self.operators[:4], "stops": len(self.stops)}  # fmt: skip


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(e: ET.Element) -> str:
    return (e.text or "").strip()


def _documents(body: bytes) -> list[bytes]:
    if body[:2] != b"PK":
        return [body]
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            return [z.read(i) for i in z.infolist() if i.filename.lower().endswith(".xml")]
    except zipfile.BadZipFile as exc:
        raise BodsError(f"the disruptions archive is damaged: {exc}") from exc


def parse_situations(body: bytes) -> list[Situation]:
    """Every PtSituationElement in a SIRI-SX document (or a zip of them)."""
    out: list[Situation] = []
    for doc in _documents(body):
        head = doc[:4096].lower()
        if b"<!doctype" in head or b"<!entity" in head:
            raise BodsError("the disruptions file carried a DOCTYPE, which it should not")
        try:
            root = ET.fromstring(doc)
        except ET.ParseError as exc:
            raise BodsError(f"the disruptions file was not XML: {exc}") from exc
        out += [_situation(e) for e in root.iter() if _local(e.tag) == "PtSituationElement"]
    return out


def _situation(e: ET.Element) -> Situation:
    s = Situation(number="")
    first: dict[str, str] = {}
    for el in e.iter():
        tag, value = _local(el.tag), _text(el)
        if not value:
            continue
        first.setdefault(tag, value)
        if tag == "OperatorRef" and value not in s.operators:
            s.operators.append(value)
        elif tag in ("PublishedLineName", "LineRef") and value not in s.lines:
            s.lines.append(value)
        elif tag == "StopPointRef" and value not in s.stops:
            s.stops.append(value)
    for loc in (el for el in e.iter() if _local(el.tag) == "Location"):
        kids = {_local(k.tag): _text(k) for k in loc}
        try:
            s.places.append((float(kids["Latitude"]), float(kids["Longitude"])))
        except (KeyError, ValueError):
            pass
    s.number = first.get("SituationNumber", "")
    s.publisher = first.get("ParticipantRef", "")
    s.summary = first.get("Summary", "")
    s.description = first.get("Description", "")
    s.reason = next((first[r] for r in REASONS if r in first), "")
    s.progress = first.get("Progress", "")
    s.starts, s.ends = first.get("StartTime", ""), first.get("EndTime", "")
    planned = first.get("Planned", "").lower()
    s.planned = True if planned == "true" else False if planned == "false" else None
    return s


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat = math.radians((a[0] + b[0]) / 2)
    return math.hypot((a[0] - b[0]) * 111.2, (a[1] - b[1]) * 111.2 * math.cos(lat))


async def fetch(api_key: str, client: httpx.AsyncClient | None = None) -> tuple[str, bytes, list[str]]:
    """The first address that answers with XML or a zip: (url, body, what each earlier try said)."""
    if not api_key:
        raise BodsError("the bods_api_key secret is not set")
    tries: list[str] = []
    owned = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0), follow_redirects=True)
    try:
        for url in URLS:
            try:
                r = await client.get(url, params={"api_key": api_key})
            except httpx.HTTPError as exc:
                tries.append(f"{url}: {type(exc).__name__}")
                continue
            kind = r.headers.get("content-type", "")
            body = r.content
            if r.status_code == 200 and (body[:2] == b"PK" or body.lstrip()[:5] == b"<?xml" or b"<Siri" in body[:2000]):
                return url, body, tries
            tries.append(f"{url}: HTTP {r.status_code}, {kind.split(';')[0] or 'no type'}, {len(body)} bytes")
    finally:
        if owned:
            await client.aclose()
    raise BodsError("no disruptions address answered with data (" + "; ".join(tries) + ")")
