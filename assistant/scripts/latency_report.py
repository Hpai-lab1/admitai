"""Chat latency per step on the lab server (PRD 7.5), from Module B's own trace spans.

    source local.env.sh && cd assistant
    python scripts/latency_report.py --widget-key <key> --tenant-id <uuid> --questions ../packs/<college>/eval/questions.csv \
        --out benchmarks/latency-<date>.json

Each question is asked as a visitor (a Module C widget session, then the SSE chat stream), one at
a time on a quiet server. The visitor measures time to the first checked sentence and to the end;
the trace (GET /traces/{id}, ops key) gives each step: guardrail, rules router, router model,
Module A retrieval, answer generation. Reported as p50 / p95 against the PRD 7.5 chat budget.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from traffic_cleanup import retire  # noqa: E402

# PRD 7.5 chat budget (ms) for the steps Module B traces; total target: first sentence <= 2.5 s
# p95 in Level 1 (F1.4 "first sentence") and full answer <= 5 s p95.
BUDGET_MS = {
    "input_guardrail": 50,
    "router_rules": 20,
    "router_model": 300,
    "retrieval": 350,
}


def pct(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(round(p * (len(s) - 1))))], 1)


def ask(http, assistant, token, conv, text):
    t0 = time.monotonic()
    first = None
    done = {}
    with http.stream(
        "POST",
        f"{assistant}/chat/{conv}/messages",
        json={"text": text, "language": "en"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    ) as r:
        r.raise_for_status()
        event = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                if event == "token" and first is None:
                    first = time.monotonic() - t0
                elif event == "done":
                    done = json.loads(line[5:])
    return first, time.monotonic() - t0, done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--widget-key", required=True)
    ap.add_argument(
        "--tenant-id", required=True, help="the college's tenant id (trace lookup)"
    )
    ap.add_argument("--questions", type=Path, required=True)
    ap.add_argument(
        "--kinds", default="direct,paraphrase", help="question kinds to ask (English)"
    )
    ap.add_argument(
        "--experience",
        default=os.environ.get("EXPERIENCE_API_URL", "http://127.0.0.1:8200"),
    )
    ap.add_argument("--assistant", default="http://127.0.0.1:8150")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    ops = os.environ.get("KEY_OPS") or os.environ.get("SERVICE_KEY")
    kinds = set(a.kinds.split(","))
    with a.questions.open(encoding="utf-8") as f:
        qs = [
            r for r in csv.DictReader(f) if r["kind"] in kinds and r["language"] == "en"
        ]
    http = httpx.Client(timeout=30)
    rows = []
    sessions: dict[str, str] = {}
    for q in qs:
        s = http.post(
            f"{a.experience}/widget/session",
            json={"public_key": a.widget_key, "utm": {}, "referrer": None},
        )
        s.raise_for_status()
        token = s.json()["session_token"]
        conv = f"latency-{uuid.uuid4().hex[:10]}"
        sessions[conv] = s.json()["session_id"]
        first, total, done = ask(http, a.assistant, token, conv, q["question"])
        steps = {}
        tid = done.get("trace_id")
        if tid and ops:
            tr = http.get(
                f"{a.assistant}/traces/{tid}",
                headers={"X-Service-Key": ops, "X-Tenant-Id": a.tenant_id},
            )
            if tr.status_code == 200:
                for sp in tr.json().get("spans") or []:
                    if sp.get("ms") is not None:
                        steps[sp["name"]] = steps.get(sp["name"], 0) + float(sp["ms"])
        rows.append(
            {
                "id": q["id"],
                "question": q["question"],
                "outcome": done.get("outcome"),
                "route": (done.get("route") or {}).get("decided_by")
                if isinstance(done.get("route"), dict)
                else None,
                "first_sentence_s": round(first, 3) if first else None,
                "total_s": round(total, 3),
                "steps_ms": steps,
            }
        )
        print(
            f"{rows[-1]['first_sentence_s']}s first / {total:.2f}s total  [{done.get('outcome')}]  {q['question']}"
        )
    time.sleep(2)
    cleanup = retire(a.tenant_id, sessions)  # test traffic: never counted as visitors
    names = sorted({k for r in rows for k in r["steps_ms"]})
    summary = {
        "questions": len(rows),
        "first_sentence_s": {
            "p50": pct(
                [r["first_sentence_s"] for r in rows if r["first_sentence_s"]], 0.5
            ),
            "p95": pct(
                [r["first_sentence_s"] for r in rows if r["first_sentence_s"]], 0.95
            ),
            "target_p95": 2.5,
        },
        "total_s": {
            "p50": pct([r["total_s"] for r in rows], 0.5),
            "p95": pct([r["total_s"] for r in rows], 0.95),
            "target_p95": 5.0,
        },
        "steps_ms": {
            n: {
                "n": sum(1 for r in rows if n in r["steps_ms"]),
                "p50": pct([r["steps_ms"][n] for r in rows if n in r["steps_ms"]], 0.5),
                "p95": pct(
                    [r["steps_ms"][n] for r in rows if n in r["steps_ms"]], 0.95
                ),
                "budget": BUDGET_MS.get(n),
            }
            for n in names
        },
    }
    report = {
        "date": time.strftime("%Y-%m-%d"),
        "settings": {
            k: os.environ.get(k)
            for k in ("ANSWER_MODEL", "ROUTER_MODEL", "LLM_BASE_URL")
        },
        "summary": summary,
        "rows": rows,
        "cleanup": cleanup,
    }
    print(json.dumps(summary, indent=1))
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print("written", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
