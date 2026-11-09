"""Dashboard numbers (PRD F9.1-F9.4), using the metric definitions in PRD 3.5. Every query runs under the caller's RLS context, so a
faculty member's numbers cover only their departments and marketing sees no handoff details."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from ..config import Settings
from ..clients import UpstreamError, assistant_analytics, knowledge_get
from ..db import Context, Jsonb, tx
from ..deps import require_staff, settings_dep

router = APIRouter(tags=["analytics"])
ASSISTANT_ROLES = (
    "college_admin",
    "admission_officer",
    "marketing_manager",
    "super_admin",
)


def _one(cur, sql: str, params: tuple = ()) -> dict:
    cur.execute(sql, params)
    return dict(cur.fetchone() or {})


CONVERSATIONS_SQL = """
with conv as (
    select conversation_id,
           count(*) filter (where kind = 'user_message') as user_msgs,
           max(created_at) filter (where kind = 'answer') as last_answer_at,
           max(created_at) filter (where kind = 'user_message') as last_user_at,
           (array_agg(created_at order by created_at desc)
               filter (where kind = 'user_message'))[2] as second_last_user_at,
           (array_agg(message_id order by created_at desc)
               filter (where kind = 'answer'))[1] as final_answer,
           bool_or(kind = 'talk_to_human') as asked_human,
           bool_or(channel = 'web_voice') as voice,
           false as whatsapp
    from app.conversation_events
    where created_at >= %(since)s
    group by conversation_id
    union all
    -- Conversations that never pass through the widget (WhatsApp), as Module B reports them.
    select conversation_id, user_msgs, last_answer_at, last_user_at, second_last_user_at,
           final_answer, asked_human, false, channel = 'whatsapp'
    from jsonb_to_recordset(%(off_widget)s::jsonb) as b(
        conversation_id text, channel text, user_msgs int, last_answer_at timestamptz,
        last_user_at timestamptz, second_last_user_at timestamptz, final_answer text,
        asked_human boolean)
), flags as (
    select c.*,
           c.user_msgs >= 2 as qualifying,
           c.asked_human or exists (select 1 from app.handoffs h
                                    where h.conversation_id = c.conversation_id) as handed_off,
           exists (select 1 from app.feedback f where f.message_id = c.final_answer
                   and f.conversation_id = c.conversation_id
                   and f.kind = 'thumb' and f.value = -1) as final_down,
           exists (select 1 from app.feedback f where f.message_id = c.final_answer
                   and f.conversation_id = c.conversation_id) as final_rated,
           exists (select 1 from app.conversation_events e
                   where e.conversation_id = c.conversation_id and e.kind = 'rephrase'
                     and e.created_at >= coalesce(c.second_last_user_at, '-infinity')) as late_rephrase,
           exists (select 1 from app.leads l where l.source->>'conversation_id' = c.conversation_id
                   and l.anonymised_at is null) as has_lead
    from conv c
)
select count(*) as total,
       count(*) filter (where qualifying) as qualifying,
       count(*) filter (where not qualifying) as single_message,
       count(*) filter (where qualifying and not handed_off and not final_down
                        and not late_rephrase) as resolved,
       count(*) filter (where qualifying and handed_off) as handed_off,
       count(*) filter (where qualifying and has_lead) as with_lead,
       count(*) filter (where final_answer is not null and not final_rated
                        and last_answer_at < now() - interval '10 minutes'
                        and (last_user_at is null or last_user_at < last_answer_at)) as abandoned,
       count(*) filter (where voice) as voice,
       count(*) filter (where whatsapp) as whatsapp
from flags
"""


def _rate(n, d):
    return round(n / d, 3) if d else None


def _conversations(cur, since, off_widget: list[dict] | None = None) -> dict:
    """PRD 3.5: qualifying = 2+ user messages; resolved = qualifying, no handoff, no thumbs-down on
    the final answer, no rephrase in the last 2 turns; abandoned = unrated answer, no reply in 10 min.
    `off_widget` adds Module B's WhatsApp conversations, which send no widget events."""
    c = _one(
        cur, CONVERSATIONS_SQL, {"since": since, "off_widget": Jsonb(off_widget or [])}
    )
    q = c["qualifying"]
    c["resolution_rate"] = _rate(c["resolved"], q)
    c["handoff_rate"] = _rate(c["handed_off"], q)
    c["lead_capture_rate"] = _rate(c["with_lead"], q)
    c["abandonment_rate"] = _rate(c["abandoned"], c["total"])
    c["voice_share"] = _rate(c["voice"], c["total"])
    c["whatsapp_share"] = _rate(c["whatsapp"], c["total"])
    return c


