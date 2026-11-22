"""Question variants for a college's curated answers, so paraphrases and Hindi / Kannada questions
find them (19 Nov 2026: none of RUAS's 214 curated answers had a variant, and "What is the
application fee?" did not reach the top 8).

    source local.env.sh && python scripts/curated_variants.py --slug ruas --dry-run   # look first
    source local.env.sh && python scripts/curated_variants.py --slug ruas             # save

For each approved curated answer without variants, Gemini writes four English rewordings of the
QUESTION (never the answer), one in Hindi and one in Kannada. Variants that repeat the question,
or are close to one of the college's test questions (so the test set stays honest), are dropped.
Each answer is then updated through Module A's API (a new version; the edit is recorded in
kb.curated_qa_history) and approved again with the actor "variants-<date>", so staff can find and
review every change. The college's curated_qa.csv gets the same variants, so a pack re-import
keeps them. Needs GEMINI_API_KEY; about one Gemini call per 10 answers.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

import httpx
import psycopg

ROOT = Path(__file__).resolve().parent.parent
A = os.environ.get("KNOWLEDGE_API_URL", "http://127.0.0.1:8100")
GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
PROMPT = """You help an Indian university's admissions assistant find answers. For each question
below, write other ways a student or parent might ask THE SAME question: 4 in English (one short
and informal), 1 in Hindi (Devanagari), 1 in Kannada (Kannada script). Keep names, programme names
and codes as they are. Never answer the question, never add facts, numbers or new topics.
Reply with JSON only: [{"id": "<id>", "en": ["...", "...", "...", "..."], "hi": "...", "kn": "..."}]

Questions:
"""


def norm(text: str) -> str:
    return re.sub(r"[^\w ]", "", text.lower()).strip()


def ask_gemini(key: str, model: str, batch: list[dict]) -> list[dict]:
    lines = "\n".join(f'{q["id"]}: {q["question"]}' for q in batch)
    for attempt in range(3):
        r = httpx.post(
            GEMINI,
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": PROMPT + lines}],
            },
            timeout=90,
        )
        if r.status_code == 200:
            text = r.json()["choices"][0]["message"]["content"]
            match = re.search(r"\[.*\]", text, re.S)
            if match:
                return json.loads(match.group(0))
        time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"Gemini gave no usable reply ({r.status_code})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="ruas")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--batch", type=int, default=10)
    args = ap.parse_args()
    key, svc = os.environ.get("GEMINI_API_KEY"), os.environ.get("KEY_OPS")
    if not key or not svc:
        sys.exit("Set GEMINI_API_KEY and KEY_OPS (source local.env.sh)")
    model = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
    with psycopg.connect(os.environ["ADMIN_DATABASE_URL"]) as conn:
        tenant = conn.execute(
            "select id::text from app.tenants where slug = %s", (args.slug,)
        ).fetchone()[0]
        rows = conn.execute(
            "select id::text, external_ref, question from kb.curated_qa where tenant_id = %s"
            " and status = 'approved' and coalesce(cardinality(variants), 0) = 0 order by external_ref",
            (tenant,),
        ).fetchall()
        tests = [
            norm(q)
            for (q,) in conn.execute(
                "select question from learn.eval_cases where tenant_id = %s and active",
                (tenant,),
            )
        ]
    todo = [{"id": r[0], "ref": r[1], "question": r[2]} for r in rows][: args.limit]
    print(f"{len(todo)} approved curated answers without variants")
    made: dict[str, list[str]] = {}
    dropped = 0
    for i in range(0, len(todo), args.batch):
        batch = todo[i : i + args.batch]
        by_id = {q["id"]: q for q in batch}
        for out in ask_gemini(key, model, batch):
            q = by_id.get(out.get("id"))
            if not q:
                continue
            raw = [*(out.get("en") or [])[:4], out.get("hi") or "", out.get("kn") or ""]
            keep, seen = [], {norm(q["question"])}
            for v in (" ".join(str(v).split()) for v in raw):
                n = norm(v)
                if not v or n in seen or len(v) > 200:
                    continue
                if any(
                    difflib.SequenceMatcher(None, n, t).ratio() >= 0.9 for t in tests
                ):
                    dropped += (
                        1  # too close to a test question: keep the test set honest
                    )
                    continue
                seen.add(n)
                keep.append(v)
            if keep:
                made[q["id"]] = keep
        print(f"  {min(i + args.batch, len(todo))}/{len(todo)}")
        time.sleep(4)
    print(
        f"variants for {len(made)} answers; {dropped} dropped as too close to a test question"
    )
    if args.dry_run:
        for q in todo[:5]:
            print(f"- {q['question']}\n    " + "\n    ".join(made.get(q["id"], [])))
        return 0
    actor = f"variants-{date.today().isoformat()}"
    headers = {"X-Service-Key": svc, "X-Tenant-Id": tenant}
    with httpx.Client(timeout=120) as client:
        for qa_id, variants in made.items():
            client.patch(
                f"{A}/curated-qa/{qa_id}",
                headers=headers,
                json={"variants": variants, "actor": actor},
            ).raise_for_status()
            client.post(
                f"{A}/curated-qa/{qa_id}/approve",
                headers=headers,
                json={
                    "actor": actor,
                    "reason": "Question variants only (generated; answer unchanged)",
                },
            ).raise_for_status()
    pack_csv = ROOT / "packs" / args.slug / "curated_qa.csv"
    if pack_csv.exists():
        by_ref = {q["ref"]: made[q["id"]] for q in todo if q["id"] in made and q["ref"]}
        rows = list(csv.DictReader(pack_csv.open(encoding="utf-8")))
        for r in rows:
            if r["external_ref"] in by_ref and not (r.get("variants") or "").strip():
                r["variants"] = "|".join(by_ref[r["external_ref"]])
        with pack_csv.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print("updated", pack_csv.relative_to(ROOT))
    print(f"saved and approved as {actor}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
