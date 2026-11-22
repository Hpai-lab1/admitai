"""Content operations (PRD 8.3 currency rules, 17): catalogue-wins conflict check, 90-day unused
flag, weekly knowledge-base health and gap report, academic-year cut-over, degraded-mode export."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from .by_design import not_answerable_by_design
from . import curated
from .config import Settings
from .db import Jsonb, tx
from .learning import add_working_days, held_in_peak, peak_season, public

_AMOUNT = re.compile(
    r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)|([\d,]{4,}(?:\.\d+)?)\s*(?:inr|rupees|/-)",
    re.I,
)


def _amounts(text: str) -> set[Decimal]:
    out = set()
    for m in _AMOUNT.finditer(text):
        raw = (m.group(1) or m.group(2)).replace(",", "")
        try:
            v = Decimal(raw)
        except Exception:  # noqa: BLE001
            continue
        if v >= 100:
            out.add(v.normalize())
    return out


def _mentions(text: str, p: dict) -> bool:
    low = text.lower()
    return any(
        len(n) >= 2 and re.search(rf"(?<![\w.]){re.escape(n.lower())}(?!\w)", low)
        for n in [p["code"], p["name"], *p["aliases"]]
    )


def conflicts_in(cur, text: str) -> list[dict]:
    """Amounts in a staff correction that the catalogue does not have for the programme it names
    (catalogue wins, 8.3): shown to the person writing or approving it, with the catalogue's
    fees, so a fee that really changed is changed in the catalogue instead."""
    # Staff often write a fee without "INR" ("4,00,000"): bare figures count too, but not years,
    # phone numbers or small counts.
    quoted = _amounts(text) | {
        Decimal(n).normalize()
        for n in (
            m.replace(",", "") for m in re.findall(r"(?<![\d.])\d[\d,]{3,}", text)
        )
        if 1000 <= int(n) < 100_000_000 and not 1990 <= int(n) <= 2099
    }
    if not quoted:
        return []
    cur.execute("select * from kb.programs where status = 'approved'")
    out = []
    for p in cur.fetchall():
        if not _mentions(text, p):
            continue
        cur.execute(
            "select id, category, component, amount, period from kb.program_fees"
            " where program_id = %s and (valid_to is null or valid_to >= current_date)"
            " order by category, component",
            (p["id"],),
        )
        fees = cur.fetchall()
        wrong = quoted - {Decimal(f["amount"]).normalize() for f in fees}
        if fees and wrong:
            out.append(
                {
                    "program_id": str(p["id"]),
                    "code": p["code"],
                    "name": p["name"],
                    "quoted": sorted(f"{a:f}" for a in wrong),
                    "fees": [
                        {
                            "id": str(f["id"]),
                            "category": f["category"],
                            "component": f["component"],
                            "amount": f"{Decimal(f['amount']).normalize():f}",
                            "period": f["period"],
                        }
                        for f in fees
                    ],
                }
            )
    return out


def _fee_for(text: str, amount: str, fees: list[dict]) -> dict | None:
    """The catalogue fee a figure in a correction replaces: the fee whose component ("tuition",
    "hostel") is named nearest before the figure, its category if one is named, else general.
    None when it cannot be told (then nothing is changed automatically)."""
    low = text.lower()
    digits = re.sub(r"[^\d]", "", amount)
    pos = None
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", low):
        if re.sub(r"[^\d]", "", m.group().split(".")[0]) == digits.split(".")[0]:
            pos = m.start()
            break
    if pos is None:
        return None
    before = low[max(0, pos - 80) : pos]
    named = [
        (before.rfind(f["component"].lower()), f)
        for f in fees
        if before.rfind(f["component"].lower()) >= 0
    ]
    if not named:
        return None
    nearest = max(i for i, _ in named)
    options = [f for i, f in named if i == nearest]
    if len(options) > 1:
        options = [f for f in options if f["category"].lower() in before] or [
            f for f in options if f["category"] == "general"
        ]
    return options[0] if len(options) == 1 else None


def fee_changes(cur, text: str) -> list[dict]:
    """The official fees a correction changes: each figure the catalogue does not have that can
    be tied to one fee. Approving the correction changes these fees (after the gate), so the chat
    quotes the corrected fee; a figure that cannot be tied to a fee changes nothing."""
    out, seen = [], set()
    for c in conflicts_in(cur, text):
        for q in c["quoted"]:
            f = _fee_for(text, q, c["fees"])
            if f is None or f["id"] in seen:
                continue
            seen.add(f["id"])
            out.append(
                {
                    "fee_id": f["id"],
                    "program": c["name"],
                    "code": c["code"],
                    "component": f["component"],
                    "category": f["category"],
                    "old": f["amount"],
                    "new": q,
                }
            )
    return out


def _inr(amount) -> str:
    """250000 -> "2,50,000" (Indian grouping), as staff read fees."""
    d = Decimal(str(amount)).normalize()
    whole, _, frac = f"{d:f}".partition(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join([*groups, tail]) + (f".{frac.ljust(2, '0')}" if frac else "")


def _resolve_agreeing(cur) -> int:
    """The other way round: an answer this check paused agrees with the catalogue again (a fee
    change was rolled back), so it is approved again and its open conflict proposal closed."""
    cur.execute(
        "select q.* from kb.curated_qa q where q.status = 'suspended'"
        " and q.status_reason like 'amount % is not in the catalogue'"
    )
    paused = cur.fetchall()
    if not paused:
        return 0
    cur.execute(
        "select p.*, array_agg(f.amount) filter (where f.amount is not null) as amounts"
        " from kb.programs p left join kb.program_fees f on f.program_id = p.id"
        " where p.status = 'approved' group by p.id"
    )
    programs = cur.fetchall()
    back = 0
    for qa in paused:
        named = [
            p for p in programs if _mentions(qa["question"] + " " + qa["answer"], p)
        ]
        allowed = {Decimal(a).normalize() for p in named for a in (p["amounts"] or [])}
        if not named or _amounts(qa["answer"]) - allowed:
            continue
        try:
            curated.set_status(
                cur,
                str(qa["id"]),
                "approved",
                "catalogue-check",
                "agrees with the catalogue again",
            )
        except curated.CuratedError:  # past its valid_to date: stays paused
            continue
        cur.execute(
            "update learn.proposals set status = 'rejected', note = 'Closed: the answer agrees with"
            " the catalogue again', updated_at = now() where status = 'pending'"
            " and kind = 'curated_conflict' and diff->>'qa_id' = %s",
            (str(qa["id"]),),
        )
        back += 1
    return back


def check_catalogue_conflicts(settings: Settings, tenant: str) -> int:
    """'Catalogue wins' (8.3): an approved curated answer that names a program and quotes an amount
    the catalogue does not have for it is suspended at once and raised as a proposal; the catalogue
    value is used meanwhile. Runs on every catalogue import and nightly."""
    found = 0
    with tx(settings.database_url, tenant) as cur:
        _resolve_agreeing(cur)
        cur.execute(
            "select p.*, array_agg(f.amount) filter (where f.amount is not null) as amounts"
            " from kb.programs p left join kb.program_fees f on f.program_id = p.id"
            " where p.status = 'approved' group by p.id"
        )
        programs = cur.fetchall()
        if not programs:
            return 0
        cur.execute("select * from kb.curated_qa where status = 'approved'")
        for qa in cur.fetchall():
            quoted = _amounts(qa["answer"])
            if not quoted:
                continue
            named = [
                p for p in programs if _mentions(qa["question"] + " " + qa["answer"], p)
            ]
            if not named:
                continue
            allowed = {
                Decimal(a).normalize() for p in named for a in (p["amounts"] or [])
            }
            wrong = sorted(f"{a:f}" for a in quoted - allowed)
            if not wrong:
                continue
            curated.set_status(
                cur,
                str(qa["id"]),
                "suspended",
                "catalogue-check",
                f"amount {', '.join(wrong)} is not in the catalogue",
            )
            catalogue = {
                p["code"]: [f"{Decimal(a).normalize():f}" for a in (p["amounts"] or [])]
                for p in named
            }
            listed = "; ".join(
                f"{p['name']}: {', '.join(_inr(a) for a in (p['amounts'] or []))}"
                for p in named
            )
            cur.execute(
                "insert into learn.proposals (tenant_id, kind, root_cause, rung, title, proposal, diff, department,"
                " severity, affected, impact_raw, impact_score, due_at) values"
                " (%s,'curated_conflict','catalogue_conflict',1,%s,%s,%s,%s,3,1,3,0.4,%s)",
                (
                    tenant,
                    f"Curated answer disagrees with the catalogue: {qa['question'][:80]}",
                    f"The curated answer quotes {', '.join(_inr(w) for w in wrong)}, which is not in the"
                    f" official fee list ({listed}). It is paused and the official fee is used. Approve to"
                    " expire it, or reject if the amount is right.",
                    Jsonb(
                        {
                            "qa_id": str(qa["id"]),
                            "source_type": "curated_qa",
                            "ref_id": str(qa["id"]),
                            "quoted": wrong,
                            "catalogue": catalogue,
                            "question": qa["question"],
                        }
                    ),
                    qa["department"],
                    add_working_days(
                        datetime.now(timezone.utc), settings.approval_sla_working_days
                    ),
                ),
            )
            found += 1
    return found


def weekly_report(settings: Settings, tenant: str, today: date | None = None) -> dict:
    """PRD 17.2 KB health + F8.5 gap and improvement report, for one tenant and the last 7 days."""
    today = today or date.today()
    week_ago = today - timedelta(days=7)
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select title, valid_to, owner from kb.documents where status = 'approved' and valid_to is not null"
            " and valid_to <= %s order by valid_to",
            (today + timedelta(days=30),),
        )
        near_expiry = [
            {**r, "valid_to": r["valid_to"].isoformat()} for r in cur.fetchall()
        ]
        cur.execute(
            "select title from kb.documents where status = 'approved' and owner is null and department is null"
        )
        no_owner = [r["title"] for r in cur.fetchall()]
        cur.execute(
            "select question, status_reason from kb.curated_qa where status = 'suspended'"
        )
        conflicts = cur.fetchall()
        cur.execute(
            "select question, last_used_at from kb.curated_qa where status = 'approved' and"
            " coalesce(last_used_at, created_at) < now() - interval '90 days'"
        )
        unused = [r["question"] for r in cur.fetchall()]
        cur.execute(
            "select question, count(*) as n from learn.signals where root_cause = 'knowledge_gap'"
            " and occurred_at >= %s and question is not null group by question order by n desc limit 40",
            (week_ago,),
        )
        gaps = [
            g for g in cur.fetchall() if not not_answerable_by_design(g["question"])
        ][:10]
        cur.execute(
            "select kind, verdict, count(*) as n from learn.signals where occurred_at >= %s group by kind, verdict",
            (week_ago,),
        )
        counts = cur.fetchall()
        cur.execute(
            "select * from learn.proposals where status = 'pending' order by impact_score desc"
        )
        backlog = cur.fetchall()
        cur.execute(
            "select title, kind, decided_at, updated_at from learn.proposals where status = 'deployed'"
            " and updated_at >= %s order by updated_at desc",
            (week_ago,),
        )
        fixed = [
            {
                **r,
                "decided_at": r["decided_at"].isoformat() if r["decided_at"] else None,
                "updated_at": r["updated_at"].isoformat(),
            }
            for r in cur.fetchall()
        ]
        cur.execute(
            "select p.updated_at - min(s.occurred_at) as took from learn.proposals p join learn.signals s"
            " on s.proposal_id = p.id where p.status = 'deployed' group by p.id, p.updated_at"
        )
        took = sorted(r["took"].total_seconds() / 86400 for r in cur.fetchall())
        # 8.4: coordinated negative feedback, rate-limited by the Feedback Analyst
        cur.execute(
            "select question, count(*) as suppressed, min(occurred_at) as first_at from learn.signals"
            " where verdict = 'suspected_abuse' and occurred_at >= %s group by question order by suppressed desc",
            (week_ago,),
        )
        anomalies = [
            {**r, "first_at": r["first_at"].isoformat()} for r in cur.fetchall()
        ]
        peak = peak_season(cur)
        from .quality import second_review_summary

        second = second_review_summary(cur)
    up = sum(c["n"] for c in counts if c["kind"] == "thumb_up")
    down = sum(
        c["n"]
        for c in counts
        if c["kind"] == "thumb_down" and c["verdict"] != "correct_but_unwelcome"
    )
    unwelcome = sum(c["n"] for c in counts if c["verdict"] == "correct_but_unwelcome")
    now = datetime.now(timezone.utc)
    return {
        "week": {"from": week_ago.isoformat(), "to": today.isoformat()},
        # 3.5: acceptance = up / (up + down), excluding answers verified "correct but unwelcome"
        "acceptance_rate": round(up / (up + down), 3) if up + down else None,
        "feedback": {
            "thumbs_up": up,
            "thumbs_down": down,
            "correct_but_unwelcome": unwelcome,
        },
        "knowledge_gaps": gaps,
        "fixed_this_week": fixed,
        "median_days_to_fix": round(took[len(took) // 2], 2) if took else None,
        "approval_backlog": {
            "size": len(backlog),
            "oldest_days": round(
                max(
                    ((now - p["created_at"]).total_seconds() / 86400 for p in backlog),
                    default=0,
                ),
                1,
            ),
            "overdue": sum(p["due_at"] < now for p in backlog),
            "top10": [public(p) for p in backlog[:10]],  # the weekly digest (8.3)
            "held_for_peak_season": sum(held_in_peak(p, peak) for p in backlog),
        },
        "peak_season": peak,
        "feedback_anomalies": anomalies,
        # F8.3 implicit signals this week (8.6 repeat-question and post-answer handoff counts)
        "implicit_signals": {
            k: sum(c["n"] for c in counts if c["kind"] == k)
            for k in (
                "rephrase",
                "talk_to_human",
                "abandoned",
                "voice_hangup",
                "barge_in",
            )
        },
        "second_review": second,
        "kb_health": {
            "documents_near_expiry": near_expiry,
            "documents_without_owner": no_owner,
            "conflicts": conflicts,
            "curated_unused_90_days": unused,
        },
    }


def cutover(
    settings: Settings, tenant: str, old_year: str, new_year: str, new_valid_to: date
) -> dict:
    """PRD 17.3 cut-over day: old-year documents and curated answers expire; curated answers that
    may still apply are re-issued as drafts for the new year, for one-click re-approval."""
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "update kb.documents set status='expired', status_reason=%s, updated_at=now()"
            " where academic_year = %s and status in ('approved','draft','needs_review')",
            (f"academic year {old_year} ended", old_year),
        )
        docs = cur.rowcount
        # Only the year and dates change, so the stored vectors stay valid; each answer gets a new
        # version (kept in its history) and must be re-approved.
        cur.execute(
            "update kb.curated_qa set academic_year=%s, valid_to=%s, version=version+1, status='draft',"
            " status_reason=%s, updated_at=now() where academic_year=%s and status in ('approved','suspended')"
            " returning *",
            (
                new_year,
                new_valid_to,
                f"re-issued for {new_year}: confirm it still applies",
                old_year,
            ),
        )
        rows = cur.fetchall()
        for row in rows:
            curated._history(cur, row, "cutover", "cutover")
    return {"documents_expired": docs, "curated_reissued_as_drafts": len(rows)}


def degraded_export(settings: Settings, tenant: str, top: int = 30) -> dict:
    """PRD 10.2 degraded mode: the most-used approved curated answers, for a static edge page."""
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select question, answer, academic_year, valid_to from kb.curated_qa where status = 'approved'"
            " and valid_to >= current_date order by last_used_at desc nulls last, updated_at desc limit %s",
            (top,),
        )
        rows = cur.fetchall()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": [{**r, "valid_to": r["valid_to"].isoformat()} for r in rows],
    }
