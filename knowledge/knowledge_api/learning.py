"""Feedback Analyst (PRD 8.1-8.3): signals -> root cause -> clusters -> impact-ranked proposals.

Runs as a batch job (nightly in Level 1; `python -m knowledge_api.learning run --tenant-id ...`
or POST /improvements/analyse). Triage is deterministic and checks correctness first: a
thumbs-down on an answer that passed Module B's sentence checks, with no reason or "not what I
asked", is "correct but unwelcome" and changes nothing.

Fix ladder (8.3): 1 knowledge (curated answer, document action), 2 retrieval, 3 routing,
4 prompt/persona, 5 model adaptation (Level 2 only). The cheapest rung is always proposed.
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import date, datetime, timedelta, timezone

import numpy as np

from .assistant_client import Assistant, AssistantClient, AssistantError
from .pii import mask_pii
from .config import Settings
from .db import Jsonb, tx
from .embedder import Embedder, make_embedder

SEVERITY = {"wrong_fact": 3, "missing_knowledge": 2, "tone": 1}
POLICY_WORDS = [
    "refund",
    "reservation",
    "quota",
    "complaint",
    "grievance",
    "ragging",
    "harassment",
    "caste",
    "disability",
    "scam",
    "legal",
]
# F8.3 implicit signals from Module C. Rephrasing and asking for a person after an answer point at a
# missing or badly found answer; abandonment, voice hang-ups and barge-ins are too weak to act on
# alone and are only counted in the weekly report (8.6 repeat-question and post-answer handoff rates).
IMPLICIT = ("rephrase", "talk_to_human", "abandoned", "voice_hangup", "barge_in")
ACTIONABLE_IMPLICIT = ("rephrase", "talk_to_human")
# An implicit signal opens a proposal only once this many similar ones are waiting; until then it
# can still join a proposal opened by stronger evidence.
IMPLICIT_MIN_CLUSTER = 2
# root cause -> (proposal kind, rung, severity)
FIX = {
    "knowledge_gap": ("curated_answer", 1, SEVERITY["missing_knowledge"]),
    "hallucination": ("curated_answer", 1, SEVERITY["wrong_fact"]),
    "outdated": ("document_action", 1, SEVERITY["wrong_fact"]),
    "tone": ("prompt", 4, SEVERITY["tone"]),
    "policy": ("sensitive_topic", 1, SEVERITY["wrong_fact"]),
}


def now() -> datetime:
    return datetime.now(timezone.utc)


def add_working_days(start: datetime, days: int) -> datetime:
    d = start
    while days > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days -= 1
    return d


# ------------------------------------------------------------------ 1. signals


def erase_conversations(cur, conversation_ids: list[str]) -> dict:
    """Erasure on request (PRD 11.4): forget what these conversations said. Signal rows stay as
    anonymous counts so cluster sizes and impact do not jump; their text and links are removed."""
    if not conversation_ids:
        return {"signals": 0, "events": 0, "proposals": 0}
    cur.execute(
        "update learn.signals set question = '[erased]', answer = null, comment = null,"
        " corrected = null, conversation_id = null, message_id = null, trace_id = null"
        " where conversation_id = any(%s)",
        (conversation_ids,),
    )
    signals = cur.rowcount
    cur.execute(
        "update learn.events set payload = jsonb_build_object('erased', true)"
        " where payload->>'conversation_id' = any(%s)",
        (conversation_ids,),
    )
    events = cur.rowcount
    cur.execute(
        "update learn.proposals set examples = coalesce((select jsonb_agg(e) from"
        " jsonb_array_elements(examples) e where not (e->>'conversation_id' = any(%s))), '[]')"
        " where exists (select 1 from jsonb_array_elements(examples) e"
        "   where e->>'conversation_id' = any(%s))",
        (conversation_ids, conversation_ids),
    )
    return {"signals": signals, "events": events, "proposals": cur.rowcount}


def _from_b(assistant: Assistant, tenant: str, message_id: str | None) -> dict:
    if not message_id or message_id == "conversation":
        return {}
    try:
        return assistant.message(tenant, message_id) or {}
    except AssistantError:
        return {}


def ingest(settings: Settings, assistant: Assistant, tenant: str) -> dict:
    """Turn Module C's events and Module B's no-answer/blocked/partial turns into signals."""
    counts = {"events": 0, "b_turns": 0}
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select id, event_id, type, payload, received_at from learn.events where processed_at is null"
            " order by id"
        )
        events = cur.fetchall()
        cur.execute(
            "select max(occurred_at) filter (where kind in ('no_answer','blocked','partial')) as since,"
            " max(occurred_at) filter (where kind = 'ungrounded') as reviewed from learn.signals"
        )
        marks = cur.fetchone()
        since, reviewed = marks["since"], marks["reviewed"]
    rows = []
    for ev in events:
        p = ev["payload"]
        b = _from_b(assistant, tenant, p.get("message_id"))
        base = {
            "source_ref": f"event:{ev['event_id']}",
            "conversation_id": p.get("conversation_id"),
            "message_id": p.get("message_id"),
            "trace_id": p.get("trace_id")
            or (str(b["trace_id"]) if b.get("trace_id") else None),
            "question": b.get("question"),
            "answer": b.get("answer") or p.get("original_answer"),
            "department": p.get("department")
            or next(iter(p.get("departments") or []), None),
            "sources": b.get("sources") or [],
            "outcome": b.get("outcome"),
            "occurred_at": ev["received_at"],
        }
        if ev["type"] == "correction.submitted":
            if not base["question"] and p.get("conversation_id"):
                base["question"] = p.get("question")
            rows.append(
                {
                    **base,
                    "kind": "correction",
                    "corrected": p.get("corrected_answer"),
                    "reason": p.get("reason"),
                }
            )
        elif p.get("kind") == "thumb":
            rows.append(
                {
                    **base,
                    "kind": "thumb_down" if p.get("value") == -1 else "thumb_up",
                    "reason": p.get("reason"),
                    "comment": p.get("comment"),
                }
            )
        elif p.get("kind") == "rating" and int(p.get("value", 5)) <= 2:
            rows.append({**base, "kind": "low_rating", "comment": p.get("comment")})
        elif ev["type"] == "signal.logged" and p.get("kind") in IMPLICIT:
            rows.append(
                {
                    **base,
                    "kind": p["kind"],
                    "question": base["question"] or p.get("question"),
                    "channel": p.get("channel"),
                }
            )
    try:
        turns = assistant.signals(tenant, since.isoformat() if since else None)
    except AssistantError:
        turns = []
    try:
        reviews = assistant.unsupported(
            tenant, reviewed.isoformat() if reviewed else None
        )
    except AssistantError:
        reviews = []
    for rv in reviews:
        bad = [x for x in rv["sentences"] if x.get("supported") is False]
        rows.append(
            {
                "kind": "ungrounded",
                "source_ref": f"review:{rv['id']}",
                "conversation_id": rv["conversation_id"],
                "message_id": str(rv["id"]),
                "trace_id": str(rv["trace_id"]) if rv.get("trace_id") else None,
                "question": rv.get("question"),
                "answer": rv.get("answer"),
                "sources": [],
                "comment": "; ".join(x["sentence"] for x in bad)[:1000],
                "outcome": "answered",
                "occurred_at": rv["created_at"],
            }
        )
    for t in turns:
        rows.append(
            {
                "kind": t["outcome"],
                "source_ref": f"message:{t['id']}",
                "conversation_id": t["conversation_id"],
                "message_id": str(t["id"]),
                "trace_id": str(t["trace_id"]) if t.get("trace_id") else None,
                "question": t.get("question"),
                "answer": t.get("answer"),
                "sources": t.get("sources") or [],
                "outcome": t["outcome"],
                "occurred_at": t["created_at"],
            }
        )
    with tx(settings.database_url, tenant) as cur:
        for r in rows:
            cause, verdict = triage(r)
            cur.execute(
                "insert into learn.signals (tenant_id, kind, source_ref, conversation_id, message_id, trace_id,"
                " question, answer, corrected, reason, comment, department, sources, outcome, root_cause, verdict,"
                " occurred_at, channel) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " on conflict (tenant_id, kind, source_ref) do nothing",
                (
                    tenant,
                    r["kind"],
                    r["source_ref"],
                    r.get("conversation_id"),
                    r.get("message_id"),
                    r.get("trace_id"),
                    mask_pii(r.get("question")),
                    mask_pii(r.get("answer")),
                    mask_pii(r.get("corrected")),
                    r.get("reason"),
                    mask_pii(r.get("comment")),
                    r.get("department"),
                    Jsonb(r.get("sources") or []),
                    r.get("outcome"),
                    cause,
                    verdict,
                    r["occurred_at"],
                    r.get("channel"),
                ),
            )
            counts["events" if r["source_ref"].startswith("event:") else "b_turns"] += (
                cur.rowcount
            )
        if events:
            cur.execute(
                "update learn.events set processed_at = now() where id = any(%s)",
                ([e["id"] for e in events],),
            )
    return counts


def triage(s: dict) -> tuple[str | None, str]:
    """(root cause, verdict). Correctness first (8.2): Module B only releases sentences whose
    numbers match a cited source, so an answered, cited response is presumed correct unless the
    visitor says it is wrong or outdated."""
    kind, reason = s["kind"], (s.get("reason") or "")
    question = (s.get("question") or "").lower()
    answered = s.get("outcome") in ("answered", "partial") and bool(s.get("sources"))
    if kind in ("thumb_up", "low_rating"):
        return None, "unreviewed"
    if kind == "correction":
        # A staff correction carries the right answer, so its fix is always that answer (PRD 8.5:
        # expert correction -> curated Q&A), even when the reason says the old one was outdated.
        return ("hallucination" if answered else "knowledge_gap"), "wrong"
    if kind in ("no_answer", "blocked", "partial"):
        return "knowledge_gap", "unreviewed"
    if kind == "ungrounded":
        return "hallucination", "wrong"
    if kind in IMPLICIT:
        if kind not in ACTIONABLE_IMPLICIT:
            return (
                None,
                "unreviewed",
            )  # counted in the report, never a proposal on its own
        if (
            kind == "talk_to_human"
            and answered
            and any(w in question for w in POLICY_WORDS)
        ):
            return "policy", "unreviewed"
        return "knowledge_gap", "unreviewed"
    # thumbs-down from a visitor
    if answered and any(w in question for w in POLICY_WORDS):
        return "policy", "unreviewed"
    if reason == "outdated":
        return ("outdated" if answered else "knowledge_gap"), "wrong"
    if reason == "wrong":
        return ("outdated" if answered else "knowledge_gap"), "wrong"
    if reason == "incomplete":
        return "knowledge_gap", "other"
    if reason == "hard_to_understand":
        return "tone", "other"
    if answered:  # not_what_i_asked, other, or no reason
        return "correct_but_unwelcome", "correct_but_unwelcome"
    return "knowledge_gap", "other"


# ------------------------------------------------------------------ 2. clusters and proposals


def _embed(embedder: Embedder, text: str) -> np.ndarray:
    model = embedder.ml or embedder
    v = np.asarray(model.query(text), dtype=np.float32)
    return v / max(float(np.linalg.norm(v)), 1e-9)


def _target(s: dict) -> dict | None:
    """The source an 'outdated' report is about: the first cited non-catalogue source."""
    for src in s.get("sources") or []:
        if src.get("source_type") in ("document", "curated_qa") and src.get("ref_id"):
            return src
    return None


def _impact(occurred: list[datetime], severity: int, at: datetime) -> tuple[float, int]:
    """8.3: affected conversations in the last 7 days x severity x recency weight."""
    recent = [t for t in occurred if at - t <= timedelta(days=7)]
    if not recent:
        return 0.0, 0
    weight = sum(0.5 ** ((at - t).total_seconds() / 86400 / 7) for t in recent) / len(
        recent
    )
    return len(recent) * severity * weight, len(recent)


def _content(cause: str, signals: list[dict]) -> dict:
    s0 = signals[-1]
    q = (s0.get("question") or "(question not recorded)").strip()
    n = len(signals)
    if cause in ("knowledge_gap", "hallucination"):
        corrected = next(
            (s["corrected"] for s in reversed(signals) if s.get("corrected")), None
        )
        if not corrected and s0.get("kind") == "ungrounded":
            return {
                "title": f"Unsupported claim in an answer ({n}x): {q[:70]}",
                "proposal": "The grounding review found this not supported by the cited sources: "
                f"\"{(s0.get('comment') or '')[:300]}\". Approve with the correct answer in "
                "the note; it goes live only after the evaluation gate.",
                "diff": {"question": q, "answer": None, "old_answer": s0.get("answer")},
            }
        if corrected:
            return {
                "title": f"Expert correction: {q[:90]}",
                "proposal": corrected,
                "diff": {
                    "question": q,
                    "answer": corrected,
                    "old_answer": s0.get("answer"),
                },
            }
        return {
            "title": f"No reliable answer ({n}x): {q[:80]}",
            "proposal": "Add a curated answer. Approve with the answer text in the note; it goes live "
            "only after the evaluation gate.",
            "diff": {"question": q, "answer": None, "old_answer": s0.get("answer")},
        }
    if cause == "outdated" and _target(s0) is None:
        title = next(
            (x.get("title") for x in s0.get("sources") or []), "the program catalogue"
        )
        return {
            "title": f"Reported wrong or outdated ({n}x): {title}",
            "proposal": "The answer came from the program catalogue. Check the value with the admissions "
            "office and re-import the corrected catalogue (approving records this as a task).",
            "diff": {
                "action": "review_catalogue",
                "source_type": "catalogue",
                "title": title,
                "question": q,
            },
        }
    if cause == "outdated":
        t = _target(s0) or {}
        return {
            "title": f"Reported wrong or outdated ({n}x): {t.get('title', q[:60])}",
            "proposal": f"Expire '{t.get('title')}' so it is never cited again, then upload or write the "
            "current version.",
            "diff": {
                "action": "expire",
                "source_type": t.get("source_type"),
                "ref_id": t.get("ref_id"),
                "title": t.get("title"),
                "question": q,
            },
        }
    if cause == "tone":
        return {
            "title": f"Hard to understand ({n}x): {q[:80]}",
            "proposal": "Revise the persona prompt: shorter sentences, explain terms such as 'quota' and "
            "'counselling' for parents. A prompt change is applied by Module B's owner.",
            "diff": {"suggestion": "simpler wording for parents", "question": q},
        }
    if cause == "policy":
        word = next((w for w in POLICY_WORDS if w in q.lower()), "policy")
        return {
            "title": f"Sensitive topic answered by the assistant: {word}",
            "proposal": f"Add '{word}' to the sensitive topics so these questions always go to a person.",
            "diff": {"topic": word, "question": q},
        }
    raise ValueError(cause)


NEGATIVE = ("thumb_down", "low_rating")
HELD_IN_PEAK = SEVERITY[
    "wrong_fact"
]  # 8.3: in peak season only wrong facts go to the experts


def peak_season(cur) -> bool:
    cur.execute("select peak_season from learn.settings")
    row = cur.fetchone()
    return bool(row and row["peak_season"])


def held_in_peak(p: dict, peak: bool) -> bool:
    return peak and p["status"] == "pending" and p["severity"] < HELD_IN_PEAK


def _norm(q: str | None) -> str:
    return " ".join((q or "").lower().split())


def limit_bursts(cur, limit: int, window: timedelta = timedelta(hours=1)) -> int:
    """8.4: user feedback alone never changes an answer, and coordinated negative feedback is
    rate-limited. Of the new negative ratings of one question, at most `limit` per `window` count;
    the rest stay on record as 'suspected_abuse' and never reach a proposal."""
    cur.execute(
        "select id, question, occurred_at from learn.signals where proposal_id is null"
        " and kind = any(%s) and root_cause is not null order by occurred_at",
        (list(NEGATIVE),),
    )
    by_question: dict[str, list[dict]] = {}
    for s in cur.fetchall():
        by_question.setdefault(_norm(s["question"]), []).append(s)
    over: list = []
    for sigs in by_question.values():
        kept: list[datetime] = []
        for s in sigs:
            kept = [t for t in kept if s["occurred_at"] - t < window]
            if len(kept) >= limit:
                over.append(s["id"])
            else:
                kept.append(s["occurred_at"])
    if over:
        cur.execute(
            "update learn.signals set root_cause = null, verdict = 'suspected_abuse' where id = any(%s)",
            (over,),
        )
    return len(over)


def propose(
    settings: Settings, embedder: Embedder, tenant: str, at: datetime | None = None
) -> dict:
    """Attach new signals to a matching pending proposal (same root cause, similar question or same
    target document) or open a new one; then recompute impact for every pending proposal."""
    at = at or now()
    made = attached = 0
    with tx(settings.database_url, tenant) as cur:
        suppressed = limit_bursts(cur, settings.feedback_burst_limit)
        cur.execute(
            "select * from learn.signals where proposal_id is null and root_cause = any(%s) order by occurred_at",
            (list(FIX),),
        )
        pending_signals = cur.fetchall()
        cur.execute(
            "select id, root_cause, centroid, diff from learn.proposals where status = 'pending'"
        )
        open_props = cur.fetchall()
        for s in pending_signals:
            cause = s["root_cause"]
            kind, rung, severity = FIX[cause]
            match = None
            if cause == "outdated":
                tgt = (_target(s) or {}).get("ref_id")
                match = next(
                    (
                        p
                        for p in open_props
                        if p["root_cause"] == cause
                        and tgt
                        and p["diff"].get("ref_id") == tgt
                    ),
                    None,
                )
                vec = None
            else:
                vec = _embed(embedder, s["question"] or s["answer"] or "")
                best = 0.0
                for p in open_props:
                    if p["root_cause"] == cause and p["centroid"] is not None:
                        sim = float(
                            np.dot(vec, np.asarray(p["centroid"], dtype=np.float32))
                        )
                        if sim >= settings.cluster_similarity and sim > best:
                            best, match = sim, p
            if match is None and s["kind"] in ACTIONABLE_IMPLICIT:
                # Weak evidence: wait until a similar implicit signal is also waiting.
                similar = [
                    o
                    for o in pending_signals
                    if o is not s
                    and o["kind"] in ACTIONABLE_IMPLICIT
                    and o["root_cause"] == cause
                    and o.get("proposal_id") is None
                    and float(
                        np.dot(
                            vec, _embed(embedder, o["question"] or o["answer"] or "")
                        )
                    )
                    >= settings.cluster_similarity
                ]
                if len(similar) + 1 < IMPLICIT_MIN_CLUSTER:
                    continue
            if match is None:
                content = _content(cause, [s])
                cur.execute(
                    "insert into learn.proposals (tenant_id, kind, root_cause, rung, title, proposal, diff,"
                    " department, severity, centroid, due_at) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                    " returning id, root_cause, centroid, diff",
                    (
                        tenant,
                        kind,
                        cause,
                        rung,
                        content["title"],
                        content["proposal"],
                        Jsonb(content["diff"]),
                        s["department"],
                        severity,
                        vec.tolist() if vec is not None else None,
                        add_working_days(at, settings.approval_sla_working_days),
                    ),
                )
                match = cur.fetchone()
                open_props.append(match)
                made += 1
            else:
                attached += 1
                if vec is not None and match["centroid"] is not None:
                    c = np.asarray(match["centroid"], dtype=np.float32) + vec
                    match["centroid"] = (
                        c / max(float(np.linalg.norm(c)), 1e-9)
                    ).tolist()
                    cur.execute(
                        "update learn.proposals set centroid = %s where id = %s",
                        (match["centroid"], match["id"]),
                    )
            cur.execute(
                "update learn.signals set proposal_id = %s where id = %s",
                (match["id"], s["id"]),
            )
            s["proposal_id"] = match["id"]
        refreshed = refresh_pending(cur, at, settings.approval_sla_working_days)
    return {
        "new_proposals": made,
        "attached": attached,
        "pending": refreshed,
        "suppressed_as_burst": suppressed,
    }


def delegates(cur) -> dict:
    cur.execute("select delegates from learn.settings")
    row = cur.fetchone()
    return dict((row or {}).get("delegates") or {})


def delegate_for(names: dict, department: str | None) -> str | None:
    """The named delegate for a proposal's department, else the college-wide one (8.3)."""
    return (names.get(department) if department else None) or names.get("default")


