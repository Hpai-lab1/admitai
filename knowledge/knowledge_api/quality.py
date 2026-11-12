"""Approval quality and reporting (PRD 8.3, 8.6, F7.7, F9.3).

- Second review: each month a random 10% of the proposals approved in the previous month is
  sampled for a second approver, to catch rubber-stamping (8.3). The second approver must be a
  different person from the one who approved it.
- Digest: the weekly top-10 e-mail for approvers (8.3, F7.7), as data. Module C owns e-mail and
  people, so it sends it; this module only says what goes in it.
- Quality report: the F9.3 figures for Module C's quality dashboard, per week.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from .config import Settings
from .db import tx
from .learning import held_in_peak, peak_season, public

SAMPLE_SHARE = 0.10
APPROVED = ("deployed", "approved_manual", "failed_gate", "rolled_back")


def previous_month(today: date) -> str:
    first = today.replace(day=1)
    return (first - timedelta(days=1)).strftime("%Y-%m")


def sample_second_reviews(
    settings: Settings, tenant: str, today: date | None = None, seed: str = ""
) -> dict:
    """Pick 10% (at least one) of last month's approved proposals for a second review. Runs at
    most once per month and tenant, so the nightly job can call it every night."""
    today = today or date.today()
    month = previous_month(today)
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select count(*) n from learn.proposals where second_review_month = %s",
            (month,),
        )
        if cur.fetchone()["n"]:
            return {"month": month, "sampled": 0, "already_done": True}
        start = date.fromisoformat(month + "-01")
        end = today.replace(day=1)
        cur.execute(
            "select id from learn.proposals where status = any(%s) and decided_at >= %s"
            " and decided_at < %s and second_review is null order by id",
            (list(APPROVED), start, end),
        )
        ids = [r["id"] for r in cur.fetchall()]
        if not ids:
            return {"month": month, "sampled": 0, "approved": 0}
        k = max(1, math.ceil(len(ids) * SAMPLE_SHARE))
        chosen = random.Random(f"{tenant}:{month}:{seed}").sample(ids, k)
        cur.execute(
            "update learn.proposals set second_review = 'pending', second_review_month = %s"
            " where id = any(%s)",
            (month, chosen),
        )
    return {"month": month, "sampled": k, "approved": len(ids)}


class SecondReviewError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def record_second_review(
    settings: Settings,
    tenant: str,
    proposal_id: str,
    agree: bool,
    reviewer: str,
    note: str | None,
) -> dict:
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select * from learn.proposals where id::text = %s for update",
            (proposal_id,),
        )
        p = cur.fetchone()
        if p is None:
            raise SecondReviewError(404, "Proposal not found")
        if p["second_review"] != "pending":
            raise SecondReviewError(
                409, "This proposal is not waiting for a second review"
            )
        if (
            p["decided_by"]
            and reviewer.strip().lower() == p["decided_by"].strip().lower()
        ):
            raise SecondReviewError(
                409, "The second review must be done by someone other than the approver"
            )
        cur.execute(
            "update learn.proposals set second_review = %s, second_reviewer = %s,"
            " second_review_note = %s, second_reviewed_at = now() where id = %s returning *",
            ("agreed" if agree else "disagreed", reviewer, note, p["id"]),
        )
        return public(cur.fetchone())


def second_review_summary(cur) -> dict:
    cur.execute(
        "select second_review_month as month,"
        " count(*) filter (where second_review = 'pending') pending,"
        " count(*) filter (where second_review = 'agreed') agreed,"
        " count(*) filter (where second_review = 'disagreed') disagreed"
        " from learn.proposals where second_review is not null"
        " group by second_review_month order by second_review_month desc limit 6"
    )
    rows = cur.fetchall()
    for r in rows:
        done = r["agreed"] + r["disagreed"]
        r["agreement_rate"] = round(r["agreed"] / done, 3) if done else None
    return {"months": rows, "pending": sum(r["pending"] for r in rows)}


# ------------------------------------------------------------------ weekly digest (8.3, F7.7)


# Proposal titles of these kinds end with a visitor's (masked) question, e.g.
# "No reliable answer (3x): <question>". The e-mail keeps only the part before the question.
VISITOR_TEXT_TITLES = (
    "No reliable answer",
    "Unsupported claim in an answer",
    "Expert correction",
    "Hard to understand",
)


def mail_title(p: dict) -> str:
    title = p.get("title") or ""
    head, sep, _ = title.partition(": ")
    if sep and head.split(" (")[0] in VISITOR_TEXT_TITLES:
        title = head
    if p.get("department"):
        title += f" - {p['department']}"
    return title


def digest(settings: Settings, tenant: str, top: int = 10) -> dict:
    """What the weekly approval e-mail says. No visitor text: examples are left out, and titles
    that end with a visitor's question are cut before it (the inbox shows the full proposal)."""
    now = datetime.now(timezone.utc)
    with tx(settings.database_url, tenant) as cur:
        peak = peak_season(cur)
        cur.execute(
            "select * from learn.proposals where status = 'pending' order by impact_score desc, created_at"
        )
        backlog = [p for p in cur.fetchall() if not held_in_peak(p, peak)]
        second = second_review_summary(cur)
    items = []
    for p in backlog[:top]:
        items.append(
            {
                "id": str(p["id"]),
                "title": mail_title(p),
                "root_cause": p["root_cause"],
                "department": p["department"],
                "impact_score": p["impact_score"],
                "evidence_count": p["affected"],
                "due_at": p["due_at"].isoformat(),
                "overdue": p["due_at"] < now,
                "assigned_to": p["assigned_to"],
                "escalation_level": p["escalation_level"],
            }
        )
    overdue = sum(p["due_at"] < now for p in backlog)
    oldest = max(
        ((now - p["created_at"]).total_seconds() / 86400 for p in backlog), default=0
    )
    lines = [
        f"{len(backlog)} improvement proposal(s) are waiting for approval"
        + (f", {overdue} past the 3-working-day target" if overdue else "")
        + ".",
        "",
    ]
    for i, it in enumerate(items, 1):
        flag = " (overdue)" if it["overdue"] else ""
        lines.append(f"{i}. {it['title']} - impact {it['impact_score']:.2f}{flag}")
    if second["pending"]:
        lines += [
            "",
            f"{second['pending']} approved proposal(s) are waiting for a second review this month.",
        ]
    lines += ["", "Open the approval inbox in the staff console to review them."]
    return {
        "subject": f"AdmitAI: {len(backlog)} proposal(s) to approve"
        + (f", {overdue} overdue" if overdue else ""),
        "text": "\n".join(lines),
        "items": items,
        "backlog": {
            "size": len(backlog),
            "overdue": overdue,
            "oldest_days": round(oldest, 1),
        },
        "second_reviews_pending": second["pending"],
        "peak_season": peak,
        "send": bool(backlog) or bool(second["pending"]),
    }


