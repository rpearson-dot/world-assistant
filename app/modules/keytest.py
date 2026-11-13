"""Does this module's key work? A small live call for the sources where one is cheap and clear.

Each module brings its own test (provides "key.test": an async function of the settings returning (ok, message)).
Everything else answers "no test for this one yet" rather than guessing. A test never changes anything and never
returns a key.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings


async def run(module_id: str, s: Settings) -> dict[str, Any]:
    from app.modules import registry

    m = registry.get(module_id)
    ref = m.provides.get("key.test") if m else None
    test = registry.import_string(ref) if ref else None
    if test is None:
        return {"ok": None, "message": "There is no live test for this one yet; check its row on the Setup list."}
    ok, message = await test(s)
    return {"ok": ok, "message": message}
