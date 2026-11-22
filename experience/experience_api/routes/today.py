"""What needs this member of staff today, most urgent first: the console's opening summary.

Only counts and links, so the page stays short. Row-Level Security limits every count to what
this person may see (faculty: their departments). Module A's approval count is asked for before
the transaction opens, and its absence is reported, never shown as zero.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..config import Settings
from ..db import Context, tx
from ..deps import ADMISSION_DESK, DEPARTMENT_ROLES, require_staff, settings_dep
from .approvals import _inbox

router = APIRouter(tags=["analytics"])

QUEUE_ROLES = ("super_admin", "college_admin", *ADMISSION_DESK, *DEPARTMENT_ROLES)
APPROVER_ROLES = ("college_admin", "admission_officer", *DEPARTMENT_ROLES)
LEAD_ROLES = ("college_admin", *ADMISSION_DESK, "marketing_manager")


@router.get(
    "/today",
    summary="What needs you today, most urgent first: overdue and waiting callbacks, "
    "approvals and hot leads not yet contacted (PRD 13, console home)",
)
def today(
    ctx: Context = Depends(require_staff()),
    settings: Settings = Depends(settings_dep),
):
    items: list[dict] = []
    approvals: int | None = None
    if ctx.role in APPROVER_ROLES:
        try:
            approvals = len(_inbox(settings, ctx)[0])
        except HTTPException as exc:  # _inbox turns Module A being down into a 502
            if exc.status_code != 502:
                raise
    with tx(settings.database_url, ctx) as cur:
        if ctx.role in QUEUE_ROLES:
            cur.execute(
                "select count(*) filter (where sla_due_at < now()) as overdue,"
                " count(*) filter (where status = 'open') as waiting,"
                " count(*) filter (where status = 'claimed' and assigned_to = %s) as mine,"
                " min(sla_due_at) filter (where status = 'open') as next_due"
                " from app.handoffs where status <> 'resolved'",
                (ctx.profile_id,),
            )
            h = cur.fetchone()
            items += [
                _item(
                    "overdue_callbacks",
                    h["overdue"],
                    "callback is past its promised time",
                    "callbacks are past their promised time",
                    "/console",
                    "urgent",
                ),
                _item(
                    "waiting_callbacks",
                    h["waiting"],
                    "request is waiting for someone to claim it",
                    "requests are waiting for someone to claim them",
                    "/console",
                    "high",
                    next_due=h["next_due"],
                ),
                _item(
                    "my_callbacks",
                    h["mine"],
                    "task you claimed is still open",
                    "tasks you claimed are still open",
                    "/console?mine=true",
                    "normal",
                ),
            ]
        if ctx.role == "college_admin" and not settings.smtp_host:
            # Seen 18 Nov 2026: with no SMTP server set, callback acknowledgements, staff alerts
            # and guardian codes waited in the outbox for ever and nobody was told.
            cur.execute(
                "select count(*) as n from app.message_outbox"
                " where status = 'pending' and channel = 'email'"
            )
            items.append(
                _item(
                    "email_not_set_up",
                    cur.fetchone()["n"],
                    "e-mail is waiting to be sent: e-mail is not set up (SMTP_HOST)",
                    "e-mails are waiting to be sent: e-mail is not set up (SMTP_HOST)",
                    "/console/admin",
                    "urgent",
                )
            )
        if ctx.role in LEAD_ROLES:
            cur.execute(
                "select count(*) as n from app.leads where band = 'hot' and stage = 'new'"
                " and anonymised_at is null",
            )
            items.append(
                _item(
                    "hot_leads",
                    cur.fetchone()["n"],
                    "hot lead has not been contacted yet",
                    "hot leads have not been contacted yet",
                    "/console/leads?band=hot&stage=new",
                    "high",
                )
            )
    if ctx.role in APPROVER_ROLES:
        items.append(
            _item(
                "approvals",
                approvals,
                "change to the assistant's answers is waiting for you",
                "changes to the assistant's answers are waiting for you",
                "/console/approvals",
                "normal",
            )
        )
    rank = {"urgent": 0, "high": 1, "normal": 2}
    items = [i for i in items if i["count"] != 0]
    items.sort(key=lambda i: (rank[i["priority"]], -(i["count"] or 0)))
    return {"role": ctx.role, "items": items}


def _item(kind, count, one, many, href, priority, **extra) -> dict:
    """`count` None means the source was unreachable: the page says so instead of hiding it."""
    if count is None:
        title = "Approvals could not be loaded right now"
    else:
        title = f"{count} {one if count == 1 else many}"
    return {
        "kind": kind,
        "count": count,
        "title": title,
        "href": href,
        "priority": priority,
        **extra,
    }
