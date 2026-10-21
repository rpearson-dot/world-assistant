"""Check where the secrets come from, without ever printing a value.

  docker compose exec api python -m app.secretsctl check
"""

from __future__ import annotations

import json
import sys

from app import config, secretstore


def main(argv: list[str]) -> int:
    if argv[:1] == ["dump-json"]:
        # For scripts/openbao.sh, which pipes this straight into the vault. Refuses to print to a screen.
        if sys.stdout.isatty():
            print("refusing to print secrets to a terminal; pipe the output into the vault", file=sys.stderr)
            return 2
        print(json.dumps(secretstore.local_values(config.SECRETS_DIR)))
        return 0
    if argv[:1] not in (["check"], []):
        print("usage: python -m app.secretsctl check")
        return 2
    directory = config.SECRETS_DIR
    print(f"backend: {secretstore.backend_name()}   secrets directory: {directory}")
    bad = 0
    for row in secretstore.report(directory):
        flag = "ok     " if row["source"] != "missing" else ("MISSING" if row["required"] else "unset  ")
        bad += row["source"] == "missing" and row["required"]
        print(f"  {flag} {row['name']:<30} from {row['source']:<8} {'(required)' if row['required'] else ''}")
    if bad:
        print(f"\n{bad} required secret(s) missing.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
