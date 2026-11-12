"""Retrieval evaluation over a pack's eval/questions.csv (PRD 20; Module A thesis: dense vs hybrid).

python -m knowledge_api.retrieval_eval packs/demo-college --tenant-id <uuid> [--modes dense,keyword,hybrid,rerank]

For answerable questions: hit@1 and hit@4 against expected_refs (qa:<external_ref>, doc:<title>;
catalogue:<code> refs are answered from the catalogue, not search, and are skipped here).
For must-refuse questions: the best vector score, so a no-answer threshold can be chosen that
refuses them while keeping answerable questions.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

from . import search
from .config import Settings
from .db import tx
from .embedder import make_embedder
from .rerank import make_reranker

REFUSE_KINDS = {"out_of_scope"}


def _ref(item: dict, qa_refs: dict[str, str]) -> str:
    c = item["citation"]
    if item["kind"] == "curated_qa":
        return f"qa:{qa_refs.get(c['qa_id'], c['qa_id'])}"
    return f"doc:{c['title']}"


def run(settings: Settings, tenant: str, pack: Path, modes: list[str]) -> dict:
    embedder = make_embedder(settings)
    reranker = make_reranker(settings.reranker) if "rerank" in modes else None
    if "rerank" in modes and reranker is None:
        raise SystemExit(
            "mode 'rerank' needs KB_RERANKER (a folder with the ONNX model)"
        )
    rows = list(
        csv.DictReader((pack / "eval" / "questions.csv").open(encoding="utf-8-sig"))
    )
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select id::text, external_ref from kb.curated_qa where external_ref is not null"
        )
        qa_refs = {r["id"]: r["external_ref"] for r in cur.fetchall()}
    report: dict = {}
    for mode in modes:
        by_kind: dict[str, list] = defaultdict(list)
        answerable_best, refuse_best, misses = [], [], []
        for r in rows:
            expected = {
                x
                for x in r["expected_refs"].split("|")
                if x and not x.startswith("catalogue:")
            }
            forbidden = {x for x in r["forbidden_refs"].split("|") if x}
            res = search.search(
                settings.database_url,
                embedder,
                tenant,
                r["question"],
                top_k=4,
                mode="hybrid" if mode == "rerank" else mode,
                reranker=reranker if mode == "rerank" else None,
            )
            got = [_ref(i, qa_refs) for i in res["items"]]
            leaked = bool(forbidden & set(got))
            if r["kind"] in REFUSE_KINDS or (not expected and r["kind"] != "sensitive"):
                refuse_best.append((res["max_vector_score"], r["id"]))
                by_kind[r["kind"]].append((None, leaked))
                continue
            if not expected:
                continue
            hit1 = bool(got) and got[0] in expected
            hit4 = bool(expected & set(got))
            by_kind[r["kind"]].append((hit1, hit4, leaked))
            answerable_best.append((res["max_vector_score"], r["id"]))
            if not hit4:
                misses.append(
                    f"{r['id']} [{r['kind']}] {r['question'][:60]!r} -> {got[:2]}"
                )
        summary = {}
        for kind, results in sorted(by_kind.items()):
            scored = [x for x in results if x[0] is not None]
            summary[kind] = {
                "n": len(results),
                "hit@1": sum(x[0] for x in scored),
                "hit@4": sum(x[1] for x in scored),
                "forbidden_cited": sum(x[-1] for x in results),
            }
        report[mode] = {
            "by_kind": summary,
            "misses": misses,
            "answerable_scores": sorted(answerable_best),
            "refuse_scores": sorted(refuse_best, reverse=True),
        }
    return report


def _print(report: dict) -> None:
    for mode, r in report.items():
        print(f"\n=== {mode} ===")
        tot1 = tot4 = n = 0
        for kind, s in r["by_kind"].items():
            if kind in REFUSE_KINDS:
                print(
                    f"  {kind:13} n={s['n']:3}  (refusal set)  forbidden cited={s['forbidden_cited']}"
                )
                continue
            n += s["n"]
            tot1 += s["hit@1"]
            tot4 += s["hit@4"]
            print(
                f"  {kind:13} n={s['n']:3}  hit@1={s['hit@1']:3}  hit@4={s['hit@4']:3}  forbidden={s['forbidden_cited']}"
            )
        print(f"  {'ALL answerable':13} n={n:3}  hit@1={tot1:3}  hit@4={tot4:3}")
        lo = r["answerable_scores"][:5]
        hi = r["refuse_scores"][:5]
        print("  lowest answerable max-scores:", [f"{s:.3f} {q}" for s, q in lo])
        print("  highest must-refuse max-scores:", [f"{s:.3f} {q}" for s, q in hi])
        for m in r["misses"]:
            print("  miss:", m)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pack", type=Path)
    ap.add_argument("--tenant-id", required=True)
    ap.add_argument("--modes", default="dense,keyword,hybrid")
    a = ap.parse_args()
    s = Settings()
    if not s.database_url:
        sys.exit("Set KB_DATABASE_URL (source local.env.sh)")
    _print(run(s, a.tenant_id, a.pack, a.modes.split(",")))
