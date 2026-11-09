"""Conversation events (PRD F8.3 implicit signals; counts behind the 3.5 metric definitions) and
break-glass access for the platform Super-Admin (PRD 5.4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..config import Settings
from ..db import Context, Jsonb, audit, tx
from ..deps import require_staff, settings_dep, visitor_or_service
from ..schemas import BreakGlassIn, ConversationEventIn

router = APIRouter()
SUPER_ADMIN = require_staff("super_admin")


@router.post(
    "/conversation-events",
    status_code=204,
    tags=["feedback"],
    summary="Log a conversation event: user message, answer, rephrase, talk to human…",
)
def conversation_event(
    body: ConversationEventIn,
    ctx: Context = Depends(visitor_or_service),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "insert into app.conversation_events (tenant_id, session_id, conversation_id, kind,"
            " message_id, channel) values (%s, %s, %s, %s, %s, %s)",
            (
                ctx.tenant_id,
                ctx.session_id,
                body.conversation_id,
                body.kind,
                body.message_id,
                body.channel,
            ),
        )
        if body.kind in LEARNING_SIGNALS:
            # PRD F8.3: Module A's learning loop reads these as implicit feedback. Ids and the
            # channel only; A looks the question up in Module B (and masks it) if it needs it.
            cur.execute(
                "insert into app.outbox_events (tenant_id, type, payload) values (%s, 'signal.logged', %s)",
                (
                    ctx.tenant_id,
                    Jsonb(
                        {
                            "kind": body.kind,
                            "conversation_id": body.conversation_id,
                            "message_id": body.message_id,
                            "channel": body.channel,
                        }
                    ),
                ),
            )


# Implicit signals Module A learns from (A's learning.IMPLICIT; "abandoned" is computed by A).
LEARNING_SIGNALS = ("rephrase", "talk_to_human", "voice_hangup", "barge_in")


def _active(cur, ctx: Context) -> dict | None:
    cur.execute(
        "select id, reason, started_at, expires_at from app.break_glass"
        " where profile_id = %s and ended_at is null and expires_at > now()"
        " order by started_at desc limit 1",
        (ctx.profile_id,),
    )
    return cur.fetchone()


@router.get(
    "/break-glass", tags=["admin"], summary="Is break-glass access active for me?"
)
def break_glass_status(
    ctx: Context = Depends(SUPER_ADMIN), settings: Settings = Depends(settings_dep)
):
    with tx(settings.database_url, ctx) as cur:
        return {"active": _active(cur, ctx)}


@router.post(
    "/break-glass",
    status_code=201,
    tags=["admin"],
    summary="Start 1 hour of access to this college's personal data, with a reason",
)
def start_break_glass(
    body: BreakGlassIn,
    ctx: Context = Depends(SUPER_ADMIN),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, ctx) as cur:
        if _active(cur, ctx):
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Break-glass access is already active"
            )
        cur.execute(
            "insert into app.break_glass (tenant_id, profile_id, reason) values (%s, %s, %s)"
            " returning id, reason, started_at, expires_at",
            (ctx.tenant_id, ctx.profile_id, body.reason),
        )
        row = cur.fetchone()
        cur.execute(
            "insert into app.notifications (tenant_id, audience, kind, payload)"
            " values (%s, 'college_admin', 'break_glass.started', %s)",
            (
                ctx.tenant_id,
                Jsonb({"reason": body.reason, "until": row["expires_at"].isoformat()}),
            ),
        )
        audit(
            cur,
            ctx,
            "break_glass.started",
            "break_glass",
            row["id"],
            after={"reason": body.reason},
        )
        return {"active": row}


@router.post(
    "/break-glass/end", status_code=204, tags=["admin"], summary="End access early"
)
def end_break_glass(
    ctx: Context = Depends(SUPER_ADMIN), settings: Settings = Depends(settings_dep)
):
    with tx(settings.database_url, ctx) as cur:
        row = _active(cur, ctx)
        if row:
            cur.execute(
                "update app.break_glass set ended_at = now() where id = %s",
                (row["id"],),
            )
            audit(cur, ctx, "break_glass.ended", "break_glass", row["id"])
