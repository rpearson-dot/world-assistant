"""Outside modules (/v1/outside, Settings → Outside modules): add one from its GitHub repository, agree what it may
have, keep it up to date, and remove every trace of it.

The steps of an install:
  1. preview: World Assistant reads the newest release (its tag, commit, manifest and image digest) and asks the
     runner to check the image (signature, build attestation, SBOM, vulnerability scan).
  2. You see what it reads, gives and controls, the hosts it reaches, each kind of personal data with a tick box,
     its keys and its limits, and any problem the checks found. Install sends what you ticked.
  3. The runner starts its container. It reaches only the module gate (app/outside/gate.py), with its own token.

Updates install by themselves (automatic updates are on by default) unless the rules in policy.py hold them, which
sends a "needs your attention" notification. Remove is in removal.py.

The runner (scripts/module-runner.py, in the updater container) takes its jobs from /v1/outside/runner.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth import require_token
from app.outside import github, policy
from app.outside import manifest as M
from app.version import VERSION

router = APIRouter(prefix="/v1/outside", tags=["outside modules"], dependencies=[Depends(require_token)])
SDK = 3
GATE = "http://wa-modgate:8090"
PROXY = "wa-modgate:8091"


def wa_version() -> str:
    return VERSION if M.VERSION.fullmatch(VERSION) else "999.0.0"  # a development build accepts any min_wa


# --- the store ------------------------------------------------------------------------------------------------------


async def row(conn: Any, mid: str) -> dict[str, Any] | None:
    r = (await conn.execute(text("SELECT * FROM xmod WHERE id = :i"), {"i": mid})).mappings().first()
    return dict(r) if r else None


async def event(conn: Any, mid: str, kind: str, words: str, data: Any = None) -> None:
    await conn.execute(text("INSERT INTO xmod_event (module, kind, text, data) VALUES (:m, :k, :t, CAST(:d AS jsonb))"),
                       {"m": mid, "k": kind, "t": words[:500], "d": json.dumps(data, default=str) if data is not None else None})  # fmt: skip


async def enqueue(conn: Any, mid: str, kind: str, spec: dict[str, Any], purpose: str = "") -> int:
    return (await conn.execute(text(
        "INSERT INTO xmod_job (module, kind, spec, purpose) VALUES (:m, :k, CAST(:s AS jsonb), :p) RETURNING id"),
        {"m": mid, "k": kind, "s": json.dumps(spec), "p": purpose})).scalar_one()  # fmt: skip


async def save(conn: Any, mid: str, **fields: Any) -> None:
    sets, vals = [], {"i": mid}
    for k, v in fields.items():
        if isinstance(v, (dict, list)) or k in ("manifest", "consent", "settings", "checks", "installed_checks", "candidate", "held", "warnings", "usage"):
            sets.append(f"{k} = CAST(:{k} AS jsonb)")
            vals[k] = json.dumps(v, default=str) if v is not None else None
        else:
            sets.append(f"{k} = :{k}")
            vals[k] = v
    await conn.execute(text(f"UPDATE xmod SET {', '.join(sets)}, updated_at = now() WHERE id = :i"), vals)


def summary(m: M.Manifest) -> dict[str, Any]:
    """What a module asks for, in words, for the permissions screen."""
    return {
        "role": m.role, "reads": list(m.reads), "gives": list(m.gives), "controls": list(m.controls),
        "hosts": [{"host": h, "why": m.purposes.get(h)} for h in m.hosts], "home_devices": list(m.home_devices),
        "data": [{"key": k, "what": M.DATA[k], "precision": v if k == "home_location" else None} for k, v in m.data.items()],
        "home_assistant_read": list(m.home_assistant_read), "home_assistant_write": list(m.home_assistant_write),
        "notifications": m.notifications, "keys": [{"name": k, **v} for k, v in m.keys.items()], "limits": m.limits,
        "settings": [f.__dict__ for f in m.settings], "licence": m.licence,
    }  # fmt: skip


def view(r: dict[str, Any]) -> dict[str, Any]:
    out = {k: r.get(k) for k in ("id", "repository", "tag", "version", "commit", "state", "message", "auto_update", "checks",
                                 "installed_checks", "warnings", "usage", "settings", "consent", "installed_at",
                                 "last_release_at", "checked_at", "updated_at")}  # fmt: skip
    if r.get("manifest"):
        m = M.from_json(r["manifest"])
        out |= {"name": m.name, "asks": summary(m), "problems": policy.problems(m, policy.Checks.from_json(r.get("checks")),
                                                                                  bool(r.get("allow_vulnerable")))}  # fmt: skip
    held = r.get("held")
    if held:
        out["held"] = {"tag": held.get("tag"), "reasons": held.get("reasons"), "since": held.get("since")}
    out["needs_attention"] = bool(held) or bool(r.get("warnings")) or r.get("state") in ("failed", "stopped_limit")
    return out


# --- adding one -------------------------------------------------------------------------------------------------------


class PreviewIn(BaseModel):
    repository: str = Field(max_length=200)


@router.post("/preview")
async def preview(request: Request, body: PreviewIn) -> dict[str, Any]:
    """Read a repository's newest release and its manifest, and start the image checks. Nothing runs yet."""
    try:
        async with httpx.AsyncClient(headers={"User-Agent": "WorldAssistant"}) as client:
            rel = await github.release(client, body.repository)
        m = M.parse(rel["manifest"], wa_version(), SDK)
    except (github.GitHubError, M.ManifestError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"GitHub: {exc}") from exc
    if github.owner_repo(m.repository) != github.owner_repo(body.repository):
        raise HTTPException(422, "its manifest names another repository")
    if rel["lock"]["image"] and rel["lock"]["image"] != m.image:
        raise HTTPException(422, "its release lock names another image than its manifest")
    from app.modules import registry

    if registry.get(m.id) is not None:
        raise HTTPException(409, f"{m.id} is the id of a built-in module: the module must use another id")
    ref = f"{m.image}@{rel['lock']['digest']}"
    async with request.app.state.engine.begin() as conn:
        have = await row(conn, m.id)
        if have and have["state"] not in ("previewed", "refused", "checking"):
            raise HTTPException(409, f"{m.id} is already installed")
        if have:
            await conn.execute(text("DELETE FROM xmod WHERE id = :i"), {"i": m.id})
        await conn.execute(text(
            "INSERT INTO xmod (id, repository, tag, version, commit, ref, manifest, state, last_release_at) "
            "VALUES (:i, :r, :t, :v, :c, :ref, CAST(:m AS jsonb), 'checking', :at)"),
            {"i": m.id, "r": m.repository, "t": rel["tag"], "v": m.version, "c": rel["commit"], "ref": ref,
             "m": json.dumps(m.to_json()), "at": rel["published_at"]})  # fmt: skip
        await save(conn, m.id, candidate={"licence": rel["licence"]})
        await enqueue(conn, m.id, "check", {"ref": ref, "repository": m.repository, "tag": rel["tag"]}, "preview")
        await event(conn, m.id, "preview", f"read {rel['tag']} from {m.repository}; checking its image")
        return view(await row(conn, m.id) or {})


