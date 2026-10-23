"""BODS disruptions (SIRI-SX): parsing, zips, refusing a DOCTYPE, and which address answered."""

from __future__ import annotations

import io
import zipfile

import httpx
import pytest

from app.integrations.bods.client import BodsError
from app.integrations.bods.disruptions import fetch, km, parse_situations

SX = b"""<?xml version="1.0" encoding="UTF-8"?>
<Siri xmlns="http://www.siri.org.uk/siri" version="2.0"><ServiceDelivery>
<SituationExchangeDelivery><Situations>
 <PtSituationElement>
  <CreationTime>2026-10-20T09:00:00Z</CreationTime>
  <ParticipantRef>Leicestershire County Council</ParticipantRef>
  <SituationNumber>abc-1</SituationNumber>
  <Progress>open</Progress>
  <ValidityPeriod><StartTime>2026-10-20T00:00:00Z</StartTime><EndTime>2026-11-01T23:59:00Z</EndTime></ValidityPeriod>
  <MiscellaneousReason>roadworks</MiscellaneousReason>
  <Planned>true</Planned>
  <Summary>Measham Road closed</Summary>
  <Description>Buses divert via the A444.</Description>
  <Consequences><Consequence><Affects>
   <Operators><AffectedOperator><OperatorRef>DIAM</OperatorRef></AffectedOperator></Operators>
   <Networks><AffectedNetwork><AffectedLine><LineRef>19A</LineRef><PublishedLineName>19A</PublishedLineName></AffectedLine></AffectedNetwork></Networks>
   <StopPoints>
    <AffectedStopPoint><StopPointRef>2600NWL1001</StopPointRef><Location><Longitude>-1.536</Longitude><Latitude>52.684</Latitude></Location></AffectedStopPoint>
    <AffectedStopPoint><StopPointRef>2600NWL1002</StopPointRef></AffectedStopPoint>
   </StopPoints>
  </Affects></Consequence></Consequences>
 </PtSituationElement>
 <PtSituationElement><ParticipantRef>Somewhere Else</ParticipantRef><SituationNumber>x</SituationNumber>
  <EquipmentReason>signalFailure</EquipmentReason><Planned>false</Planned></PtSituationElement>
</Situations></SituationExchangeDelivery></ServiceDelivery></Siri>"""


def test_a_situation_is_read() -> None:
    first, second = parse_situations(SX)
    assert (first.number, first.publisher, first.summary, first.reason, first.planned) == (
        "abc-1", "Leicestershire County Council", "Measham Road closed", "roadworks", True)
    assert first.lines == ["19A"] and first.operators == ["DIAM"]
    assert first.stops == ["2600NWL1001", "2600NWL1002"] and first.places == [(52.684, -1.536)]
    assert (first.starts, first.ends) == ("2026-10-20T00:00:00Z", "2026-11-01T23:59:00Z")
    assert (second.reason, second.planned, second.stops) == ("signalFailure", False, [])
    assert km((52.684, -1.536), (52.69, -1.55)) < 2


def test_a_zip_of_documents_and_a_doctype() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.xml", SX)
        z.writestr("b.xml", SX)
        z.writestr("readme.txt", b"hi")
    assert len(parse_situations(buf.getvalue())) == 4
    with pytest.raises(BodsError, match="DOCTYPE"):
        parse_situations(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><Siri/>')
    with pytest.raises(BodsError, match="not XML"):
        parse_situations(b"<Siri><oops></Siri>")


async def test_the_first_address_with_data_is_used_and_the_others_reported() -> None:
    seen: list[str] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        assert req.url.params["api_key"] == "k"
        if req.url.path.startswith("/api/"):
            return httpx.Response(404, headers={"content-type": "text/html"}, content=b"<html>no</html>")
        return httpx.Response(200, content=SX)

    url, body, tries = await fetch("k", httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    assert url.endswith("/disruptions/download/") and body == SX
    assert tries == ["https://data.bus-data.dft.gov.uk/api/v1/siri-sx/: HTTP 404, text/html, 15 bytes"]

    def nothing(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>sign in</html>")

    with pytest.raises(BodsError, match="no disruptions address answered"):
        await fetch("k", httpx.AsyncClient(transport=httpx.MockTransport(nothing)))
    with pytest.raises(BodsError, match="not set"):
        await fetch("")
