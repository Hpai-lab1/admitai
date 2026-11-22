"""Answer feedback and staff corrections (PRD F4.1, F3.6). Both are written to the outbox and
delivered to Module A as feedback.created / correction.submitted events."""

from __future__ import annotations

import hashlib
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from ..clients import UpstreamError, assistant_post, knowledge_get
from ..config import Settings
from ..db import Context, Jsonb, audit, tx
from ..deps import (
    ADMISSION_DESK,
    DEPARTMENT_ROLES,
    any_caller,
    policy_role,
    require_staff,
    settings_dep,
)
from ..schemas import CorrectionIn, Created, FeedbackIn
from .handoffs import _dispatch_quietly

router = APIRouter(tags=["feedback"])
# Who may correct an answer (a correction is a proposal; approving it is separate).
CORRECTORS = require_staff("college_admin", *ADMISSION_DESK, *DEPARTMENT_ROLES)


def session_fp(ctx: Context) -> str | None:
    """A one-way hash of the visitor's session, so Module A can tell feedback from different
    visitors apart (its self-learning layer needs several distinct sessions to agree) without
    ever receiving the session id. Module A salts it again before storing it."""
    if ctx.session_id is None:
        return None
    return hashlib.sha256(
        f"feedback|{ctx.tenant_id}|{ctx.session_id}".encode()
    ).hexdigest()


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
        payload = {"feedback_id": str(fid), **body.model_dump(), "actor": actor}
        fp = session_fp(ctx)
        if fp:
            payload["session_fp"] = fp
        cur.execute(
            "insert into app.outbox_events (tenant_id, type, payload) values (%s, 'feedback.created', %s)",
            (ctx.tenant_id, Jsonb(payload)),
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
            # a 3-star chat rating counts when the visitor picked a complaint from the dropdown
            "((f.kind = 'thumb' and f.value = -1) or (f.kind = 'rating' and (f.value <= 2"
            " or (f.value = 3 and f.reason is not null))))"
        )
    if policy_role(ctx.role) == "faculty":
        where.append("f.department = any(%s)")
        params.append(list(ctx.departments))
    elif policy_role(ctx.role) == "admission_officer":
        where.append("f.department is null")
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "select f.id, f.conversation_id, f.message_id, f.kind, f.value, f.reason, f.comment,"
            " f.actor, f.channel, f.trace_id, f.department, f.created_at,"
            " exists (select 1 from app.corrections c where c.message_id = f.message_id"
            "   and c.conversation_id = f.conversation_id) as corrected,"
            " (select c.id::text from app.corrections c where c.message_id = f.message_id"
            "   and c.conversation_id = f.conversation_id order by c.created_at desc limit 1)"
            "   as correction_id"
            f" from app.feedback f where {' and '.join(where)}"
            " order by f.created_at desc limit %s",
            (*params, limit),
        )
        rows = cur.fetchall()
        if ctx.role == "super_admin":
            # PRD 5.3: platform staff see feedback aggregated, without visitors' words, unless
            # break-glass is active (seen 18 Nov 2026: the comments were readable). A read
            # during break-glass is audited.
            from .signals import _active

            if not _active(cur, ctx):
                return [
                    {
                        **r,
                        "comment": None,
                        "conversation_id": None,
                        "message_id": None,
                        "trace_id": None,
                        "redacted": True,
                    }
                    for r in rows
                ]
            audit(cur, ctx, "feedback.read_break_glass", "tenant", ctx.tenant_id)
    return _with_stages(settings, ctx, _with_texts(settings, ctx, rows))


def _with_stages(settings: Settings, ctx: Context, rows: list[dict]) -> list[dict]:
    """Where each correction is on its way to visitors, from Module A: received, waiting for
    approval, being tested, live, failed the tests, rejected, rolled back, or not used. None when
    Module A cannot say; the page then shows "correction sent", as before."""
    ids = sorted({r["correction_id"] for r in rows if r.get("correction_id")})
    stages: dict = {}
    if ids:
        try:
            stages = knowledge_get(
                settings,
                "/improvements/corrections",
                ctx.tenant_id,
                {"ids": ",".join(ids)},
            )["items"]
        except (UpstreamError, KeyError, TypeError):
            stages = {}
    for r in rows:
        s = stages.get(r.get("correction_id") or "")
        r["correction_stage"] = (
            s["stage"] if s else ("sent" if r.get("correction_id") else None)
        )
    return rows


def _with_texts(settings: Settings, ctx: Context, rows: list[dict]) -> list[dict]:
    """The question and the answer each rating is about, from Module B (masked there), so staff
    can see what to correct. `text`: "found"; "missing" when B has no such answer (erased on
    request, or a demo record); "unavailable" when B cannot be reached right now."""
    ids = sorted({r["message_id"] for r in rows if r["message_id"] != "conversation"})
    try:
        found = (
            assistant_post(settings, "/messages/lookup", ctx.tenant_id, {"ids": ids})[
                "items"
            ]
            if ids
            else {}
        )
        state = None
    except (UpstreamError, KeyError, TypeError):
        found, state = {}, "unavailable"
    for r in rows:
        m = found.get(r["message_id"])
        r["question"] = m["question"] if m else None
        r["answer"] = m["answer"] if m else None
        r["text"] = (
            "conversation"
            if r["message_id"] == "conversation"
            else state or ("found" if m else "missing")
        )
    return rows


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
