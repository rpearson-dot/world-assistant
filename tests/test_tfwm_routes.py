"""TfWM route ids to route numbers: from a GTFS routes.txt, or by the stops the live trips call at."""

from __future__ import annotations

import io
import zipfile

import pytest

from app.integrations.tfwm.routes import match_by_stops, read_routes


def _gtfs() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("agency.txt", "﻿agency_id,agency_name\nOP1,National Express West Midlands\n")
        z.writestr("routes.txt", "route_id,agency_id,route_short_name,route_long_name,route_type\n"
                                 "1153,OP1,907,Sutton Coldfield - Birmingham,3\n2001,OP1,X51,Cannock - Birmingham,3\n")
    return buf.getvalue()


def test_routes_txt_names_the_ids() -> None:
    routes = read_routes(_gtfs())
    assert routes["1153"].short_name == "907" and routes["1153"].agency == "National Express West Midlands"
    assert read_routes(io.BytesIO(_gtfs()))["2001"].short_name == "X51"
    with pytest.raises(zipfile.BadZipFile):
        read_routes(b"<html>sign in</html>")


def _trip(route: str, stops: list[str]) -> dict[str, object]:
    return {"trip": {"route_id": route}, "stops": [{"stop_id": s} for s in stops]}


def test_the_route_calling_at_the_lines_stops_wins() -> None:
    line = {f"43000{i}" for i in range(10)}
    updates = [
        _trip("1153", [f"43000{i}" for i in range(6)]),
        _trip("1153", [f"43000{i}" for i in range(4, 10)]),  # its trips together cover the line
        _trip("9999", ["430003", "430004"] + [f"4300X{i}" for i in range(20)]),  # a long route crossing it
        _trip("", ["430001"]),
    ]
    best = match_by_stops(updates, line)
    assert best[0] == ("1153", 1.0, 10)
    assert best[1][0] == "9999" and best[1][1] < 0.2
    assert match_by_stops(updates, {"nowhere"}) == []


async def test_a_download_streams_to_a_file_and_says_why_it_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app.integrations.tfwm import client as tc

    real = httpx.AsyncClient

    def fake(**kw: object) -> httpx.AsyncClient:
        def answer(req: httpx.Request) -> httpx.Response:
            assert req.url.params["app_key"] == "k"
            return httpx.Response(200, content=_gtfs()) if req.url.path.endswith(".zip") else httpx.Response(403)
        return real(transport=httpx.MockTransport(answer))

    monkeypatch.setattr(tc.httpx, "AsyncClient", fake)
    c = tc.TfwmClient("i", "k", "http://api.tfwm.org.uk")
    out = io.BytesIO()
    assert await c.download("/gtfs/tfwm_gtfs.zip", out) == len(_gtfs())
    out.seek(0)
    assert read_routes(out)["1153"].short_name == "907"
    with pytest.raises(tc.TfwmError, match="HTTP 403"):
        await c.download("/gtfs/nothing", io.BytesIO())
