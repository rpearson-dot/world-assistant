"""Write docs/api.md from the app's own routes, so the reference is always what the server answers.

    python scripts/api-doc.py           rewrite docs/api.md
    python scripts/api-doc.py --check   exit 1 if docs/api.md is out of date (a test runs this)
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app

OUT = Path(__file__).resolve().parent.parent / "docs" / "api.md"

HEAD = """# API reference

Every endpoint World Assistant answers, grouped by area. This page is written from the code
(`python scripts/api-doc.py`), and a test fails when it falls behind. Your server's live reference, with every
parameter and a "try it" button, is at `/docs`.

**Access.** From outside the home network, `/v1/*` needs `Authorization: Bearer <api_token>`. On the home network,
and for the web app in a browser on your tailnet, no token is needed ([security](security.md#access)). The web
pages themselves hold no data: each one calls these endpoints.

"""


def first_paragraph(text: str) -> str:
    first = text.strip().split("\n\n")[0]
    return " ".join(first.split()).replace("|", "\\|")


def build() -> str:
    spec = app.openapi()
    groups: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for path, ops in spec["paths"].items():
        parts = path.strip("/").split("/")
        key = parts[1] if parts[0] == "v1" and len(parts) > 1 else "Other"
        for method, op in ops.items():
            text = first_paragraph(op.get("description") or "") or op.get("summary", "")
            groups[key].append((path, method.upper(), text))
    out = [HEAD]
    keys = sorted(k for k in groups if k != "Other") + (["Other"] if "Other" in groups else [])
    out.append("**Areas:** " + " · ".join(f"[{k}](#{k.lower().replace(' ', '-')})" for k in keys) + "\n")
    for k in keys:
        out.append(f"\n## {k}\n\n| Method | Path | What it does |\n|---|---|---|")
        for path, methods, text in sorted(groups[k]):
            out.append(f"| {methods} | `{path}` | {text} |")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    text = build()
    if "--check" in sys.argv:
        sys.exit(0 if OUT.exists() and OUT.read_text() == text else 1)
    OUT.write_text(text)
    print(f"wrote {OUT} ({text.count(chr(10))} lines)")
