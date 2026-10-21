"""app.smoke against a toy server: it must pass a healthy one and catch each kind of fault."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from app import smoke

TOKEN = "toy-token"
PAGE = "<html><nav>n</nav><script>1</script></html>"


def toy(*, broken_route: bool = False, bad_page: bool = False, open_api: bool = False,
        plain_503: bool = False, json_503: bool = False) -> FastAPI:  # fmt: skip
    app = FastAPI()

    def auth(authorization: str | None = Header(None)) -> None:
        if not open_api and authorization != f"Bearer {TOKEN}":
            raise HTTPException(401, "no")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/readyz")
    def readyz() -> dict:
        return {"ok": True}

    for p in smoke.PAGES:
        body = PAGE.replace("<nav>", "/*BASE_CSS*/<nav>") if (bad_page and p == "/fuel") else PAGE
        app.get(p, response_class=HTMLResponse)(lambda body=body: body)

    @app.get("/v1/status", dependencies=[Depends(auth)])
    def status() -> dict:
        return {"worker_heartbeat": "2026-10-21T10:00:00Z"}

    for i in range(12):
        app.get(f"/v1/thing{i}", dependencies=[Depends(auth)])(lambda i=i: {"i": i})

    if broken_route:
        @app.get("/v1/boom", dependencies=[Depends(auth)])
        def boom() -> dict:
            raise RuntimeError("bug")

    if json_503:
        @app.get("/v1/needs-key", dependencies=[Depends(auth)])
        def needs_key() -> dict:
            raise HTTPException(503, "key not set")

    if plain_503:
        @app.get("/v1/down", dependencies=[Depends(auth)])
        def down() -> JSONResponse:
            return JSONResponse({"error": "no detail field"}, status_code=503)

    places: dict[str, dict] = {}

    @app.put("/v1/places/{pid}", dependencies=[Depends(auth)])
    def put_place(pid: str, body: dict) -> dict:
        places[pid] = body
        return {"id": pid}

    @app.delete("/v1/places/{pid}", dependencies=[Depends(auth)])
    def del_place(pid: str) -> dict:
        places.pop(pid, None)
        return {}

    @app.get("/v1/places", dependencies=[Depends(auth)])
    def list_places() -> list:
        return [{"id": k} for k in places]

    @app.get("/v1/fuel/near", dependencies=[Depends(auth)])
    def fuel_near(place: str | None = None) -> dict:
        return {"stations": []}

    @app.get("/v1/pollen/history", dependencies=[Depends(auth)])
    def pollen_history(place: str | None = None) -> dict:
        return {"days": []}

    @app.get("/v1/events/near", dependencies=[Depends(auth)])
    def events_near(place: str | None = None) -> dict:
        raise HTTPException(503, "no events source is set up")

    @app.get("/v1/pollen/near", dependencies=[Depends(auth)])
    def pollen_near(place: str | None = None) -> dict:
        raise HTTPException(503, "pollen is not set up")

    @app.get("/v1/ask", dependencies=[Depends(auth)])
    def ask() -> list:
        return [{"tool": "pollen"}, {"tool": "fuel_cheapest"}]

    return app


@contextmanager
def serve(app: FastAPI) -> Iterator[str]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        t.join(5)


def go(app: FastAPI, **kw: object) -> tuple[int, str]:
    lines: list[str] = []
    with serve(app) as base:
        code = smoke.run(base, TOKEN, out=lines.append, **kw)  # type: ignore[arg-type]
    return code, "\n".join(lines)


def test_a_healthy_stack_passes_with_and_without_seeding() -> None:
    for seed in (False, True):
        code, text = go(toy(json_503=True), seed=seed)
        assert code == 0, text
    assert "assistant lists its tools" in text and "delete the test place" in text


def test_a_500_anywhere_is_caught() -> None:
    code, text = go(toy(broken_route=True))
    assert code == 1 and "FAIL GET /v1/boom" in text


def test_a_503_without_an_explanation_is_caught() -> None:
    code, text = go(toy(plain_503=True))
    assert code == 1 and "FAIL GET /v1/down" in text


def test_a_page_with_template_leftovers_is_caught() -> None:
    code, text = go(toy(bad_page=True))
    assert code == 1 and "FAIL page /fuel" in text


def test_an_open_api_is_caught() -> None:
    code, text = go(toy(open_api=True))
    assert code == 1 and "FAIL no token is refused" in text


def test_an_unreachable_server_fails_cleanly() -> None:
    lines: list[str] = []
    assert smoke.run("http://127.0.0.1:1", TOKEN, out=lines.append) == 1
    assert any(line.startswith("FAIL") for line in lines)