@router.get("")
async def list_outside(request: Request) -> dict[str, Any]:
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text("SELECT * FROM xmod ORDER BY id"))).mappings().all()
    return {"modules": [view(dict(r)) for r in rows], "data_kinds": M.DATA}


@router.get("/{mid}")
async def get_one(request: Request, mid: str) -> dict[str, Any]:
    async with request.app.state.engine.connect() as conn:
        r = await row(conn, mid)
        if r is None:
            raise HTTPException(404, "no such module")
        ev = (await conn.execute(text("SELECT at, kind, text FROM xmod_event WHERE module = :m ORDER BY id DESC LIMIT 30"),
                                 {"m": mid})).mappings().all()  # fmt: skip
    return {**view(r), "events": [dict(e) for e in ev]}


class ConsentIn(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict)  # home_location: precision (or "none"); places: true/false ...
    home_devices: dict[str, str] = Field(default_factory=dict)  # what it is: its address on the home network
    allow_vulnerable: bool = False  # install despite high or critical findings, for this version only


def agreed(m: M.Manifest, body: ConsentIn) -> dict[str, Any]:
    """What you agreed, checked against what it asked: never more than it asked, a location never more precise."""
    import ipaddress

    data: dict[str, Any] = {}
    for k, asked in m.data.items():
        give = body.data.get(k)
        if k == "home_location":
            if give in M.PRECISION and give != "none" and M.PRECISION.index(give) <= M.PRECISION.index(asked):
                data[k] = give
        elif give is True:
            data[k] = True
    devices = {}
    for what in m.home_devices:
        addr = (body.home_devices.get(what) or "").strip()
        if not addr:
            continue
        try:
            ip = ipaddress.ip_address(addr.split(":")[0])
        except ValueError as exc:
            raise HTTPException(422, f"{what}: an IP address on your home network, such as 10.0.2.50") from exc
        if not ip.is_private or ip.is_loopback:
            raise HTTPException(422, f"{what}: only an address on your home network")
        devices[what] = addr
    return {"data": data, "home_devices": devices, "hosts": list(m.hosts), "home_assistant_read": list(m.home_assistant_read),
            "home_assistant_write": list(m.home_assistant_write), "notifications": m.notifications,
            "controls": list(m.controls), "limits": m.limits, "at": datetime.now(UTC).isoformat()}  # fmt: skip


