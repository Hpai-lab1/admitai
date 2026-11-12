"""Learning-loop input (PRD 21.4): feedback.created and correction.submitted events from Module C.
Delivery is at-least-once, so events are de-duplicated by (tenant, event_id)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..config import Settings
from ..db import Jsonb, tx
from ..deps import settings_dep, tenant_dep
from ..learning import erase_conversations

router = APIRouter(tags=["learning"])

EVENT_TYPES = {
    "feedback.created",
    "correction.submitted",
    "erasure.requested",
    "signal.logged",  # F8.3 implicit signals (rephrase, talk to a human, abandonment, voice)
}


class EventIn(BaseModel):
    event_id: str | int
    type: str
    tenant_id: UUID
    occurred_at: str | None = None
    data: dict


@router.post("/events", summary="Receive an event from Module C (idempotent)")
def receive(body: EventIn, settings: Settings = Depends(settings_dep)):
    if body.type not in EVENT_TYPES:
        raise HTTPException(422, f"Unknown event type {body.type}")
    with tx(settings.database_url, body.tenant_id) as cur:
        cur.execute(
            "insert into learn.events (tenant_id, event_id, type, payload) values (%s,%s,%s,%s)"
            " on conflict (tenant_id, event_id) do nothing",
            (body.tenant_id, str(body.event_id), body.type, Jsonb(body.data)),
        )
        duplicate = cur.rowcount == 0
        if body.type == "erasure.requested":
            # Carried out at once, not at the next learning run; repeating it is harmless.
            erased = erase_conversations(
                cur, list(body.data.get("conversation_ids") or [])
            )
            cur.execute(
                "update learn.events set processed_at = now() where event_id = %s",
                (str(body.event_id),),
            )
            return {"ok": True, "duplicate": duplicate, "erased": erased}
    return {"ok": True, "duplicate": duplicate}


@router.get("/events", summary="Events received for this tenant (newest first)")
def list_events(
    limit: int = 100,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select event_id, type, payload, received_at, processed_at from learn.events order by id desc limit %s",
            (min(limit, 500),),
        )
        return {"items": cur.fetchall()}
