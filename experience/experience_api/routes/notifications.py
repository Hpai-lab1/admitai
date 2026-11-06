"""In-console notifications for new handoffs and callbacks (PRD F3.5). Who sees which is decided
by the RLS policy on app.notifications (admission queue, faculty departments, admins)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..config import Settings
from ..db import Context, tx
from ..deps import require_staff, settings_dep

router = APIRouter(tags=["notifications"])


@router.get("/notifications", summary="Your notifications, unread first")
def list_notifications(
    unread_only: bool = False,
    limit: int = Query(50, ge=1, le=200),
    ctx: Context = Depends(require_staff()),
    settings: Settings = Depends(settings_dep),
):
    cond = "not (%s = any(read_by))" if unread_only else "true"
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            f"select id, kind, audience, payload, created_at, %s = any(read_by) as read"
            f" from app.notifications where {cond} order by read, created_at desc limit %s",
            (ctx.profile_id, *((ctx.profile_id,) if unread_only else ()), limit),
        )
        items = cur.fetchall()
        cur.execute(
            "select count(*) as n from app.notifications where not (%s = any(read_by))",
            (ctx.profile_id,),
        )
        return {"unread": cur.fetchone()["n"], "items": items}


@router.post(
    "/notifications/{notification_id}/read", status_code=204, summary="Mark one as read"
)
def mark_read(
    notification_id: int,
    ctx: Context = Depends(require_staff()),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "update app.notifications set read_by = array_append(read_by, %s)"
            " where id = %s and not (%s = any(read_by))",
            (ctx.profile_id, notification_id, ctx.profile_id),
        )
        if cur.rowcount == 0:
            cur.execute(
                "select 1 from app.notifications where id = %s", (notification_id,)
            )
            if not cur.fetchone():
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
