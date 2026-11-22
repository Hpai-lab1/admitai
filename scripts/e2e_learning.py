"""Full improvement cycle (PRD 21.7 week-10 milestone; 8.1-8.4), live on the demo college:

  visitor question nobody answered -> staff correction in the console -> Feedback Analyst ->
  proposal in the approval inbox -> approval -> evaluation gate on a sandbox copy -> deployed ->
  the next visitor gets the expert's answer -> (optional) College Admin rolls it back.

    source local.env.sh && python scripts/e2e_learning.py [--rollback]

Takes a few minutes: the gate answers the whole test set twice through Module B.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experience"))
from experience_api.security import totp_code  # noqa: E402

C, B, A = "http://127.0.0.1:8200", "http://127.0.0.1:8150", "http://127.0.0.1:8100"
QUESTION = "Can I pay the tuition fee in instalments?"
ANSWER = (
    "Yes. The tuition fee can be paid in two instalments: the first before classes begin and the second "
    "by 15 January."
)


def ask(token: str, text: str) -> dict:
    out = {"text": "", "done": None}
    with httpx.stream(
        "POST",
        f"{B}/chat/e2e-learn-{uuid.uuid4().hex[:6]}/messages",
        json={"text": text},
        headers={"Authorization": f"Bearer {token}"},
        timeout=120,
    ) as r:
        ev = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                d = json.loads(line[5:])
                if ev == "token":
                    out["text"] += d["text"]
                elif ev == "done":
                    out["done"] = d
    out["text"] = out["text"].strip()
    return out


def visitor(s: httpx.Client) -> str:
    return s.post(
        f"{C}/widget/session", json={"public_key": "pk_demo_college_0001", "utm": {}}
    ).json()["session_token"]


def login(s: httpx.Client, email: str) -> dict:
    body = s.post(
        f"{C}/auth/login",
        json={
            "tenant": "demo-college",
            "email": email,
            "password": os.environ["DEMO_PASSWORD"],
        },
    ).json()
    if (
        body["status"] != "ok"
    ):  # admins use TOTP (PRD 5.4); the secret comes back on first enrolment
        secret = body.get("secret") or _enrolled_secret(email)
        body = s.post(
            f"{C}/auth/mfa",
            json={"mfa_token": body["mfa_token"], "code": totp_code(secret)},
        ).json()
    return {"Authorization": f"Bearer {body['access_token']}"}


def _enrolled_secret(email: str) -> str:
    """Local test only: the admin enrolled MFA on an earlier run; read the secret as Module C's own
    tests do. A real admin uses their authenticator app."""
    import psycopg

    with psycopg.connect(os.environ["ADMIN_DATABASE_URL"]) as conn:
        row = conn.execute(
            "select p.mfa_secret from app.profiles p join app.tenants t on t.id = p.tenant_id"
            " where p.email = %s and t.slug = 'demo-college'",
            (email,),
        ).fetchone()
    if not row or not row[0]:
        sys.exit(f"{email} needs MFA and has no secret")
    return row[0]


def step(n: int, text: str) -> None:
    print(f"\n[{n}] {text}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rollback", action="store_true")
    args = ap.parse_args()
    s = httpx.Client(timeout=60)
    tenant = s.post(
        f"{C}/widget/session", json={"public_key": "pk_demo_college_0001"}
    ).json()["tenant"]["id"]

    step(1, f"A visitor asks: {QUESTION}")
    first = ask(visitor(s), QUESTION)
    print(f"    assistant: {first['text']}\n    outcome: {first['done']['outcome']}")

    step(2, "The admission officer corrects it in the console")
    officer = login(s, "officer@example.com")
    r = s.post(
        f"{C}/corrections",
        headers=officer,
        json={
            "conversation_id": first["done"]["conversation_id"],
            "message_id": first["done"]["message_id"],
            "original_answer": first["text"],
            "corrected_answer": ANSWER,
            "reason": "Instalment rule from the fee office",
            "scope": "general",
            "trace_id": first["done"]["trace_id"],
        },
    )
    print(f"    correction recorded: {r.status_code}")
    for _ in range(20):
        if any(
            e["payload"].get("message_id") == first["done"]["message_id"]
            for e in s.get(
                f"{A}/events",
                headers={"X-Service-Key": os.environ["KEY_OPS"], "X-Tenant-Id": tenant},
            ).json()["items"]
        ):
            break
        time.sleep(0.5)
    print("    Module A received correction.submitted")

    step(3, "Nightly Feedback Analyst run")
    print(
        "   ",
        s.post(
            f"{A}/improvements/analyse",
            headers={"X-Service-Key": os.environ["KEY_OPS"], "X-Tenant-Id": tenant},
            timeout=120,
        ).json(),
    )

    step(4, "The proposal in the officer's approval inbox")
    inbox = s.get(f"{C}/approvals", headers=officer).json()["items"]
    p = next(x for x in inbox if x["diff"].get("question") == QUESTION)
    print(
        f"    {p['title']}\n    root cause: {p['root_cause']}  impact: {p['impact_score']}  "
        f"due: {p['due_at'][:10]}\n    proposed: {p['proposal']}"
    )

    step(5, "Approved; the evaluation gate runs on a sandbox copy of the college")
    r = s.post(
        f"{C}/approvals/decide",
        headers=officer,
        json={"ids": [p["id"]], "decision": "approve"},
    ).json()
    print(f"    {r}")
    t0 = time.time()
    while True:
        detail = s.get(
            f"{A}/improvements/{p['id']}",
            headers={"X-Service-Key": os.environ["KEY_OPS"], "X-Tenant-Id": tenant},
        ).json()
        if detail["status"] != "evaluating":
            break
        time.sleep(5)
    gate = (detail.get("eval") or {}).get("gate") or {}
    print(f"    after {time.time() - t0:.0f} s: {detail['status']}")
    print(
        f"    baseline  {gate.get('baseline')}\n    candidate {gate.get('candidate')}"
    )
    if gate.get("reasons"):
        print(f"    reasons: {gate['reasons']}")
    if detail["status"] != "deployed":
        return 1

    step(6, f"A new visitor asks: {QUESTION.replace('tuition fee', 'fees')}")
    after = ask(visitor(s), QUESTION.replace("tuition fee", "fees"))
    print(
        f"    assistant: {after['text']}\n    sources: {[x['title'] for x in after['done']['sources']]}"
    )
    ok = "two instalments" in after["text"]

    if args.rollback:
        step(7, "The College Admin rolls the change back")
        admin = login(s, "admin@example.com")
        print(
            "   ",
            s.post(f"{C}/approvals/{p['id']}/rollback", headers=admin)
            .json()
            .get("status"),
        )
        again = ask(visitor(s), QUESTION)
        print(f"    assistant: {again['text']}  ({again['done']['outcome']})")
        ok = ok and "two instalments" not in again["text"]

    print("\nFULL CYCLE " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