# ------------------------------------------------------------------ quality report (F9.3, 8.6)


def _week(d: datetime | date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def quality_report(
    settings: Settings, tenant: str, weeks: int = 12, today: date | None = None
) -> dict:
    """F9.3: acceptance rate, eval score per run, proposals opened/approved/deployed; plus the
    8.6 self-improvement figures that Module A can compute (implicit signals, regressions caught
    by the gate, time to fix, second-review agreement)."""
    today = today or date.today()
    # Exactly `weeks` ISO weeks, the current one included, starting on a Monday.
    start = today - timedelta(weeks=max(weeks, 1) - 1)
    since = start - timedelta(days=start.weekday())
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select kind, verdict, channel, occurred_at from learn.signals where occurred_at >= %s",
            (since,),
        )
        signals = cur.fetchall()
        cur.execute(
            "select status, created_at, decided_at, updated_at from learn.proposals"
            " where created_at >= %s or decided_at >= %s or updated_at >= %s",
            (since, since, since),
        )
        props = cur.fetchall()
        cur.execute(
            "select id, label, trigger, scores, passed, finished_at from learn.eval_runs"
            " where status = 'done' and finished_at >= %s order by finished_at",
            (since,),
        )
        runs = cur.fetchall()
        cur.execute(
            "select p.updated_at - min(s.occurred_at) as took, p.updated_at from learn.proposals p"
            " join learn.signals s on s.proposal_id = p.id where p.status = 'deployed'"
            " and p.updated_at >= %s group by p.id, p.updated_at",
            (since,),
        )
        fixes = cur.fetchall()
        second = second_review_summary(cur)

    by_week: dict[str, dict] = defaultdict(
        lambda: {
            "thumbs_up": 0,
            "thumbs_down": 0,
            "correct_but_unwelcome": 0,
            "rephrase": 0,
            "talk_to_human": 0,
            "abandoned": 0,
            "voice_hangup": 0,
            "barge_in": 0,
            "proposals_opened": 0,
            "proposals_approved": 0,
            "proposals_deployed": 0,
            "proposals_rejected": 0,
            "regressions_caught": 0,
        }
    )
    for i in range(
        max(weeks, 1)
    ):  # quiet weeks show as zeros, so the trend has no gaps
        by_week[_week(since + timedelta(weeks=i))]
    for s in signals:
        w = by_week[_week(s["occurred_at"])]
        if s["kind"] == "thumb_up":
            w["thumbs_up"] += 1
        elif s["kind"] == "thumb_down":
            if s["verdict"] == "correct_but_unwelcome":
                w["correct_but_unwelcome"] += 1
            else:
                w["thumbs_down"] += 1
        elif s["kind"] in w:
            w[s["kind"]] += 1
    for p in props:
        if p["created_at"].date() >= since:
            by_week[_week(p["created_at"])]["proposals_opened"] += 1
        if p["decided_at"] and p["decided_at"].date() >= since:
            w = by_week[_week(p["decided_at"])]
            if p["status"] in APPROVED:
                w["proposals_approved"] += 1
            if p["status"] == "rejected":
                w["proposals_rejected"] += 1
            if p["status"] == "failed_gate":
                w["regressions_caught"] += 1
        if p["status"] == "deployed" and p["updated_at"].date() >= since:
            by_week[_week(p["updated_at"])]["proposals_deployed"] += 1
    rows = []
    for key in sorted(by_week):
        w = by_week[key]
        rated = w["thumbs_up"] + w["thumbs_down"]
        rows.append(
            {
                "week": key,
                **w,
                # 3.5: correct-but-unwelcome answers are excluded from the acceptance rate
                "acceptance_rate": round(w["thumbs_up"] / rated, 3) if rated else None,
            }
        )
    took = sorted(f["took"].total_seconds() / 86400 for f in fixes)
    decided = [p for p in props if p["decided_at"]]
    approved = sum(p["status"] in APPROVED for p in decided)
    return {
        "from": since.isoformat(),
        "to": today.isoformat(),
        "weeks": rows,
        # F9.3 "eval score per release": every finished run, oldest first
        "eval_runs": [
            {
                "id": str(r["id"]),
                "label": r["label"],
                "trigger": r["trigger"],
                "finished_at": r["finished_at"].isoformat(),
                "passed": r["passed"],
                **{
                    k: (r["scores"] or {}).get(k)
                    for k in (
                        "accuracy",
                        "refusal",
                        "groundedness",
                        "routing",
                        "judge_accuracy",
                        "cases",
                    )
                },
            }
            for r in runs
        ],
        "proposals": {
            "approved_share": round(approved / len(decided), 3) if decided else None,
            "decided": len(decided),
        },
        "median_days_to_fix": round(took[len(took) // 2], 2) if took else None,
        "second_review": second,
    }