async def start_job(conn: Any, r: dict[str, Any], m: M.Manifest, ref: str, purpose: str) -> None:
    from app import secretstore
    from app.config import SECRETS_DIR
    from app.keys import write_key

    token_name = f"x_{m.id}__token"
    token = secretstore.read_secret(token_name, SECRETS_DIR)
    if not token:
        token = secrets.token_urlsafe(32)
        write_key(token_name, token)
        secretstore.clear_cache()
    await save(conn, m.id, token_hash=hashlib.sha256(token.encode()).hexdigest())
    env = {"WA_GATE": GATE, "WA_MODULE": m.id, "WA_MODULE_TOKEN": token, "HTTP_PROXY": f"http://{m.id}:{token}@{PROXY}",
           "HTTPS_PROXY": f"http://{m.id}:{token}@{PROXY}", "NO_PROXY": "wa-modgate"}  # fmt: skip
    for k in m.keys:
        v = secretstore.read_secret(f"x_{m.id}_{k}", SECRETS_DIR)
        if v:
            env[f"WA_KEY_{k.upper()}"] = v
    await enqueue(conn, m.id, "start", {"module": m.id, "ref": ref, "version": m.version, "memory_mb": m.limits["memory_mb"], "env": env},
                  purpose)  # fmt: skip


@router.post("/{mid}/install")
async def install(request: Request, mid: str, body: ConsentIn) -> dict[str, Any]:
    """Install a previewed module with what you agreed to share. Refused while its checks have a problem (high or
    critical vulnerabilities can be accepted for this version with allow_vulnerable)."""
    async with request.app.state.engine.begin() as conn:
        r = await row(conn, mid)
        if r is None or r["state"] not in ("previewed", "refused"):
            raise HTTPException(409, "preview it first, and wait for its checks")
        m = M.from_json(r["manifest"])
        bad = policy.problems(m, policy.Checks.from_json(r["checks"]), body.allow_vulnerable)
        if bad:
            raise HTTPException(409, "not installed: " + "; ".join(bad))
        consent = agreed(m, body)
        await save(conn, mid, consent=consent, state="installing", allow_vulnerable=body.allow_vulnerable,
                   installed_checks=r["checks"], message="starting its container")  # fmt: skip
        await start_job(conn, r, m, r["ref"], "install")
        shared = [M.DATA[k] for k in consent["data"]] + [f"{k} at {v}" for k, v in consent["home_devices"].items()]
        await event(conn, mid, "install", f"installing {r['tag']}; you agreed to share: " + (", ".join(shared) or "nothing personal"), consent)
        return view(await row(conn, mid) or {})