def refresh_pending(cur, at: datetime, sla_days: int = 3) -> int:
    """Recompute content, examples and impact of pending proposals, and apply the approval SLA
    (8.3): past the due date a proposal goes to the named delegate for its department (with a new
    due date), and past that one to the College Admin. Without a delegate it goes straight to the
    College Admin."""
    cur.execute("select * from learn.proposals where status = 'pending'")
    props = cur.fetchall()
    peak = peak_season(cur)
    names = delegates(cur)
    for p in props:
        cur.execute(
            "select * from learn.signals where proposal_id = %s order by occurred_at",
            (p["id"],),
        )
        sigs = cur.fetchall()
        if not sigs:
            continue
        raw, affected = _impact([s["occurred_at"] for s in sigs], p["severity"], at)
        content = _content(p["root_cause"], sigs)
        examples = [
            {
                "question": s["question"],
                "answer": (s["answer"] or "")[:300],
                "reason": s["reason"],
                "kind": s["kind"],
                "conversation_id": s["conversation_id"],
                "trace_id": s["trace_id"],
                "at": s["occurred_at"].isoformat(),
            }
            for s in sigs[-5:]
        ]
        level, assigned, due, note = (
            p["escalation_level"],
            p["assigned_to"],
            p["due_at"],
            p["note"],
        )
        # A proposal held for peak season waits on purpose; it is not escalated meanwhile.
        if at > p["due_at"] and level < 2 and not held_in_peak(p, peak):
            delegate = delegate_for(names, p["department"]) if level == 0 else None
            if delegate:
                level, assigned = 1, delegate
                due = add_working_days(at, sla_days)
                note = f"Passed to the named delegate {delegate}: past the approval SLA"
            else:
                level, assigned = 2, "college_admin"
                note = "Escalated to the College Admin: past the approval SLA"
        to_admin = level == 2 and p["escalation_level"] < 2
        diff = {
            **content["diff"],
            **({"department": p["department"]} if to_admin else {}),
        }
        cur.execute(
            "update learn.proposals set title=%s, proposal=%s, diff=%s, examples=%s, affected=%s,"
            " impact_raw=%s, impact_score=%s, escalated = escalated or %s,"
            " department = case when %s then null else department end,"
            " escalation_level=%s, assigned_to=%s, due_at=%s, note=%s, updated_at=now() where id=%s",
            (
                content["title"],
                content["proposal"],
                Jsonb(diff),
                Jsonb(examples),
                max(affected, len(sigs)),
                raw,
                round(1 - math.exp(-raw / 6), 4),
                level > 0,
                to_admin,
                level,
                assigned,
                due,
                note,
                p["id"],
            ),
        )
    return len(props)


