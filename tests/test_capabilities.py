"""Every assistant tool documents itself as an App Intent — a title, a one-line summary, sample phrases and a kind
(query or action) — so the capability manifest (/v1/capabilities) and an iPhone Siri bridge have what they need.
Adding a tool without that metadata fails here. The manifest also builds and has a stable version."""

from __future__ import annotations

import re

from app import capabilities
from app.assistant import TOOLS

# slots a phrase may use beyond the tool's own schema: the shared vocabularies the app lists live (places, lists, …)
_VOCAB = set(capabilities._VALUES_FROM)


def test_every_tool_has_app_intent_metadata() -> None:
    missing = []
    for name, t in sorted(TOOLS.items()):
        problems = []
        if not t.title.strip():
            problems.append("title")
        if not t.summary.strip():
            problems.append("summary")
        if not t.phrases:
            problems.append("phrases")
        if t.kind not in ("query", "action"):
            problems.append(f"kind={t.kind!r}")
        if problems:
            missing.append(f"{name}: {', '.join(problems)}")
    assert not missing, "tools missing App-Intent metadata (title, summary, phrases, kind):\n" + "\n".join(missing)


def test_a_phrase_slot_is_a_real_parameter() -> None:
    bad = []
    for name, t in TOOLS.items():
        props = set((t.schema or {}).get("properties", {})) | _VOCAB
        for ph in t.phrases:
            for slot in re.findall(r"\{(\w+)\}", ph):
                if slot not in props:
                    bad.append(f"{name}: phrase {ph!r} uses {{{slot}}}, which is not a parameter")
    assert not bad, "\n".join(bad)


def test_the_manifest_builds_and_is_stable() -> None:
    m1 = capabilities.build()
    m2 = capabilities.build()
    assert capabilities._version(m1) == capabilities._version(m2), "the version hash is not stable"
    assert m1["modules"], "no modules in the manifest"
    for mod in m1["modules"]:
        for cap in mod["capabilities"]:
            assert cap["kind"] in ("query", "action", "page", "feed")
            if cap["kind"] in ("query", "action"):
                assert cap["intent"]["title"] and cap["intent"]["summary"], f"{cap['name']} has no intent title/summary"
                assert cap["consume"]["mcp_tool"] == cap["name"]
