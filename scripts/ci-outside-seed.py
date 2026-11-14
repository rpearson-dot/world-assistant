"""CI only (scripts/ci-stack.sh): put the example outside module in the database as if you had previewed and
installed it, without GitHub or a signed image (those are covered by tests/test_outside_flow.py), so the real
runner can start it on Docker and the stack check can prove it is sealed, then remove it. Run inside the api
container: python - < scripts/ci-outside-seed.py"""

import asyncio
import hashlib
import json
import secrets

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.keys import write_key
from app.outside import manifest as M

TOML = open("/tmp/wa-module.toml").read()  # noqa: SIM115 - copied in by the stack check
IMAGE = "wa-hello-ci"


async def main() -> None:
    m = M.parse(TOML, "999.0.0", 3)
    token = secrets.token_urlsafe(32)
    write_key(f"x_{m.id}__token", token)
    consent = {"data": {"home_location": "postcode district", "places": True}, "home_devices": {}, "hosts": [],
               "home_assistant_read": [], "home_assistant_write": list(m.home_assistant_write), "notifications": False,
               "controls": [], "limits": m.limits}
    env = {"WA_GATE": "http://wa-modgate:8090", "WA_MODULE": m.id, "WA_MODULE_TOKEN": token,
           "HTTP_PROXY": f"http://{m.id}:{token}@wa-modgate:8091", "HTTPS_PROXY": f"http://{m.id}:{token}@wa-modgate:8091",
           "NO_PROXY": "wa-modgate"}
    engine = create_async_engine(get_settings().database_url)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM xmod WHERE id = :i"), {"i": m.id})
        await conn.execute(text(
            "INSERT INTO xmod (id, repository, tag, version, ref, manifest, consent, state, token_hash, installed_at) "
            "VALUES (:i, :r, 'v1.0.0', :v, :ref, CAST(:m AS jsonb), CAST(:c AS jsonb), 'installing', :h, now())"),
            {"i": m.id, "r": m.repository, "v": m.version, "ref": IMAGE, "m": json.dumps(m.to_json()), "c": json.dumps(consent),
             "h": hashlib.sha256(token.encode()).hexdigest()})
        await conn.execute(text("INSERT INTO xmod_job (module, kind, spec, purpose) VALUES (:i, 'start', CAST(:s AS jsonb), 'install')"),
                           {"i": m.id, "s": json.dumps({"module": m.id, "ref": IMAGE, "version": m.version,
                                                       "memory_mb": m.limits["memory_mb"], "env": env})})
    await engine.dispose()
    print(token)


asyncio.run(main())
