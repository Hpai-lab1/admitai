"""Webhook endpoints for a college's own systems (PRD 12.2), managed by its College Admin, and the
relay for Module A's knowledge events."""

from __future__ import annotations

import hmac
import uuid
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from ..config import Settings
from ..db import Context, Jsonb, audit, tx
from ..deps import require_staff, settings_dep
from ..webhooks import EVENTS, FROM_KNOWLEDGE, UnsafeUrl, check_url, emit, new_secret

router = APIRouter(tags=["webhooks"])
ADMIN = require_staff("college_admin")
EventType = Literal[
    "lead.created",
    "lead.updated",
    "handoff.created",
    "handoff.resolved",
    "improvement.deployed",
    "kb.sync.completed",
]


class EndpointIn(BaseModel):
    url: str = Field(max_length=500)
    events: list[EventType] = Field(min_length=1)


def _public(row: dict) -> dict:
    return {k: row[k] for k in ("id", "url", "events", "active", "created_at")}


def _safe(url: str, settings: Settings) -> None:
    try:
        check_url(url, settings)
    except UnsafeUrl as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None


@router.get(
    "/webhooks", summary="This college's webhook endpoints and recent delivery counts"
)
def list_endpoints(
    ctx: Context = Depends(ADMIN), settings: Settings = Depends(settings_dep)
):
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "select e.*, (select jsonb_object_agg(status, n) from (select status, count(*) as n"
            "   from app.webhook_deliveries d where d.endpoint_id = e.id"
            "   and d.created_at > now() - interval '7 days' group by status) s) as last_7_days"
            " from app.webhook_endpoints e order by e.created_at"
        )
        return {
            "events": list(EVENTS),
            "items": [
                {**_public(r), "last_7_days": r["last_7_days"] or {}}
                for r in cur.fetchall()
            ],
        }


@router.post(
    "/webhooks",
    status_code=201,
    summary="Add an endpoint; the signing secret is shown only in this response",
)
def add_endpoint(
    body: EndpointIn,
    ctx: Context = Depends(ADMIN),
    settings: Settings = Depends(settings_dep),
):
    _safe(body.url, settings)
    secret = new_secret()
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "insert into app.webhook_endpoints (tenant_id, url, secret, events, created_by)"
            " values (%s, %s, %s, %s, %s) returning *",
            (ctx.tenant_id, body.url, secret, sorted(set(body.events)), ctx.profile_id),
        )
        row = cur.fetchone()
        audit(
            cur,
            ctx,
            "webhook.created",
            "webhook",
            row["id"],
            after={"url": row["url"], "events": row["events"]},
        )
    return {**_public(row), "secret": secret}


@router.post("/webhooks/{endpoint_id}/rotate", summary="Replace the signing secret")
def rotate(
    endpoint_id: UUID,
    ctx: Context = Depends(ADMIN),
    settings: Settings = Depends(settings_dep),
):
    secret = new_secret()
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "update app.webhook_endpoints set secret = %s where id = %s returning *",
            (secret, endpoint_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook not found")
        audit(cur, ctx, "webhook.secret_rotated", "webhook", endpoint_id)
    return {**_public(row), "secret": secret}


@router.delete(
    "/webhooks/{endpoint_id}", status_code=204, summary="Stop sending to an endpoint"
)
def deactivate(
    endpoint_id: UUID,
    ctx: Context = Depends(ADMIN),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "update app.webhook_endpoints set active = false where id = %s",
            (endpoint_id,),
        )
        if not cur.rowcount:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook not found")
        audit(cur, ctx, "webhook.deactivated", "webhook", endpoint_id)


@router.get(
    "/webhooks/{endpoint_id}/deliveries", summary="Recent deliveries (no payloads)"
)
def deliveries(
    endpoint_id: UUID,
    ctx: Context = Depends(ADMIN),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "select id, event_id, type, status, attempts, last_status, last_error, created_at,"
            " delivered_at, next_attempt_at from app.webhook_deliveries where endpoint_id = %s"
            " order by id desc limit 100",
            (endpoint_id,),
        )
        return {"items": cur.fetchall()}


@router.post(
    "/webhooks/{endpoint_id}/test", status_code=202, summary="Queue a signed ping"
)
def ping(
    endpoint_id: UUID,
    ctx: Context = Depends(ADMIN),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "select active from app.webhook_endpoints where id = %s", (endpoint_id,)
        )
        row = cur.fetchone()
    if not row or not row["active"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook not found")
    eid = uuid.uuid4()
    with tx(settings.database_url, Context(ctx.tenant_id, "system")) as cur:
        cur.execute(
            "insert into app.webhook_deliveries (tenant_id, endpoint_id, event_id, type, payload)"
            " values (%s, %s, %s, 'ping', %s)",
            (
                ctx.tenant_id,
                endpoint_id,
                eid,
                Jsonb({"id": str(eid), "type": "ping", "data": {}}),
            ),
        )
    return {"event_id": str(eid)}


class KnowledgeEvent(BaseModel):
    event_id: UUID
    type: Literal["improvement.deployed", "kb.sync.completed"]
    data: dict


@router.post(
    "/webhooks/events",
    status_code=202,
    summary="Module A's knowledge events, sent on to the college's endpoints",
)
def from_knowledge(
    body: KnowledgeEvent,
    x_service_key: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    settings: Settings = Depends(settings_dep),
):
    keys = (settings.key_from_knowledge, settings.ops_key)
    if not x_service_key or not any(
        k and hmac.compare_digest(x_service_key, k) for k in keys
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown service")
    try:
        tenant = UUID(x_tenant_id or "")
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "X-Tenant-Id must be a uuid"
        ) from None
    assert body.type in FROM_KNOWLEDGE
    with tx(settings.database_url, Context(tenant, "system")) as cur:
        queued = emit(cur, body.type, body.data, event_id=body.event_id)
    return {"queued": queued}
