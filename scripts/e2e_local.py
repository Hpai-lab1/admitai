"""Walking skeleton (PRD 21.7, week 4): a real question end to end through Modules C, B and A,
over HTTP exactly as the widget and console call them. Run after bootstrap_local.py and
run_local.sh:

    source local.env.sh && python scripts/e2e_local.py

Checks: visitor session from C -> checked, cited answer from B (facts from A) -> feedback in C
reaches A as an event -> minor detection -> consent, lead and callback in C with B's handoff
package -> the officer sees it in the queue -> knowledge screens served from A through C.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid

import httpx

C, B, A = "http://127.0.0.1:8200", "http://127.0.0.1:8150", "http://127.0.0.1:8100"
PK = "pk_demo_college_0001"
results: list[tuple[bool, str]] = []


def check(ok: bool, what: str, detail: object = "") -> None:
    results.append((ok, what))
    print(
        f"{'PASS' if ok else 'FAIL'}  {what}"
        + (f"  [{detail}]" if detail and not ok else "")
    )


def ask(token: str, conv: str, text: str) -> dict:
    out = {"text": "", "events": {}, "first_s": None}
    t0 = time.time()
    with httpx.stream(
        "POST",
        f"{B}/chat/{conv}/messages",
        json={"text": text},
        headers={"Authorization": f"Bearer {token}"},
        timeout=120,
    ) as r:
        r.raise_for_status()
        ev = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:])
                if ev == "token":
                    out["first_s"] = out["first_s"] or round(time.time() - t0, 1)
                    out["text"] += data["text"]
                else:
                    out["events"][ev] = data
    out["text"] = out["text"].strip()
    return out


def main() -> int:
    s = httpx.Client(timeout=30)
    sess = s.post(
        f"{C}/widget/session", json={"public_key": PK, "utm": {"utm_source": "e2e"}}
    ).json()
    token = sess["session_token"]
    tenant = sess["tenant"]["id"]
    v = {"Authorization": f"Bearer {token}"}
    check(bool(token), "C issues a visitor session for the demo college")

    conv = "e2e-" + uuid.uuid4().hex[:8]
    a1 = ask(token, conv, "What is the tuition fee for B.Tech CSE?")
    done = a1["events"]["done"]
    check(
        # Amounts are written as on the college's website ("₹2,50,000"): compare the digits.
        "250000" in a1["text"].replace(",", "") and "[1]" in a1["text"],
        "B answers with the exact catalogue fee and a citation",
        a1["text"],
    )
    check(
        done["sources"][0]["source_type"] == "catalogue",
        "the citation is A's program catalogue",
        done["sources"],
    )
    print(f"      answer: {a1['text']}  (first sentence {a1['first_s']} s)")

    a2 = ask(token, conv, "and what about its hostel fee?")
    check(
        "90000" in a2["text"].replace(",", ""),
        "follow-up 'its hostel fee' resolves to the same program",
        a2["text"],
    )
    check(
        "lead_prompt" in a2["events"],
        "B asks for contact once, after two answered questions",
    )

    a3 = ask(token, conv, "Is there a swimming pool on campus?")
    check(
        a3["events"]["done"]["outcome"] == "no_answer"
        and a3["events"].get("handoff_requested"),
        "an unanswerable question gets an honest no-answer and a handoff offer",
        a3["text"],
    )

    # Feedback: C records it and delivers feedback.created to A.
    r = s.post(
        f"{C}/feedback",
        headers=v,
        json={
            "conversation_id": conv,
            "message_id": done["message_id"],
            "kind": "thumb",
            "value": -1,
            "reason": "outdated",
            "trace_id": done["trace_id"],
        },
    )
    check(r.status_code == 201, "C records a thumbs-down with a reason", r.text)
    seen = False
    for _ in range(20):
        events = s.get(
            f"{A}/events",
            headers={"X-Service-Key": os.environ["KEY_OPS"], "X-Tenant-Id": tenant},
        ).json()["items"]
        if any(e["payload"].get("message_id") == done["message_id"] for e in events):
            seen = True
            break
        time.sleep(0.5)
    check(seen, "A receives feedback.created with B's message and trace ids")

    # A minor: B flags it; no lead is possible without a verified guardian.
    minor_conv = "e2e-minor-" + uuid.uuid4().hex[:6]
    m = ask(token, minor_conv, "I am 16 and in class 11. Is there a hostel?")
    check(
        m["events"].get("consent_required", {}).get("given_by") == "guardian",
        "B detects an under-18 enquirer and asks for guardian consent",
    )

    # Adult visitor asks for a person: consent -> lead -> callback carrying B's handoff package.
    sess2 = s.post(f"{C}/widget/session", json={"public_key": PK, "utm": {}}).json()
    t2, v2 = (
        sess2["session_token"],
        {"Authorization": f"Bearer {sess2['session_token']}"},
    )
    conv2 = "e2e-cb-" + uuid.uuid4().hex[:6]
    q = ask(t2, conv2, "Which labs does the CSE department have?")
    h = q["events"].get("handoff_requested") or {
        "queue": "faculty",
        "department": "Computer Science",
    }
    for purpose, granted in (("contact", True), ("marketing", False)):
        s.post(
            f"{C}/consents",
            headers=v2,
            json={"purpose": purpose, "granted": granted, "text_version": "2026-10-v1"},
        )
    r = s.post(
        f"{C}/leads",
        headers=v2,
        json={
            "name": "E2E Test",
            "phone": "+15550109999",
            "program_interest": ["BTECH-CSE"],
            "conversation_id": conv2,
        },
    )
    check(r.status_code == 201, "C creates a consented lead", r.text)
    r = s.post(
        f"{C}/callbacks",
        headers=v2,
        json={
            "conversation_id": conv2,
            "queue": "faculty",
            "department": "Computer Science",
            "reason": "Labs question",
            "transcript": [{"role": "user", "text": "(widget copy)"}],
        },
    )
    check(r.status_code == 201, "C accepts the callback request", r.text)

    officer = s.post(
        f"{C}/auth/login",
        json={
            "tenant": "demo-college",
            "email": "cs.faculty@example.com",
            "password": os.environ["DEMO_PASSWORD"],
        },
    ).json()
    staff = {"Authorization": f"Bearer {officer['access_token']}"}
    queue = s.get(f"{C}/handoffs", headers=staff).json()
    mine = next((x for x in queue if x["conversation_id"] == conv2), None)
    check(mine is not None, "the CSE faculty member sees the callback in their queue")
    if mine:
        ctx = mine["context"]
        check(
            ctx["transcript"][0]["text"] == "Which labs does the CSE department have?"
            and "ai_draft" in ctx,
            "the handoff package is B's transcript and AI draft (not the widget copy)",
            ctx,
        )
        check(
            bool(ctx.get("trace_id")),
            "the package carries B's trace id for attribution",
        )
    print(f"      handoff route from B: {h}")

    # Erasure on request (PRD 11.4): C blanks its rows, B the transcript, A the learning signals.
    ops = {"X-Service-Key": os.environ["KEY_OPS"], "X-Tenant-Id": tenant}
    r = s.post(f"{C}/privacy/erase", headers=v2)
    check(
        r.status_code == 200 and r.json()["leads_anonymised"] == 1,
        "a visitor can erase their data",
        r.text,
    )
    view = s.get(f"{B}/chat/{conv2}", headers=ops).json()
    check(
        view.get("erased_at")
        and all(m["content"] == "[erased]" for m in view["messages"]),
        "B's transcript of that visitor is blanked",
        view.get("messages", [])[:1],
    )
    erased = next(
        (
            e
            for e in s.get(f"{A}/events", headers=ops).json()["items"]
            if e["type"] == "erasure.requested"
            and conv2 in e["payload"].get("conversation_ids", [])
        ),
        None,
    )
    check(
        bool(erased and erased["processed_at"]),
        "A received and applied the erasure for that conversation",
    )
    after = next(
        (
            x
            for x in s.get(f"{C}/handoffs", headers=staff).json()
            if x["conversation_id"] == conv2
        ),
        {},
    )
    check(
        after.get("context") == {"erased": True},
        "the callback keeps its place in the queue without the transcript",
        after.get("context"),
    )

    # Knowledge screens in the console come from A through C.
    admin = s.post(
        f"{C}/auth/login",
        json={
            "tenant": "demo-college",
            "email": "officer@example.com",
            "password": os.environ["DEMO_PASSWORD"],
        },
    ).json()
    docs = s.get(
        f"{C}/kb/documents",
        headers={"Authorization": f"Bearer {admin['access_token']}"},
    )
    titles = (
        {d["title"]: d["status"] for d in docs.json().get("items", [])}
        if docs.status_code == 200
        else {}
    )
    check(
        titles.get("Prospectus 2027-28") == "approved"
        and titles.get("Fee notice 2026-27") == "expired",
        "C's knowledge screen lists A's documents with their status",
        docs.text[:200],
    )

    failed = [w for ok, w in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
