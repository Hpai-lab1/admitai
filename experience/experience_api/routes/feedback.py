"""Answer feedback and staff corrections (PRD F4.1, F3.6). Both are written to the outbox and
delivered to Module A as feedback.created / correction.submitted events."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from ..config import Settings
from ..db import Context, Jsonb, audit, tx
from ..deps import any_caller, require_staff, settings_dep
from ..schemas import CorrectionIn, Created, FeedbackIn
from .handoffs import _dispatch_quietly

router = APIRouter(tags=["feedback"])
CORRECTORS = require_staff("college_admin", "admission_officer", "faculty")


@router.post(
    "/feedback",
    response_model=Created,
    status_code=201,
    summary="Record a rating or reason for an answer (emits feedback.created to Module A)",
)
def create_feedback(
    body: FeedbackIn,
    background: BackgroundTasks,
    ctx: Context = Depends(any_caller),
    settings: Settings = Depends(settings_dep),
):
    actor = "staff" if ctx.actor_kind == "staff" else "visitor"
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "insert into app.feedback (tenant_id, conversation_id, message_id, kind, value, reason, comment, actor,"
            " channel, trace_id, department) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
            (
                ctx.tenant_id,
                body.conversation_id,
                body.message_id,
                body.kind,
                body.value,
                body.reason,
                body.comment,
                actor,
                body.channel,
                body.trace_id,
                body.department,
            ),
        )
        fid = cur.fetchone()["id"]
        cur.execute(
            "insert into app.outbox_events (tenant_id, type, payload) values (%s, 'feedback.created', %s)",
            (
                ctx.tenant_id,
                Jsonb({"feedback_id": str(fid), **body.model_dump(), "actor": actor}),
            ),
        )
    background.add_task(_dispatch_quietly, settings, ctx.tenant_id)
    return Created(id=fid)


@router.get(
    "/feedback",
    summary="Review queue of low-rated answers for your role and departments (F7.4)",
)
def list_feedback(
    only: Literal["all", "negative"] = "negative",
    limit: int = Query(100, ge=1, le=500),
    ctx: Context = Depends(require_staff()),
    settings: Settings = Depends(settings_dep),
):
    where = ["true"]
    params: list = []
    if only == "negative":
        where.append(
            "((f.kind = 'thumb' and f.value = -1) or (f.kind = 'rating' and f.value <= 2))"
        )
    if ctx.role == "faculty":
        where.append("f.department = any(%s)")
        params.append(list(ctx.departments))
    elif ctx.role == "admission_officer":
        where.append("f.department is null")
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "select f.id, f.conversation_id, f.message_id, f.kind, f.value, f.reason, f.comment,"
            " f.actor, f.channel, f.trace_id, f.department, f.created_at,"
            " exists (select 1 from app.corrections c where c.message_id = f.message_id"
            "   and c.conversation_id = f.conversation_id) as corrected"
            f" from app.feedback f where {' and '.join(where)}"
            " order by f.created_at desc limit %s",
            (*params, limit),
        )
        return cur.fetchall()


@router.post(
    "/corrections",
    response_model=Created,
    status_code=201,
    summary="Staff correction of an answer (emits correction.submitted to Module A)",
)
def create_correction(
    body: CorrectionIn,
    background: BackgroundTasks,
    ctx: Context = Depends(CORRECTORS),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "insert into app.corrections (tenant_id, conversation_id, message_id, original_answer, corrected_answer,"
            " reason, scope, author_id, trace_id) values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
            (
                ctx.tenant_id,
                body.conversation_id,
                body.message_id,
                body.original_answer,
                body.corrected_answer,
                body.reason,
                body.scope,
                ctx.profile_id,
                body.trace_id,
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
                        **body.model_dump(),
                        "author_role": ctx.role,
                        "departments": list(ctx.departments),
                    }
                ),
            ),
        )
        audit(
            cur,
            ctx,
            "correction.submitted",
            "correction",
            cid,
            after={"scope": body.scope},
        )
    background.add_task(_dispatch_quietly, settings, ctx.tenant_id)
    return Created(id=cid)


@router.get(
    "/corrections", summary="Corrections and whether Module A has accepted them"
)
def list_corrections(
    limit: int = Query(100, ge=1, le=500),
    ctx: Context = Depends(CORRECTORS),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "select c.*, n.full_name as author_name from app.corrections c"
            " left join app.staff_names() n on n.id = c.author_id order by c.created_at desc limit %s",
            (limit,),
        )
        return cur.fetchall()