# --- running it ---------------------------------------------------------------------------------------------------


class AutoIn(BaseModel):
    on: bool


@router.put("/{mid}/auto-update")
async def auto_update(request: Request, mid: str, body: AutoIn) -> dict[str, Any]:
    async with request.app.state.engine.begin() as conn:
        if await row(conn, mid) is None:
            raise HTTPException(404, "no such module")
        await save(conn, mid, auto_update=body.on)
        await event(conn, mid, "auto-update", "automatic updates " + ("on" if body.on else "off"))
        return view(await row(conn, mid) or {})


@router.put("/{mid}/settings")
async def put_settings(request: Request, mid: str, body: dict[str, Any]) -> dict[str, Any]:
    """Its settings, checked against the fields its manifest declares (World Assistant draws them; the module never
    adds its own)."""
    async with request.app.state.engine.begin() as conn:
        r = await row(conn, mid)
        if r is None:
            raise HTTPException(404, "no such module")
        m, out = M.from_json(r["manifest"]), {}
        for f in m.settings:
            if f.key not in body:
                continue
            v = body[f.key]
            if f.type == "number" and not isinstance(v, (int, float)):
                raise HTTPException(422, f"{f.label}: a number")
            if f.type == "boolean" and not isinstance(v, bool):
                raise HTTPException(422, f"{f.label}: on or off")
            if f.type == "choice" and v not in f.choices:
                raise HTTPException(422, f"{f.label}: one of {', '.join(f.choices)}")
            if f.type == "text" and (not isinstance(v, str) or len(v) > 500):
                raise HTTPException(422, f"{f.label}: text up to 500 characters")
            out[f.key] = v
        await save(conn, mid, settings=out)
        return {"settings": out}


class KeyIn(BaseModel):
    value: str = Field(min_length=1, max_length=4000)


@router.put("/{mid}/keys/{name}")
async def put_key(request: Request, mid: str, name: str, body: KeyIn) -> dict[str, Any]:
    """One of the keys its manifest declares; given to it when it next starts (it is restarted now)."""
    from app.keys import write_key

    async with request.app.state.engine.begin() as conn:
        r = await row(conn, mid)
        if r is None or name not in (r["manifest"].get("keys") or {}):
            raise HTTPException(404, "not a key this module declares")
        write_key(f"x_{mid}_{name}", body.value)
        from app import secretstore

        secretstore.clear_cache()
        if r["state"] == "running":
            await start_job(conn, r, M.from_json(r["manifest"]), r["ref"], "restart")
        await event(conn, mid, "key", f"key {name} set")
    return {"name": name, "set": True}


@router.get("/keys/all")
async def all_keys(request: Request) -> dict[str, Any]:
    """Every key the outside modules declare and whether each is set (for the Keys page)."""
    from app import secretstore
    from app.config import SECRETS_DIR

    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text("SELECT id, manifest FROM xmod WHERE state NOT IN ('checking', 'previewed', 'refused')"))).all()
    out = []
    for mid, man in rows:
        for k, v in (man.get("keys") or {}).items():
            out.append({"module": mid, "name": k, "label": v.get("label"), "required": v.get("required"),
                        "set": bool(secretstore.read_secret(f"x_{mid}_{k}", SECRETS_DIR))})  # fmt: skip
    return {"keys": out}


class ApproveIn(ConsentIn):
    pass


