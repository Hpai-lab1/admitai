"""Human handoff and callback queue (PRD F3.3-F3.6, F1.7). Module B creates handoffs with the
context package; visitors ask for a callback; staff claim, reply and resolve. Who sees which task
is decided by the RLS policies (admission queue for officers, faculty queue by department)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from .. import messaging
from ..clients import assistant_conversation, dispatch_events, handoff_package
from ..config import Settings
from ..webhooks import emit, handoff_data
from ..db import Context, Jsonb, audit, tx
from ..deps import require_staff, service_context, settings_dep, visitor_context
from ..schemas import CallbackAck, CallbackIn, Handoff, HandoffIn, ReplyIn

router = APIRouter(tags=["handoffs"])
# super_admin is listed but sees rows only during break-glass (RLS, migration 004)
QUEUE_STAFF = require_staff(
    "super_admin", "college_admin", "admission_officer", "faculty"
)

SELECT = """
select h.*, n.full_name as assigned_name,
       case when l.id is null then null else jsonb_build_object(
           'id', l.id, 'name', l.name, 'phone', l.phone, 'email', l.email, 'city', l.city,
           'is_minor', l.is_minor, 'guardian_contact', l.guardian_contact, 'program_interest', l.program_interest,
           'band', l.band, 'score', l.score, 'stage', l.stage) end as lead
