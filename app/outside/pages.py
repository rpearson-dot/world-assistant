"""An outside module's own pages, shown inside World Assistant: /x/<id> is World Assistant's page with the module's
page in a sandboxed frame (scripts and forms only: it cannot reach World Assistant's pages, storage or keys), and
/x/<id>/frame/... fetches the module's page through the module gate. Only from the home network or the tailnet."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.localnet import private_peer
from app.pagestyle import themed

page_router = APIRouter()
CSP = ("sandbox allow-scripts allow-forms; default-src 'self' 'unsafe-inline' data:; connect-src 'self'; "
       "frame-ancestors 'self'; form-action 'self'; base-uri 'none'")  # fmt: skip

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Assistant outside module</title>
<style>
/*BASE_CSS*/
iframe { width: 100%; height: 75vh; border: 1px solid var(--line); border-radius: 12px; background: var(--card); }
</style></head><body><main>
<!--NAV-->
<h1 id="h"></h1>
<p class="dim small">An outside module's own page, shown in a sealed frame: it cannot read World Assistant's pages or keys.
<a href="/settings#outside">Manage it in Settings</a>.</p>
<iframe id="f" sandbox="allow-scripts allow-forms" referrerpolicy="no-referrer"></iframe>
<script>
const parts = location.pathname.split('/'), id = parts[2];
document.getElementById('h').textContent = id;
document.getElementById('f').src = '/x/' + encodeURIComponent(id) + '/frame/';
</script></main></body></html>"""


async def _running(request: Request, mid: str) -> None:
    if not private_peer(request):
        raise HTTPException(403, "outside modules' pages open from the home network or the tailnet")
    async with request.app.state.engine.connect() as conn:
        ok = (await conn.execute(text("SELECT 1 FROM xmod WHERE id = :i AND state = 'running'"), {"i": mid})).first()
    if not ok:
        raise HTTPException(404, "no such running outside module")


@page_router.get("/x/{mid}", response_class=HTMLResponse, include_in_schema=False)
async def module_page(request: Request, mid: str) -> str:
    await _running(request, mid)
    return themed(PAGE, "settings", request)


@page_router.api_route("/x/{mid}/frame/{path:path}", methods=["GET", "POST"], include_in_schema=False)
async def frame(request: Request, mid: str, path: str) -> Response:
    import httpx

    from app.outside.gate import gate_key

    await _running(request, mid)
    try:
        async with httpx.AsyncClient(timeout=25) as c:
            r = await c.request(request.method, f"http://wa-modgate:8090/pages/{mid}/{path}", params=dict(request.query_params),
                                content=await request.body(), headers={"x-wa-gate": gate_key(),
                                                                       "content-type": request.headers.get("content-type", "")})  # fmt: skip
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"the module gate did not answer: {exc}") from exc
    return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type"),
                    headers={"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})  # fmt: skip
