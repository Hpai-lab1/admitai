"""Answer quality of each college's live assistant on its test set (PRD 20), through Module A's
evaluation runner and Module B headless. Read-only for the tenants.

    source local.env.sh && python scripts/eval_live.py [--limit N]
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "knowledge"))
from knowledge_api import gate  # noqa: E402
from knowledge_api.assistant_client import AssistantClient  # noqa: E402
from knowledge_api.config import Settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--slug", action="append", help="only these colleges (repeatable)")
    args = ap.parse_args()
    s = Settings()
    with psycopg.connect(os.environ["ADMIN_DATABASE_URL"]) as conn:
        tenants = conn.execute(
            "select slug, id::text from app.tenants where slug in ('demo-college','ruas','riverside') and status = 'live'"
            " order by slug"
        ).fetchall()
    tenants = [t for t in tenants if not args.slug or t[0] in args.slug]
    failed_critical = 0
    for slug, tid in tenants:
        run = gate.run_suite(
            s, AssistantClient(s), tid, tid, "adhoc", "check_all", None, args.limit
        )
        sc = run["scores"]
        by_kind = Counter()
        totals = Counter()
        with psycopg.connect(os.environ["KB_ADMIN_DATABASE_URL"]) as conn:
            kinds = dict(
                conn.execute(
                    "select ext_id, kind from learn.eval_cases where tenant_id = %s",
                    (tid,),
                ).fetchall()
            )
        for r in run["results"]:
            totals[kinds.get(r["id"], "?")] += 1
            by_kind[kinds.get(r["id"], "?")] += r["passed"]
        crit = [r["id"] for r in run["results"] if r["critical"] and not r["passed"]]
        failed_critical += len(crit)
        pct = lambda v: "-" if v is None else f"{v:.0%}"  # noqa: E731
        print(
            f"{slug:13} accuracy {pct(sc['accuracy'])}  refusal {pct(sc['refusal'])}  groundedness "
            f"{pct(sc['groundedness'])} of {sc.get('claims', 0)} claims  no forbidden source "
            f"{pct(sc.get('forbidden_free'))}  routing {pct(sc['routing'])}  ({sc['passed']}/{sc['cases']} cases)"
        )
        if sc.get("review_error"):
            print(f"              grounding review unavailable: {sc['review_error']}")
        print(
            "              "
            + "  ".join(f"{k} {by_kind[k]}/{totals[k]}" for k in sorted(totals))
        )
        if crit:
            print(f"              critical cases failing: {crit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