@router.get("/analytics/overview", summary="Dashboard data for your role")
def overview(
    days: int = Query(30, ge=1, le=365),
    ctx: Context = Depends(require_staff()),
    settings: Settings = Depends(settings_dep),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    out: dict = {"period_days": days, "since": since, "role": ctx.role}
    with tx(settings.database_url, ctx) as cur:
        out["sessions"] = _one(
            cur,
            "select count(*) as total,"
            " count(*) filter (where channel = 'web_voice') as voice"
            " from app.visitor_sessions where created_at >= %s",
            (since,),
        )
        # Module B's numbers are not split by department, so faculty keep the widget-only view.
        b = (
            assistant_analytics(settings, ctx.tenant_id, since)
            if ctx.role in ASSISTANT_ROLES
            else None
        )
        out["conversations"] = _conversations(cur, since, b and b["conversations"])
        if ctx.role in ASSISTANT_ROLES:
            # None means Module B was unreachable: the dashboard says so instead of showing
            # WhatsApp as zero.
            out["assistant"] = b and {"by_channel": b["by_channel"]}
        fb = _one(
            cur,
            "select count(*) filter (where kind = 'rating') as ratings,"
            " count(*) filter (where kind = 'rating' and value >= 4) as satisfied,"
            " count(*) filter (where kind = 'thumb' and value = 1) as helpful,"
            " count(*) filter (where kind = 'thumb' and value = -1) as not_helpful,"
            " round(avg(value) filter (where kind = 'rating'), 2) as avg_rating"
            " from app.feedback where created_at >= %s",
            (since,),
        )
        rated = (fb["helpful"] or 0) + (fb["not_helpful"] or 0)
        fb["helpful_rate"] = (
            round(fb["helpful"] / rated, 3) if rated else None
        )  # acceptance rate
        fb["csat"] = (
            round(fb["satisfied"] / fb["ratings"], 3) if fb["ratings"] else None
        )
        cur.execute(
            "select reason, count(*) as n from app.feedback where created_at >= %s and reason is not null"
            " group by reason order by n desc",
            (since,),
        )
        fb["reasons"] = cur.fetchall()
        out["feedback"] = fb

        if ctx.role in ("college_admin", "admission_officer", "faculty"):
            h = _one(
                cur,
                "select count(*) filter (where status <> 'resolved') as open,"
                " count(*) filter (where status <> 'resolved' and sla_due_at < now()) as overdue,"
                " count(*) filter (where status = 'resolved' and created_at >= %s) as resolved,"
                " count(*) filter (where status = 'resolved' and created_at >= %s and resolved_at <= sla_due_at)"
                "   as resolved_in_time,"
                " round(extract(epoch from percentile_cont(0.5) within group (order by resolved_at - created_at)"
                "   filter (where status = 'resolved' and created_at >= %s)) / 3600.0, 1) as median_hours"
                " from app.handoffs",
                (since, since, since),
            )
            h["sla_met_rate"] = (
                round(h["resolved_in_time"] / h["resolved"], 3)
                if h["resolved"]
                else None
            )
            cur.execute(
                "select coalesce(department, 'Admissions') as queue, count(*) as n from app.handoffs"
                " where created_at >= %s group by 1 order by n desc",
                (since,),
            )
            h["by_queue"] = cur.fetchall()
            out["handoffs"] = h
            out["corrections"] = _one(
                cur,
                "select count(*) as submitted,"
                " count(*) filter (where status = 'accepted') as accepted"
                " from app.corrections where created_at >= %s",
                (since,),
            )

        if ctx.role in ("college_admin", "admission_officer", "marketing_manager"):
            leads = _one(
                cur,
                "select count(*) as total,"
                " count(*) filter (where band = 'hot') as hot, count(*) filter (where band = 'warm') as warm,"
                " count(*) filter (where band = 'cold') as cold,"
                " count(*) filter (where is_minor) as via_guardian"
                " from app.leads where created_at >= %s and anonymised_at is null",
                (since,),
            )
            cur.execute(
                "select stage, count(*) as n from app.leads where created_at >= %s and anonymised_at is null"
                " group by stage",
                (since,),
            )
            leads["funnel"] = {r["stage"]: r["n"] for r in cur.fetchall()}
            cur.execute(
                "select coalesce(source->'utm'->>'utm_source', 'direct') as source, count(*) as n,"
                " count(*) filter (where stage in ('applied', 'admitted')) as applied"
                " from app.leads where created_at >= %s and anonymised_at is null group by 1 order by n desc",
                (since,),
            )
            leads["by_source"] = cur.fetchall()
            sessions = out["sessions"]["total"] or 0
            leads["capture_rate"] = (
                round(leads["total"] / sessions, 3) if sessions else None
            )
            out["leads"] = leads
    return out


QUALITY_ROLES = ("college_admin", "admission_officer")


@router.get(
    "/analytics/quality",
    summary="Quality over time: acceptance rate, implicit signals, proposals and eval score per "
    "run, by week (PRD F9.3; from Module A)",
)
def quality(
    weeks: int = Query(12, ge=1, le=52),
    ctx: Context = Depends(require_staff(*QUALITY_ROLES)),
    settings: Settings = Depends(settings_dep),
):
    try:
        data = knowledge_get(
            settings, "/reports/quality", ctx.tenant_id, {"weeks": weeks}
        )
    except UpstreamError:
        # Shown as "unavailable" on the dashboard, never as zeros.
        return {"available": False}
    return {"available": True, **data}


@router.get(
    "/analytics/topics",
    summary="Questions the assistant could not answer this week, most asked first (PRD F9.2; "
    "from Module A, personal details masked)",
)
def topics(
    ctx: Context = Depends(require_staff(*QUALITY_ROLES)),
    settings: Settings = Depends(settings_dep),
):
    try:
        report = knowledge_get(settings, "/reports/weekly", ctx.tenant_id)
    except UpstreamError:
        return {"available": False}
    return {
        "available": True,
        "week": report.get("week"),
        "unanswered": [
            {"question": g.get("question"), "count": g.get("n")}
            for g in report.get("knowledge_gaps") or []
        ],
    }
