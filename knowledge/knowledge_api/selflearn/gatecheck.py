"""Stage 6, the gate, for lessons: the existing evaluation gate (gate.gate_and_deploy) with three
additions, used only for proposals that carry a lesson_id:

1. the questions the lesson affects (select_quick, plus the lesson's own questions) are joined by a
   stratified sample of the college's whole test set (every kind and source: hand-written,
   catalogue-generated, earlier proposals), so a lesson cannot pass on its own questions alone;
2. zero tolerance: no metric may fall at all (accuracy, refusals, groundedness, routing,
   forbidden citations);
3. retrieval is measured as well: Hit@1 and Hit@4 over every test question with an expected page
   or curated answer, before and after the lesson, in the sandbox. Any fall blocks the lesson,
   and the lesson's own questions must be found (Hit@4).
"""

from __future__ import annotations

import random

from .. import search
from ..config import Settings
from ..db import tx
from ..embedder import Embedder


def stratified_sample(
    settings: Settings, tenant: str, n: int, seed: int = 7
) -> set[str]:
    """Up to n case ids, spread over (source, kind) strata, critical cases first in each."""
    if n <= 0:
        return set()
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select ext_id, source, kind, critical from learn.eval_cases where active"
            " order by ext_id"
        )
        rows = cur.fetchall()
    strata: dict[tuple, list[dict]] = {}
    for r in rows:
        strata.setdefault((r["source"], r["kind"]), []).append(r)
    rng = random.Random(seed)
    for v in strata.values():
        rng.shuffle(v)
        v.sort(key=lambda r: not r["critical"])
    chosen: list[str] = []
    while len(chosen) < n and any(strata.values()):
        for key in sorted(strata):
            if strata[key] and len(chosen) < n:
                chosen.append(strata[key].pop(0)["ext_id"])
    return set(chosen)


def _ref(item: dict, qa_refs: dict[str, str]) -> str:
    c = item["citation"]
    if item["kind"] == "curated_qa":
        return f"qa:{qa_refs.get(c['qa_id'], c['qa_id'])}"
    return f"doc:{c['title']}"


def retrieval_cases(settings: Settings, tenant: str, extra: list[dict]) -> list[dict]:
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select ext_id, question, expected_refs from learn.eval_cases where active"
        )
        rows = [dict(r) for r in cur.fetchall()]
    rows += [
        {
            "ext_id": c["ext_id"],
            "question": c["question"],
            "expected_refs": c["expected_refs"],
        }
        for c in extra
    ]
    out = []
    for r in rows:
        refs = {x for x in r["expected_refs"] or [] if x.startswith(("doc:", "qa:"))}
        if refs and r["question"]:
            out.append({**r, "refs": refs})
    return out


def retrieval(
    settings: Settings, embedder: Embedder, target: str, cases: list[dict]
) -> dict:
    """Hit@1 / Hit@4 of `target` (a sandbox copy) on the cases."""
    from ..gate import _qa_refs

    qa_refs = _qa_refs(settings, target)
    per: dict[str, dict] = {}
    for c in cases:
        res = search.search(
            settings.database_url,
            embedder,
            target,
            c["question"],
            top_k=4,
            lessons=True,
        )
        got = [_ref(i, qa_refs) for i in res["items"]]
        per[c["ext_id"]] = {
            "hit1": bool(got) and got[0] in c["refs"],
            "hit4": bool(c["refs"] & set(got)),
        }
    n = len(per) or 1
    return {
        "hit@1": round(sum(v["hit1"] for v in per.values()) / n, 4),
        "hit@4": round(sum(v["hit4"] for v in per.values()) / n, 4),
        "cases": len(per),
        "per_case": per,
    }


def judge(before: dict, after: dict, own: set[str]) -> tuple[list[str], dict]:
    """Reasons the lesson must not go live, and the summary stored with the gate result."""
    reasons = []
    for m in ("hit@1", "hit@4"):
        if after[m] < before[m]:
            reasons.append(f"retrieval {m} fell from {before[m]:.1%} to {after[m]:.1%}")
    broke = [
        cid
        for cid, v in after["per_case"].items()
        if cid not in own
        and before["per_case"].get(cid, {}).get("hit4")
        and not v["hit4"]
    ]
    if broke:
        reasons.append(
            "retrieval no longer finds the right source for: " + ", ".join(broke[:10])
        )
    missed = [cid for cid in own if not after["per_case"].get(cid, {}).get("hit4")]
    if missed:
        reasons.append(
            "the lesson's own question is still not found: " + ", ".join(missed)
        )
    summary = {
        "before": {k: before[k] for k in ("hit@1", "hit@4", "cases")},
        "after": {k: after[k] for k in ("hit@1", "hit@4", "cases")},
    }
    return reasons, summary
