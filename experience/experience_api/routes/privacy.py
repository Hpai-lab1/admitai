"""Erasure on request (PRD 11.4, NFR Privacy). A visitor erases their own session from the widget;
Module B asks on behalf of a WhatsApp contact; a College Admin acts on a request received by phone
or e-mail, starting from the lead."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..clients import dispatch_events
from ..config import Settings
from ..db import Context, tx
from ..deps import require_staff, settings_dep, visitor_or_service
from ..privacy import erase_session

router = APIRouter(tags=["privacy"])


def _run(settings: Settings, tenant_id: UUID, session_id: UUID, by: str) -> dict:
    with tx(settings.database_url, Context(tenant_id, "system")) as cur:
        cur.execute("select 1 from app.visitor_sessions where id = %s", (session_id,))
        if cur.fetchone() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown visitor session")
        result = erase_session(cur, Context(tenant_id, "system"), session_id, by)
    try:  # best effort now; the nightly run retries whatever is still in the outbox
        dispatch_events(settings, tenant_id)
    except Exception:  # noqa: BLE001 - the request is recorded either way
        pass
    return result


@router.post(
    "/privacy/erase",
    summary="Erase this visitor's data: consents withdrawn, leads anonymised, transcripts blanked",
)
def erase_mine(
    ctx: Context = Depends(visitor_or_service),
    settings: Settings = Depends(settings_dep),
):
    if not ctx.session_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "X-Session-Id is required for this call"
        )
    return _run(settings, ctx.tenant_id, ctx.session_id, ctx.actor_kind)


@router.post(
    "/leads/{lead_id}/erase",
    summary="Erase a lead's visitor session on a request received outside the widget",
)
def erase_lead(
    lead_id: UUID,
    ctx: Context = Depends(require_staff("college_admin")),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, ctx) as cur:
        cur.execute("select session_id from app.leads where id = %s", (lead_id,))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")
    return _run(settings, ctx.tenant_id, row["session_id"], f"staff:{ctx.profile_id}")
