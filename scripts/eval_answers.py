"""The college's own test questions (the pack's eval/questions.csv cases) through the live
assistant, as a visitor would ask them: how many get a real answer, whether the expected numbers
and codes appear, whether the reply is in the asked language, and how long the first sentence
takes. Out-of-scope cases pass when the assistant declines or offers a person.

    source local.env.sh && python scripts/eval_answers.py [--slug ruas] [--kind hindi]

Conversations are named eval-answers-<date>-<case>, which live analytics leaves out. Uses the
college's answer model (Gemini for RUAS), one question every few seconds. Writes a dated JSON
report to docs/eval/. Added 19 Nov 2026: the review's accuracy figure scored only the cited
source; this scores the answer itself.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

import httpx
import psycopg

ROOT = Path(__file__).resolve().parent.parent
B = os.environ.get("ASSISTANT_API_URL", "http://127.0.0.1:8150")
DECLINED = ("no_answer", "clarify", "handoff", "blocked", "refused")
LANG = {"hindi": "hi", "kannada": "kn"}


def facts_of(answer: str) -> set[str]:
    """Numbers and codes a correct answer must repeat (amounts, counts, codes like E197)."""
    return {f.replace(",", "") for f in re.findall(r"[A-Z]\d{3}|\d[\d,]*\d", answer)}


def ask(client: httpx.Client, headers: dict, conv: str, text: str, lang: str) -> dict:
    t0, first, out, done = time.monotonic(), None, "", {}
    with client.stream(
        "POST",
        f"{B}/chat/{conv}/messages",
        headers=headers,
        json={"text": text, "language": lang},
    ) as r:
        event = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:])
                if event == "token":
                    first = first or time.monotonic() - t0
                    out += data.get("text", "")
                elif event == "done":
                    done = data
    return {
        "text": out,
        "done": done,
        "first_s": first,
        "total_s": time.monotonic() - t0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="ruas")
    ap.add_argument("--kind", action="append", help="only these kinds (repeatable)")
    ap.add_argument("--pause", type=float, default=3.0)
    args = ap.parse_args()
    key = os.environ.get("KEY_OPS")
    if not key:
        sys.exit("Set KEY_OPS (source local.env.sh)")
    with psycopg.connect(os.environ["ADMIN_DATABASE_URL"]) as conn:
        tenant = conn.execute(
            "select id::text from app.tenants where slug = %s", (args.slug,)
        ).fetchone()[0]
        answers = {
            ref: a
            for ref, a in conn.execute(
                "select external_ref, answer from kb.curated_qa where tenant_id = %s"
                " and external_ref is not null",
                (tenant,),
            )
        }
        cases = conn.execute(
            "select ext_id, kind, question, language, expected_refs from learn.eval_cases"
            " where tenant_id = %s and active and source = 'pack' order by ext_id",
            (tenant,),
        ).fetchall()
    cases = [c for c in cases if not args.kind or c[1] in args.kind]
    headers = {"X-Service-Key": key, "X-Tenant-Id": tenant}
    stamp = date.today().isoformat()
    results: dict[str, list[dict]] = defaultdict(list)
    with httpx.Client(timeout=120) as client:
        for ext_id, kind, question, lang, refs in cases:
            r = ask(
                client,
                headers,
                f"eval-answers-{stamp}-{ext_id}",
                question,
                lang or "en",
            )
            outcome = r["done"].get("outcome")
            wanted = set()
            for ref in refs or []:
                if ref.startswith("qa:") and ref[3:] in answers:
                    wanted |= facts_of(answers[ref[3:]])
            text = r["text"].replace(",", "")
            if kind == "out_of_scope":
                passed = outcome in DECLINED
                real = None
            else:
                real = outcome in ("answered", "partial") and not r["text"].startswith(
                    ("I'm sorry", "ಕ್ಷಮಿಸಿ", "माफ़", "Did you mean")
                )
                facts_ok = not wanted or any(f in text for f in wanted)
                lang_ok = r["done"].get("language") == LANG.get(
                    kind, r["done"].get("language")
                )
                passed = bool(real and facts_ok and lang_ok)
            results[kind].append(
                {
                    "id": ext_id,
                    "question": question,
                    "outcome": outcome,
                    "real_answer": real,
                    "expected_facts": sorted(wanted),
                    "passed": passed,
                    "first_s": round(r["first_s"], 2) if r["first_s"] else None,
                    "answer": r["text"][:300],
                }
            )
            print(
                f"{ext_id} {kind:12} {outcome or '-':9} {'pass' if passed else 'FAIL'}",
                flush=True,
            )
            time.sleep(args.pause)
    summary = {}
    rows_all = [r for rows in results.values() for r in rows]
    for kind, rows in sorted(results.items()) + [("all", rows_all)]:
        firsts = sorted(r["first_s"] for r in rows if r["first_s"])
        summary[kind] = {
            "n": len(rows),
            "passed": sum(r["passed"] for r in rows),
            "real_answers": sum(1 for r in rows if r["real_answer"]),
            "declined": sum(1 for r in rows if r["outcome"] in DECLINED),
            "first_s_median": statistics.median(firsts) if firsts else None,
            "first_s_p95": firsts[max(0, int(len(firsts) * 0.95) - 1)]
            if firsts
            else None,
        }
    print(f"\n{'kind':12} {'n':>3} passed  real  declined  first-sentence median / p95")
    for kind, s in summary.items():
        print(
            f"{kind:12} {s['n']:3} {s['passed']:6} {s['real_answers']:5} {s['declined']:9}"
            f"   {s['first_s_median']} / {s['first_s_p95']}"
        )
    report = ROOT / "docs" / "eval" / f"answers-{args.slug}-{stamp}.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps({"summary": summary, "cases": results}, indent=1, ensure_ascii=False)
        + "\n"
    )
    print("report:", report.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
