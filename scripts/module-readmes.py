"""Write each module folder's README.md (app/modules/<id>/README.md) from its declaration and its files, so the folder
says what the module does, what it needs and provides, and where each part is.

    python scripts/module-readmes.py           rewrite them
    python scripts/module-readmes.py --check   exit 1 if any is out of date (a test runs this)
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.modules import registry

DOCS = {"nh": "trip-conditions.md", "fuel": "data-sources.md", "tfl": "trains.md", "nr": "trains.md", "bods": "live-buses.md",
        "tfwm": "bus.md", "events": "data-sources.md", "pollen": "environment.md", "octopus": "energy.md",
        "floods": "floods.md", "environment": "environment.md", "tflroads": "tfl-roads.md", "tflstations": "stations.md",
        "ukpn": "power.md", "gridserve": "ev.md", "busdisruptions": "bus-disruptions.md", "naptan": "naptan.md",
        "boundary": "boundaries.md", "usrn": "boundaries.md", "codepoint": "postcodes.md", "bus": "bus.md",
        "conditions": "trip-conditions.md", "vehicles": "leaf.md", "vehiclechecks": "data-sources.md",
        "shopping": "shopping.md", "here": "here.md", "crime": "data-sources.md", "food": "data-sources.md",
        "wishlist": "wishlist.md", "bikes": "bikes.md", "outdoors": "outdoors.md", "graph": "graph.md",
        "buslog": "bus-log.md", "ocpp": "ocpp.md", "ovms": "ovms.md", "airstage": "airstage.md", "weather": "weather.md"}  # fmt: skip


def _summary(p: Path) -> str:
    try:
        doc = ast.get_docstring(ast.parse(p.read_text())) or ""
    except SyntaxError:
        return ""
    first = " ".join(doc.strip().split("\n\n")[0].split())
    return first if len(first) <= 160 else first[:157].rsplit(" ", 1)[0] + "..."


def build(module_id: str) -> str:
    m = registry.get(module_id)
    assert m is not None
    folder = ROOT / "app" / "modules" / module_id
    out = [f"# {m.name} (`{m.id}`)", "", f"{m.unlocks}." if not m.unlocks.endswith(".") else m.unlocks, ""]
    if m.note:
        out += [m.note, ""]
    doc = DOCS.get(m.id)
    if doc and (ROOT / "docs" / doc).exists():
        out += [f"How to use it: [docs/{doc}](../../../docs/{doc}).", ""]
    rows = []
    if m.role:
        rows.append(("Role", {"source": "Source: fetches from outside", "processor": "Processor: works from other data",
                              "output": "Output: gets things to you"}.get(m.role, m.role)))  # fmt: skip
    if m.reads:
        rows.append(("Reads", "; ".join(m.reads)))
    if m.gives:
        rows.append(("Gives", "; ".join(m.gives)))
    if m.controls:
        rows.append(("**Controls**", "; ".join(m.controls)))
    if m.keys or m.more_keys:
        rows.append(("Keys", ", ".join(f"`{k}`" for k in (*m.keys, *m.more_keys)) + (" (optional)" if m.key_optional else "")))
    if m.needs:
        rows.append(("Needs", ", ".join(f"[{x}](../{x}/)" for x in m.needs)))
    if m.uses:
        rows.append(("Uses when there", ", ".join(f"[{x}](../{x}/)" for x in m.uses)))
    if m.prefixes:
        rows.append(("Endpoints", ", ".join(f"`{p}`" for p in m.prefixes)))
    if m.worker:
        rows.append(("Scheduled jobs", ", ".join(f"`{j.func.split(':')[-1]}`" for j in m.worker)))
    if m.tools:
        rows.append(("Assistant tools", ", ".join(f"`{t}`" for t in m.tools)))
    if m.provides:
        rows.append(("Plugs into", ", ".join(f"`{p}`" for p in m.provides)))
    if rows:
        out += ["| | |", "|---|---|", *[f"| {a} | {b} |" for a, b in rows], ""]
    out += ["## Files", ""]
    for p in sorted(folder.rglob("*.py")):
        if p.name == "__init__.py" and not p.read_text().strip():
            continue
        rel = p.relative_to(folder).as_posix()
        s = _summary(p)
        out.append(f"- `{rel}`" + (f": {s}" if s else ""))
    out += ["", ("_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its "
            "files; run it after changing either._"), ""]
    return "\n".join(out)


def folders() -> list[str]:
    return sorted(m.id for m in registry.modules() if (ROOT / "app" / "modules" / m.id).is_dir())


if __name__ == "__main__":
    stale = []
    for mid in folders():
        path = ROOT / "app" / "modules" / mid / "README.md"
        text = build(mid)
        if "--check" in sys.argv:
            if not path.exists() or path.read_text() != text:
                stale.append(mid)
        else:
            path.write_text(text)
    if "--check" in sys.argv:
        print("out of date: " + ", ".join(stale) if stale else "all current")
        sys.exit(1 if stale else 0)
    print(f"wrote {len(folders())} module READMEs")