@router.post("/{mid}/approve")
async def approve(request: Request, mid: str, body: ApproveIn) -> dict[str, Any]:
    """Install a held update, agreeing to anything more it asks for (what you tick)."""
    async with request.app.state.engine.begin() as conn:
        r = await row(conn, mid)
        if r is None or not r.get("held"):
            raise HTTPException(409, "no update is waiting")
        held = r["held"]
        new = M.from_json(held["manifest"])
        bad = policy.problems(new, policy.Checks.from_json(held.get("checks")), body.allow_vulnerable)
        if bad:
            raise HTTPException(409, "not installed: " + "; ".join(bad))
        await save(conn, mid, candidate={"tag": held["tag"], "commit": held["commit"], "ref": held["ref"], "manifest": held["manifest"],
                                          "checks": held.get("checks"), "consent": agreed(new, body), "previous_ref": r["ref"]},
                   held=None, state="updating", message=f"installing {held['tag']}")  # fmt: skip
        await start_job(conn, r, new, held["ref"], "update")
        await event(conn, mid, "approve", f"you approved {held['tag']}")
        return view(await row(conn, mid) or {})


# --- the runner's queue -----------------------------------------------------------------------------------------------


@router.get("/runner/next")
async def runner_next(request: Request) -> dict[str, Any] | None:
    """The oldest waiting job for the runner (it marks it taken)."""
    async with request.app.state.engine.begin() as conn:
        r = (await conn.execute(text(
            "UPDATE xmod_job SET state = 'running', taken_at = now() WHERE id = (SELECT id FROM xmod_job WHERE state = 'waiting' "
            "OR (state = 'running' AND taken_at < now() - interval '30 minutes') ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "RETURNING id, module, kind, spec"))).mappings().first()  # fmt: skip
    return dict(r) if r else None


@router.post("/runner/{jid}/done")
async def runner_done(request: Request, jid: int, result: dict[str, Any]) -> dict[str, Any]:
    async with request.app.state.engine.begin() as conn:
        job = (await conn.execute(text(
            "UPDATE xmod_job SET state = 'done', done_at = now(), result = CAST(:r AS jsonb) WHERE id = :i AND state = 'running' "
            "RETURNING module, kind, spec, purpose"), {"i": jid, "r": json.dumps(result)})).mappings().first()  # fmt: skip
        if job is None:
            raise HTTPException(404, "no such running job")
        await handle(conn, dict(job), result)
        await conn.execute(text("DELETE FROM xmod_job WHERE state = 'done' AND done_at < now() - interval '30 days'"))
    return {"ok": True}


async def handle(conn: Any, job: dict[str, Any], res: dict[str, Any]) -> None:
    mid, kind, purpose = job["module"], job["kind"], job["purpose"] or ""
    r = await row(conn, mid)
    if kind == "remove":
        from app.outside import removal

        await removal.finish(conn, mid, res)
        return
    if r is None:
        return
    m = M.from_json(r["manifest"])
    if kind == "check" and purpose == "preview":
        checks = {**res, "licence": (r.get("candidate") or {}).get("licence")}
        bad = policy.problems(m, policy.Checks.from_json(checks))
        await save(conn, mid, checks=checks, checked_at=datetime.now(UTC), state="previewed",
                   message="checked: " + ("; ".join(bad) if bad else "nothing found"))  # fmt: skip
        await event(conn, mid, "checked", "image checked" + (": " + "; ".join(bad) if bad else ": signed, attested, no high or critical findings"), res)
    elif kind == "check" and purpose == "rescan":
        await save(conn, mid, installed_checks={**(r.get("installed_checks") or {}), **{k: res.get(k) for k in ("high", "critical", "found")}},
                   checked_at=datetime.now(UTC))  # fmt: skip
        await refresh_warnings(conn, mid)
    elif kind == "check" and purpose == "update":
        cand = r.get("candidate") or {}
        checks = {**res, "licence": cand.get("licence")}
        new = M.from_json(cand["manifest"])
        verdict, reasons = policy.update(m, new, policy.Checks.from_json(checks), bool(r["auto_update"]))
        if verdict == "install":
            await save(conn, mid, candidate={**cand, "checks": checks, "consent": {**(r["consent"] or {}), "limits": new.limits},
                                              "previous_ref": r["ref"]}, state="updating", message=f"installing {cand['tag']}")  # fmt: skip
            await start_job(conn, r, new, cand["ref"], "update")
            await event(conn, mid, "update", f"{cand['tag']} passed every check: installing it")
        else:
            await save(conn, mid, held={**cand, "checks": checks, "reasons": reasons, "since": datetime.now(UTC).isoformat()}, candidate=None)
            await event(conn, mid, "held", f"{cand['tag']} is waiting for you: " + "; ".join(reasons), reasons)
            await attention(conn, mid, m.name, f"An update ({cand['tag']}) is waiting for you: " + "; ".join(reasons), f"held:{cand['tag']}")
        return
    elif kind == "start":
        ok = bool(res.get("running")) and not res.get("error")
        if purpose == "update":
            cand = r.get("candidate") or {}
            if ok:
                await save(conn, mid, tag=cand["tag"], commit=cand["commit"], ref=cand["ref"], manifest=cand["manifest"],
                           version=cand["manifest"]["version"], consent=cand.get("consent") or r["consent"],
                           installed_checks=cand.get("checks"), candidate=None, state="running", message=None)  # fmt: skip
                await event(conn, mid, "updated", f"now running {cand['tag']}")
                if cand.get("previous_ref") and cand["previous_ref"] != cand["ref"]:
                    await enqueue(conn, mid, "remove_image", {"refs": [cand["previous_ref"]]}, "tidy")
            else:
                await save(conn, mid, candidate=None, state="starting", message=f"{cand.get('tag')} did not start: going back")
                await start_job(conn, r, m, r["ref"], "rollback")
                await event(conn, mid, "rollback", f"{cand.get('tag')} did not start ({res.get('error') or 'not running'}): going back to {r['tag']}")
                await attention(conn, mid, m.name, f"Its update to {cand.get('tag')} did not start, so it went back to {r['tag']}.", f"rollback:{cand.get('tag')}")
        else:
            await save(conn, mid, state="running" if ok else "failed", message=None if ok else (res.get("error") or "it did not start"),
                       installed_at=r["installed_at"] or datetime.now(UTC))  # fmt: skip
            await event(conn, mid, "started" if ok else "failed", "running" if ok else f"did not start: {res.get('error')}")
    elif kind == "stop":
        await event(conn, mid, "stopped", "stopped")
    elif kind == "usage":
        await save(conn, mid, usage={**res, "at": datetime.now(UTC).isoformat()})
        lim = m.limits
        if res.get("storage_mb") is not None and res["storage_mb"] > lim["storage_mb"]:
            await save(conn, mid, state="stopped_limit", message=f"stopped: it uses {res['storage_mb']} MB of storage, over its {lim['storage_mb']} MB")
            await enqueue(conn, mid, "stop", {"module": mid}, "limit")
            await attention(conn, mid, m.name, f"Stopped: its storage ({res['storage_mb']} MB) went over its limit of {lim['storage_mb']} MB.", "limit")


async def refresh_warnings(conn: Any, mid: str) -> None:
    r = await row(conn, mid)
    if r is None:
        return
    held = r.get("held") or {}
    since = datetime.fromisoformat(held["since"]) if held.get("since") else None
    w = policy.warnings({"held_since": since, "installed_checks": r.get("installed_checks"), "last_release_at": r.get("last_release_at")},
                        datetime.now(UTC))  # fmt: skip
    if w != (r.get("warnings") or []):
        await save(conn, mid, warnings=w)
        new = [x for x in w if x not in (r.get("warnings") or [])]
        if new:
            await attention(conn, mid, M.from_json(r["manifest"]).name, "Falling behind: " + "; ".join(new), "warn:" + hashlib.sha1("|".join(new).encode()).hexdigest()[:8])


async def attention(conn: Any, mid: str, name: str, body: str, key: str) -> None:
    """A "needs your attention" notification (Settings → Notifications: outside modules)."""
    from app import notify

    engine = conn.engine
    try:
        await notify.send(engine, "modules", f"x:{mid}:{key}", f"{name} needs your attention", body, click="/settings#outside")
    except Exception:  # noqa: BLE001, S110 - the badge on its row still shows it
        pass


# --- keeping up to date (worker jobs) -----------------------------------------------------------------------------------


async def outside_daily(ctx: dict[str, Any]) -> Any:
    """Each day: a newer release for each installed module is read and checked (and installed by itself unless held);
    on Sundays, every installed image is scanned again for new vulnerabilities; warnings refreshed."""
    from app.jobs import tracked

    engine = ctx["engine"]

    async def work() -> dict[str, Any]:
        async with engine.connect() as conn:
            rows = [dict(r) for r in (await conn.execute(text(
                "SELECT * FROM xmod WHERE state IN ('running', 'stopped_limit')"))).mappings().all()]  # fmt: skip
        if not rows:
            return {"not_set_up": "No outside modules installed"}
        out: dict[str, Any] = {"checked": 0, "newer": [], "errors": []}
        async with httpx.AsyncClient(headers={"User-Agent": "WorldAssistant"}) as client:
            for r in rows:
                try:
                    rel = await github.release(client, r["repository"])
                    new = M.parse(rel["manifest"], wa_version(), SDK)
                except (github.GitHubError, M.ManifestError, httpx.HTTPError) as exc:
                    out["errors"].append(f"{r['id']}: {exc}"[:200])
                    continue
                out["checked"] += 1
                async with engine.begin() as conn:
                    await save(conn, r["id"], last_release_at=rel["published_at"])
                    held_tag = (r.get("held") or {}).get("tag")
                    if rel["tag"] != r["tag"] and rel["tag"] != held_tag and not r.get("candidate"):
                        ref = f"{new.image}@{rel['lock']['digest']}"
                        await save(conn, r["id"], candidate={"tag": rel["tag"], "commit": rel["commit"], "ref": ref,
                                                             "manifest": new.to_json(), "licence": rel["licence"]})  # fmt: skip
                        await enqueue(conn, r["id"], "check", {"ref": ref, "repository": r["repository"], "tag": rel["tag"]}, "update")
                        await event(conn, r["id"], "release", f"{rel['tag']} is out: checking it")
                        out["newer"].append(f"{r['id']} {rel['tag']}")
                    if datetime.now(UTC).weekday() == 6:
                        await enqueue(conn, r["id"], "check", {"ref": r["ref"], "repository": r["repository"], "tag": r["tag"]}, "rescan")
                    await refresh_warnings(conn, r["id"])
        return out

    return await tracked(engine, "outside_daily", work)


async def outside_usage(ctx: dict[str, Any]) -> Any:
    """Each hour: each running module's storage and memory, and a stop for one over its limit."""
    from app.jobs import tracked

    engine = ctx["engine"]

    async def work() -> dict[str, Any]:
        async with engine.begin() as conn:
            ids = (await conn.execute(text("SELECT id FROM xmod WHERE state = 'running'"))).scalars().all()
            for mid in ids:
                await enqueue(conn, mid, "usage", {"module": mid}, "hourly")
        return {"modules": len(ids)} if ids else {"not_set_up": "No outside modules running"}

    return await tracked(engine, "outside_usage", work)


@router.post("/{mid}/remove")
async def remove(request: Request, mid: str) -> dict[str, Any]:
    """Remove every trace of it: its container, storage and images (the runner), then its keys, token, Home
    Assistant entities, settings, history and its row. GET /v1/outside/removed/{mid} has the report."""
    from app.outside import removal

    async with request.app.state.engine.begin() as conn:
        r = await row(conn, mid)
        if r is None:
            raise HTTPException(404, "no such module")
        return await removal.begin(conn, r)


@router.get("/removed/{mid}")
async def removed(request: Request, mid: str) -> dict[str, Any]:
    async with request.app.state.engine.connect() as conn:
        r = (await conn.execute(text(
            "SELECT at, data FROM xmod_event WHERE module = :m AND kind = 'removed' ORDER BY id DESC LIMIT 1"), {"m": mid})).first()  # fmt: skip
        busy = await row(conn, mid)
    if r is None:
        return {"state": "removing" if busy else "unknown"}
    return {"state": "removed", "at": r[0], "report": r[1]}

