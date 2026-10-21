import httpx
import pytest

from app.integrations.nationalhighways.client import (
    CLOSURES,
    NationalHighwaysClient,
    cursor_from_x_next,
)

BASE = "https://api.test"


class CountingLimiter:
    def __init__(self):
        self.acquired = 0

    async def acquire(self, max_wait: float = 30.0) -> None:
        self.acquired += 1


def make_client(handler, limiter=None, **kwargs):
    limiter = limiter or CountingLimiter()
    client = NationalHighwaysClient(
        "secret-key", limiter, BASE, transport=httpx.MockTransport(handler), retry_delay=0, **kwargs
    )
    return client, limiter


async def collect(client, path=CLOSURES, params=None, max_pages=5):
    return [p async for p in client.pages(path, params, max_pages=max_pages)]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("abc123", "abc123"),
        ("https://api.test/roads/v2.0/closures?closureType=planned&pageCursor=42", "42"),
        ("/roads/v2.0/closures?PageCursor=7", "7"),
        ("https://evil.example/steal?pageCursor=zz", "zz"),
        ("https://api.test/roads/v2.0/closures", None),
    ],
)
def test_cursor_from_x_next(value, expected):
    assert cursor_from_x_next(value) == expected


async def test_sends_key_and_asks_for_json():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    client, _ = make_client(handler)
    pages = await collect(client, params={"closureType": "unplanned"})
    assert pages[0].ok
    assert pages[0].json == {"ok": True}
    assert seen["headers"]["ocp-apim-subscription-key"] == "secret-key"
    assert seen["headers"]["x-response-mediaType".lower()] == "application/json"
    assert seen["headers"]["x-data-format"] == "DATEXII"
    assert "closureType=unplanned" in seen["url"]
    assert "secret-key" not in seen["url"]  # key travels in a header, never the URL


async def test_follows_x_next_and_first_request_has_no_cursor():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "pageCursor" not in request.url.params:
            return httpx.Response(200, json={"p": 1}, headers={"x-next": "2"})
        return httpx.Response(200, json={"p": 2})

    client, limiter = make_client(handler)
    pages = await collect(client)
    assert [p.json["p"] for p in pages] == [1, 2]
    assert "pageCursor" not in requests[0].url.params
    assert requests[1].url.params["pageCursor"] == "2"
    assert limiter.acquired == 2  # one limiter slot per page


async def test_never_sends_key_to_a_host_named_in_x_next():
    hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if "pageCursor" not in request.url.params:
            return httpx.Response(
                200, json={}, headers={"x-next": "https://evil.example/x?pageCursor=9"}
            )
        return httpx.Response(200, json={})

    client, _ = make_client(handler)
    await collect(client)
    assert hosts == ["api.test", "api.test"]


async def test_stops_at_max_pages():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={}, headers={"x-next": "more"})

    client, limiter = make_client(handler)
    pages = await collect(client, max_pages=3)
    assert len(pages) == 3
    assert limiter.acquired == 3
    assert pages[-1].x_next == "more"


async def test_429_is_returned_not_retried():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, json={"message": "slow down"})

    client, _ = make_client(handler)
    pages = await collect(client)
    assert len(calls) == 1
    assert pages[0].rate_limited
    assert not pages[0].ok
    assert pages[0].json == {"message": "slow down"}


async def test_server_error_is_retried_once_with_a_fresh_slot():
    statuses = iter([503, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(statuses), json={"ok": True})

    client, limiter = make_client(handler)
    pages = await collect(client)
    assert pages[0].ok
    assert limiter.acquired == 2


async def test_transport_error_becomes_an_error_page_after_retries():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client, limiter = make_client(handler)
    pages = await collect(client)
    assert len(pages) == 1
    assert pages[0].status_code is None
    assert "ConnectError" in pages[0].error
    assert limiter.acquired == 2


async def test_non_json_body_is_kept_as_text():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<xml>not json</xml>")

    client, _ = make_client(handler)
    pages = await collect(client)
    assert pages[0].json is None
    assert pages[0].text == "<xml>not json</xml>"