def public(p: dict) -> dict:
    """The shape Module C's approval inbox renders (plus the evidence behind it)."""
    return {
        "id": str(p["id"]),
        "kind": p["kind"],
        "root_cause": p["root_cause"],
        "rung": p["rung"],
        "department": p["department"],
        "impact_score": p["impact_score"],
        "title": p["title"],
        "proposal": p["proposal"],
        "evidence_count": p["affected"],
        "examples": p["examples"],
        "diff": p["diff"],
        "status": p["status"],
        "due_at": p["due_at"].isoformat(),
        "overdue": p["status"] == "pending" and p["due_at"] < now(),
        "escalated": p["escalated"],
        # 0 owning approver, 1 named delegate, 2 College Admin (8.3)
        "escalation_level": p.get("escalation_level", 2 if p["escalated"] else 0),
        "assigned_to": p.get("assigned_to"),
        "second_review": p.get("second_review"),
        "decided_by": p["decided_by"],
        "note": p["note"],
        "eval_run_id": str(p["eval_run_id"]) if p["eval_run_id"] else None,
        "created_at": p["created_at"].isoformat(),
    }


def run(
    settings: Settings, embedder: Embedder, assistant: Assistant, tenant: str
) -> dict:
    from . import health

    from . import gate

    report = {
        "recovered": gate.recover_interrupted(settings)
        if settings.admin_database_url
        else {}
    }
    report["ingested"] = ingest(settings, assistant, tenant)
    report["proposals"] = propose(settings, embedder, tenant)
    report["catalogue_conflicts"] = health.check_catalogue_conflicts(settings, tenant)
    from . import quality

    report["second_reviews"] = quality.sample_second_reviews(settings, tenant)
    with tx(settings.database_url, tenant) as cur:
        from . import curated, documents

        report["expired"] = {
            "curated": curated.expire_due(cur, date.today()),
            "documents": documents.expire_due(cur, date.today()),
        }
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Nightly Feedback Analyst run for one tenant"
    )
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("--tenant-id", required=True)
    a = ap.parse_args()
    s = Settings()
    if not s.database_url:
        sys.exit("Set KB_DATABASE_URL (source local.env.sh)")
    print(run(s, make_embedder(s), AssistantClient(s), a.tenant_id))