from app.handoffs h
left join app.staff_names() n on n.id = h.assigned_to
left join app.leads l on l.id = h.lead_id and l.anonymised_at is null
"""


def _tenant_config(cur) -> dict:
    cur.execute("select config from app.tenants where id = app.current_tenant()")
    row = cur.fetchone()
    return (row or {}).get("config") or {}


def _audience(queue: str, department: str | None) -> str:
    return f"faculty:{department}" if queue == "faculty" else "admission"


def _to_model(
    row: dict, events: list[dict] | None = None, feedback: list[dict] | None = None
) -> Handoff:
    overdue = row["status"] != "resolved" and row["sla_due_at"] < datetime.now(
        timezone.utc
    )
    return Handoff(
        **{
            k: row[k]
            for k in Handoff.model_fields
            if k in row and k not in ("overdue", "events", "feedback")
        },
        overdue=overdue,
        events=events or [],
        feedback=feedback or [],
    )


def _load(cur, handoff_id: UUID) -> dict:
    cur.execute(SELECT + " where h.id = %s", (handoff_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Task not found, or not in your queue"
        )
    return row


def _create(
    cur,
    ctx: Context,
    settings: Settings,
    *,
    conversation_id: str,
    session_id: UUID | None,
    queue: str,
    department: str | None,
    kind: str,
    reason: str,
    language: str,
    context: dict,
    preferred_time: str | None,
    preferred_channel: str | None,
    lead_id: UUID | None,
) -> dict:
    if queue == "faculty" and not department:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "A faculty question needs a department",
        )
    cfg = _tenant_config(cur)
    known = cfg.get("departments") or []
    if department and known and department not in known:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown department: {department}"
        )
    sla = datetime.now(timezone.utc) + timedelta(
        hours=int(cfg.get("response_time_hours") or 24)
    )
    cur.execute(
        "insert into app.handoffs (tenant_id, conversation_id, session_id, queue, department, kind, reason,"
        " language, context, preferred_time, preferred_channel, lead_id, sla_due_at)"
        " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
        (
            ctx.tenant_id,
            conversation_id,
            session_id,
            queue,
            department,
            kind,
            reason,
            language,
            Jsonb(context),
            preferred_time,
            preferred_channel,
            lead_id,
            sla,
        ),
    )
    hid = cur.fetchone()["id"]
    cur.execute(
        "insert into app.handoff_events (tenant_id, handoff_id, kind, body) values (%s, %s, 'created', %s)",
        (ctx.tenant_id, hid, reason),
    )
    cur.execute(
        "insert into app.notifications (tenant_id, audience, kind, payload) values (%s, %s, %s, %s)",
        (
            ctx.tenant_id,
            _audience(queue, department),
            f"{kind}.created",
            Jsonb({"handoff_id": str(hid), "reason": reason, "department": department}),
        ),
    )
    # E-mail to the queue (PRD F7.5): which queue and a link, never the applicant's words.
    messaging.queue(
        cur,
        ctx.tenant_id,
        f"{kind}_created",
        f"New {kind} in the {department or 'Admissions'} queue",
        f"A new {kind} is waiting, due by {sla:%d %b %Y, %H:%M} UTC.\n\n"
        f"{settings.web_origin.rstrip('/')}/console/handoffs/{hid}\n",
        audience=_audience(queue, department),
        handoff_id=hid,
    )
    audit(
        cur,
        ctx,
        f"{kind}.created",
        "handoff",
        hid,
        after={"queue": queue, "department": department},
    )
    emit(cur, "handoff.created", handoff_data(_load(cur, hid)), lead_id=lead_id)
    return {"id": hid, "sla_due_at": sla}


@router.post(
    "/handoffs",
    response_model=Handoff,
    status_code=201,
    summary="Create a handoff or callback task with its context package (called by Module B)",
)
def create_handoff(
    body: HandoffIn,
    ctx: Context = Depends(service_context),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, ctx) as cur:
        made = _create(
            cur,
            ctx,
            settings,
            conversation_id=body.conversation_id,
            session_id=body.session_id,
            queue=body.queue,
            department=body.department,
            kind=body.kind,
            reason=body.reason,
            language=body.language,
            context=body.context.model_dump(),
            preferred_time=body.preferred_time,
            preferred_channel=body.preferred_channel,
            lead_id=body.lead_id,
        )
        return _to_model(_load(cur, made["id"]))


def has_contact_consent(cur, session_id: UUID) -> bool:
    cur.execute(
        "select 1 from app.consents where session_id = %s and purpose = 'contact' and granted"
        " and withdrawn_at is null limit 1",
        (session_id,),
    )
    return cur.fetchone() is not None


@router.post(
    "/callbacks",
    response_model=CallbackAck,
    status_code=201,
    tags=["widget"],
    summary="Visitor asks for a person to call back (needs contact consent first)",
)
def request_callback(
    body: CallbackIn,
    ctx: Context = Depends(visitor_context),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, ctx) as cur:
        if not has_contact_consent(cur, ctx.session_id):
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Please agree to be contacted first"
            )
        cur.execute(
            "select id, email, phone, is_minor, guardian_contact from app.leads where session_id = %s"
            " and anonymised_at is null order by created_at desc limit 1",
            (ctx.session_id,),
        )
        lead = cur.fetchone()
        if not lead:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Please share how we can reach you first"
            )
        # Prefer Module B's own record of the conversation (with the AI draft, sources and trace);
        # the widget's transcript is the fallback when B is unavailable. A visitor can only pull a
        # conversation that belongs to their own session.
        view = assistant_conversation(settings, ctx.tenant_id, body.conversation_id)
        if view and view.get("session_id") == str(ctx.session_id):
            context = handoff_package(view)
            language = view.get("language") or body.language
        else:
            context = {"transcript": [t.model_dump() for t in body.transcript]}
            language = body.language
        made = _create(
            cur,
            ctx,
            settings,
            conversation_id=body.conversation_id,
            session_id=ctx.session_id,
            queue=body.queue,
            department=body.department,
            kind="callback",
            reason=body.reason,
            language=language,
            context=context,
            preferred_time=body.preferred_time,
            preferred_channel=body.preferred_channel,
            lead_id=lead["id"],
        )
        due = made["sla_due_at"]
        to = messaging.applicant_email(lead)
        wa = messaging.applicant_whatsapp(lead, body.preferred_channel)
        if to or wa:  # acknowledgement with the expected time (PRD F3.4)
            college = messaging.college_name(cur)
            text = (
                f"Thank you for contacting {college}. Someone from the admissions team will contact"
                f" you by {due:%d %b %Y, %H:%M} UTC"
                + (f" ({body.preferred_time})" if body.preferred_time else "")
                + f".\n\n{college}\n"
            )
            for addr, channel in ((to, "email"), (wa, "whatsapp")):
                if addr:
                    messaging.queue(
                        cur,
                        ctx.tenant_id,
                        "callback_ack",
                        f"{college}: we have your request",
                        text,
                        to=addr,
                        lead_id=lead["id"],
                        handoff_id=made["id"],
                        channel=channel,
                    )
    return CallbackAck(
        handoff_id=made["id"],
        expected_response_by=due,
        message=f"Thank you. Someone from the college will contact you by {due:%d %b %Y, %H:%M} UTC.",
    )


@router.get(
    "/handoffs",
    response_model=list[Handoff],
    summary="Queue of handoffs and callbacks for your role and departments",
)
def list_handoffs(
    status_: Literal["open", "claimed", "resolved", "active"] = Query(
        "active", alias="status"
    ),
    queue: Literal["admission", "faculty"] | None = None,
    department: str | None = None,
    mine: bool = False,
    limit: int = Query(100, ge=1, le=500),
    ctx: Context = Depends(QUEUE_STAFF),
    settings: Settings = Depends(settings_dep),
):
    where, params = [], []
    if status_ == "active":
        where.append("h.status <> 'resolved'")
    else:
        where.append("h.status = %s")
        params.append(status_)
    if queue:
        where.append("h.queue = %s")
        params.append(queue)
    if department:
        where.append("h.department = %s")
        params.append(department)
    if mine:
        where.append("h.assigned_to = %s")
        params.append(ctx.profile_id)
    sql = SELECT + " where " + " and ".join(where) + " order by h.sla_due_at limit %s"
    with tx(settings.database_url, ctx) as cur:
        cur.execute(sql, (*params, limit))
        return [_to_model(r) for r in cur.fetchall()]


@router.get(
    "/handoffs/{handoff_id}",
    response_model=Handoff,
    summary="One task with its history",
)
def get_handoff(
    handoff_id: UUID,
    ctx: Context = Depends(QUEUE_STAFF),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, ctx) as cur:
        row = _load(cur, handoff_id)
        cur.execute(
            "select e.kind, e.body, e.at, n.full_name as actor from app.handoff_events e"
            " left join app.staff_names() n on n.id = e.actor_id where e.handoff_id = %s order by e.id",
            (handoff_id,),
        )
        events = [dict(r) for r in cur.fetchall()]
        # feedback received in this conversation (F7.2)
        cur.execute(
            "select kind, value, reason, comment, created_at from app.feedback"
            " where conversation_id = %s order by created_at",
            (row["conversation_id"],),
        )
        feedback = [dict(r) for r in cur.fetchall()]
    return _to_model(row, events, feedback)


def _event(
    cur, ctx: Context, handoff_id: UUID, kind: str, body: str | None = None
) -> None:
    cur.execute(
        "insert into app.handoff_events (tenant_id, handoff_id, actor_id, kind, body) values (%s, %s, %s, %s, %s)",
        (ctx.tenant_id, handoff_id, ctx.profile_id, kind, body),
    )


@router.post(
    "/handoffs/{handoff_id}/claim",
    response_model=Handoff,
    summary="Take ownership of a task",
)
def claim(
    handoff_id: UUID,
    ctx: Context = Depends(QUEUE_STAFF),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, ctx) as cur:
        row = _load(cur, handoff_id)
        if row["status"] == "resolved":
            raise HTTPException(
                status.HTTP_409_CONFLICT, "This task is already resolved"
            )
        if row["assigned_to"] and row["assigned_to"] != ctx.profile_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Already claimed by {row['assigned_name'] or 'someone else'}",
            )
        cur.execute(
            "update app.handoffs set status = 'claimed', assigned_to = %s, updated_at = now() where id = %s",
            (ctx.profile_id, handoff_id),
        )
        _event(cur, ctx, handoff_id, "claimed")
        audit(cur, ctx, "handoff.claimed", "handoff", handoff_id)
        return _to_model(_load(cur, handoff_id))


@router.post(
    "/handoffs/{handoff_id}/reply",
    response_model=Handoff,
    summary="Reply on a task; optionally offer the reply as a correction (PRD F3.6)",
)
def reply(
    handoff_id: UUID,
    body: ReplyIn,
    background: BackgroundTasks,
    ctx: Context = Depends(QUEUE_STAFF),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, ctx) as cur:
        row = _load(cur, handoff_id)
        if row["status"] == "resolved":
            raise HTTPException(
                status.HTTP_409_CONFLICT, "This task is already resolved"
            )
        if row["assigned_to"] is None:
            cur.execute(
                "update app.handoffs set status = 'claimed', assigned_to = %s, updated_at = now()"
                " where id = %s",
                (ctx.profile_id, handoff_id),
            )
            _event(cur, ctx, handoff_id, "claimed")
        _event(cur, ctx, handoff_id, "replied", body.body)
        if body.send_email:
            lead = row["lead"]
            to = messaging.applicant_email(lead)
            if not to:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "There is no e-mail address for this applicant; please phone them",
                )
            college = messaging.college_name(cur)
            messaging.queue(
                cur,
                ctx.tenant_id,
                "staff_reply",
                f"{college}: an answer to your question",
                f"{body.body}\n\n{college} admissions\n",
                to=to,
                lead_id=lead["id"],
                handoff_id=handoff_id,
            )
            _event(cur, ctx, handoff_id, "emailed", "Reply e-mailed to the applicant")
        if body.send_whatsapp:
            lead = row["lead"]
            wa = messaging.staff_reply_whatsapp(lead)
            if not wa:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "There is no WhatsApp number for this applicant; please phone or e-mail them",
                )
            college = messaging.college_name(cur)
            messaging.queue(
                cur,
                ctx.tenant_id,
                "staff_reply",
                "",
                f"{body.body}\n\n{college} admissions",
                to=wa,
                lead_id=lead["id"],
                handoff_id=handoff_id,
                channel="whatsapp",
            )
            _event(
                cur,
                ctx,
                handoff_id,
                "messaged",
                "Reply queued for WhatsApp (sent through the college's WhatsApp number)",
            )
        if body.as_correction:
            ctx_pkg = row["context"] or {}
            cur.execute(
                "insert into app.corrections (tenant_id, conversation_id, message_id, original_answer,"
                " corrected_answer, reason, scope, author_id, trace_id) values (%s, %s, %s, %s, %s, %s, 'general', %s, %s)"
                " returning id",
                (
                    ctx.tenant_id,
                    row["conversation_id"],
                    body.message_id or f"handoff:{handoff_id}",
                    ctx_pkg.get("ai_draft"),
                    body.body,
                    f"Staff reply to: {row['reason']}",
                    ctx.profile_id,
                    ctx_pkg.get("trace_id"),
                ),
            )
            cid = cur.fetchone()["id"]
            cur.execute(
                "insert into app.outbox_events (tenant_id, type, payload) values (%s, 'correction.submitted', %s)",
                (
                    ctx.tenant_id,
                    Jsonb(
                        {
                            "correction_id": str(cid),
                            "conversation_id": row["conversation_id"],
                            "corrected_answer": body.body,
                            "original_answer": ctx_pkg.get("ai_draft"),
                            "department": row["department"],
                            "trace_id": ctx_pkg.get("trace_id"),
                        }
                    ),
                ),
            )
            audit(cur, ctx, "correction.submitted", "correction", cid)
        audit(cur, ctx, "handoff.replied", "handoff", handoff_id)
        result = _to_model(_load(cur, handoff_id))
    if body.as_correction:
        background.add_task(_dispatch_quietly, settings, ctx.tenant_id)
    return result


@router.post(
    "/handoffs/{handoff_id}/resolve", response_model=Handoff, summary="Mark a task done"
)
def resolve(
    handoff_id: UUID,
    ctx: Context = Depends(QUEUE_STAFF),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, ctx) as cur:
        row = _load(cur, handoff_id)
        if row["status"] == "resolved":
            return _to_model(row)
        cur.execute(
            "update app.handoffs set status = 'resolved', resolved_at = now(),"
            " assigned_to = coalesce(assigned_to, %s), updated_at = now() where id = %s",
            (ctx.profile_id, handoff_id),
        )
        _event(cur, ctx, handoff_id, "resolved")
        audit(cur, ctx, "handoff.resolved", "handoff", handoff_id)
        done = _load(cur, handoff_id)
        emit(cur, "handoff.resolved", handoff_data(done), lead_id=done.get("lead_id"))
        return _to_model(done)


def _dispatch_quietly(settings: Settings, tenant_id: UUID) -> None:
    """Best effort: if Module A is down the events stay in the outbox for the next try."""
    try:
        dispatch_events(settings, tenant_id)
    except Exception:  # noqa: BLE001 - never fail the user's request because delivery failed
        pass
