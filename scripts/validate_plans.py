"""Validate every plan.json under the given folders against docs/plan.schema.json.

    python scripts/validate_plans.py out [more folders]

Prints one line per plan and exits non-zero if any fails.
"""
import json
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent.parent


def main(*dirs):
    schema = json.loads((ROOT / "docs" / "plan.schema.json").read_text())
    check = jsonschema.Draft202012Validator(schema)
    plans = sorted(p for d in (dirs or ["out"]) for p in Path(d).rglob("plan.json"))
    bad = 0
    for p in plans:
        errs = list(check.iter_errors(json.loads(p.read_text())))
        bad += bool(errs)
        print(("ok  " if not errs else "FAIL"), p, *(f"\n      {'/'.join(map(str, e.path))}: {e.message}" for e in errs[:3]))
    print(f"{len(plans) - bad} of {len(plans)} plans valid")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main(*sys.argv[1:])
