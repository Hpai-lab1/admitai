"""Contract check (repo rule: "Every API a module provides must match its OpenAPI file in
contracts/"): regenerate each module's OpenAPI into docs/proposed-contracts/ and confirm that every
endpoint in the agreed contracts is implemented.

    source local.env.sh && python scripts/check_contracts.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MODULES = (
    ("knowledge", "knowledge_api"),
    ("assistant", "assistant_api"),
    ("experience", "experience_api"),
)


def main() -> int:
    out = ROOT / "docs" / "proposed-contracts"
    out.mkdir(parents=True, exist_ok=True)
    gaps = []
    for name, pkg in MODULES:
        sys.path.insert(0, str(ROOT / name))
        spec = __import__(f"{pkg}.main", fromlist=["create_app"]).create_app().openapi()
        spec["info"]["version"] = "0.2.0-proposed"
        (out / f"{name}.openapi.json").write_text(
            json.dumps(spec, indent=2, ensure_ascii=False) + "\n"
        )
        stub = yaml.safe_load((ROOT / "contracts" / f"{name}.openapi.yaml").read_text())
        missing = [
            f"{m.upper()} {p}"
            for p, methods in stub["paths"].items()
            for m in methods
            if p not in spec["paths"] or m not in spec["paths"][p]
        ]
        gaps += [f"{name}: {x}" for x in missing]
        print(
            f"{name:11} {len(spec['paths']):3} paths implemented; contract endpoints missing: {missing or 'none'}"
        )
    if gaps:
        print("CONTRACT GAPS:", gaps)
        return 1
    print("all contract endpoints implemented")
    return 0


if __name__ == "__main__":
    sys.exit(main())
