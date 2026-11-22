"""Retrieval quality of a college's knowledge base, without any answer model: each active test
case that names a curated answer (`qa:<external_ref>`) is sent to Module A's /kb/search, and the
rank of that answer is recorded. The website's copy of the same FAQ entry (a chunk whose heading
ends with the curated question) counts as found, reported separately.

    source local.env.sh && python scripts/eval_retrieval.py [--slug ruas] [--report docs/eval/x.json]

Prints Hit@1 / Hit@4 / Hit@8 by kind of case (direct, paraphrase, hindi, kannada, hinglish) and
writes a dated JSON report. Read-only: searches only, nothing is stored. Added 19 Nov 2026 so a
knowledge change can be measured in a minute (the answer run, scripts/eval_live.py, takes much
longer because every case goes through the answer model).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

import httpx
import psycopg

ROOT = Path(__file__).resolve().parent.parent
A = os.environ.get("KNOWLEDGE_API_URL", "http://127.0.0.1:8100")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="ruas")
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--report", help="where to write the JSON report")
    args = ap.parse_args()
    key = os.environ.get("KEY_OPS") or os.environ.get("SERVICE_KEY")
    if not key:
        sys.exit("Set KEY_OPS (source local.env.sh)")
    with psycopg.connect(os.environ["ADMIN_DATABASE_URL"]) as conn:
        row = conn.execute(
            "select id::text from app.tenants where slug = %s", (args.slug,)
        ).fetchone()
        if not row:
            sys.exit(f"No college {args.slug}")
        tenant = row[0]
        qa = {
            ext: (qid, question.strip().lower())
            for qid, ext, question in conn.execute(
                "select id::text, external_ref, question from kb.curated_qa"
                " where tenant_id = %s and external_ref is not null",
                (tenant,),
            )
        }
        cases = conn.execute(
            "select ext_id, kind, question, expected_refs from learn.eval_cases"
            " where tenant_id = %s and active and kind <> 'out_of_scope'"
            " and exists (select 1 from unnest(expected_refs) r where r like 'qa:%%')"
            " order by ext_id",
            (tenant,),
        ).fetchall()
    headers = {"X-Service-Key": key, "X-Tenant-Id": tenant}
    by_kind: dict[str, list[dict]] = defaultdict(list)
    with httpx.Client(timeout=60) as client:
        for ext_id, kind, question, refs in cases:
            wanted = [qa[r[3:]] for r in refs if r.startswith("qa:") and r[3:] in qa]
            ids = {w[0] for w in wanted}
            texts = {w[1] for w in wanted}
            t0 = time.monotonic()
            items = (
                client.post(
                    f"{A}/kb/search",
                    headers=headers,
                    json={"query": question, "top_k": args.top_k},
                )
                .raise_for_status()
                .json()["items"]
            )
            ms = (time.monotonic() - t0) * 1000
            rank = copy_rank = None
            for i, item in enumerate(items, 1):
                c = item.get("citation") or {}
                if rank is None and c.get("qa_id") in ids:
                    rank = i
                heading = (c.get("heading_path") or "").split(">")[-1].strip().lower()
                if copy_rank is None and heading and heading in texts:
                    copy_rank = i
            by_kind[kind].append(
                {
                    "id": ext_id,
                    "question": question,
                    "rank": rank,
                    "website_copy_rank": copy_rank,
                    "top_kind": items[0]["kind"] if items else None,
                    "ms": round(ms),
                }
            )

    def hit(rows: list[dict], k: int, field: str = "rank") -> float:
        return sum(1 for r in rows if r[field] and r[field] <= k) / len(rows)

    def either(r: dict) -> int | None:
        ranks = [x for x in (r["rank"], r["website_copy_rank"]) if x]
        return min(ranks) if ranks else None

    summary = {}
    all_rows = [r for rows in by_kind.values() for r in rows]
    for kind, rows in sorted(by_kind.items()) + [("all", all_rows)]:
        for r in rows:
            r["either_rank"] = either(r)
        summary[kind] = {
            "n": len(rows),
            "curated_hit@1": round(hit(rows, 1), 3),
            "curated_hit@4": round(hit(rows, 4), 3),
            "curated_hit@8": round(hit(rows, 8), 3),
            "either_hit@1": round(hit(rows, 1, "either_rank"), 3),
            "either_hit@4": round(hit(rows, 4, "either_rank"), 3),
            "website_copy_on_top": sum(
                1 for r in rows if r["website_copy_rank"] == 1 and r["rank"] != 1
            ),
        }
    print(f"{args.slug}: {len(all_rows)} cases with a curated answer")
    print(
        f"{'kind':12} {'n':>3}  curated @1  @4  @8 | curated or website copy @1  @4 | copy on top"
    )
    for kind, s in summary.items():
        print(
            f"{kind:12} {s['n']:3}  {s['curated_hit@1']:9.0%} {s['curated_hit@4']:4.0%}"
            f" {s['curated_hit@8']:4.0%} | {s['either_hit@1']:24.0%} {s['either_hit@4']:4.0%}"
            f" | {s['website_copy_on_top']:11}"
        )
    misses = [r["id"] for r in all_rows if not r["either_rank"]]
    print("not in the top", args.top_k, ":", " ".join(misses) or "none")
    report = (
        Path(args.report)
        if args.report
        else (
            ROOT
            / "docs"
            / "eval"
            / f"retrieval-{args.slug}-{date.today().isoformat()}.json"
        )
    )
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps(
            {"slug": args.slug, "summary": summary, "cases": by_kind},
            indent=1,
            ensure_ascii=False,
        )
        + "\n"
    )
    print(
        "report:", report.relative_to(ROOT) if report.is_relative_to(ROOT) else report
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
