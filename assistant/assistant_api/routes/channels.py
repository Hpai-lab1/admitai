"""WhatsApp webhook (PRD F1.11, 12.1: POST /channels/whatsapp/webhook, signature-verified)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from .. import store, whatsapp
from ..auth import Caller, service_only
from ..engine import Deps
from .chat import deps_dep

router = APIRouter(tags=["channels"])


@router.get("/channels/whatsapp/webhook", summary="Meta's subscription check")
def verify(
    request: Request,
    mode: str = Query(alias="hub.mode"),
    token: str = Query(alias="hub.verify_token"),
    challenge: str = Query(alias="hub.challenge"),
):
    s = request.app.state.settings
    if (
        mode != "subscribe"
        or not s.whatsapp_verify_token
        or token != s.whatsapp_verify_token
    ):
        raise HTTPException(403, "Verification failed")
    return PlainTextResponse(challenge)


def sender_for(request: Request, phone_number_id: str | None) -> whatsapp.Sender:
    s = request.app.state.settings
    if s.whatsapp_token and phone_number_id:
        return whatsapp.CloudApiSender(
            s.whatsapp_graph_url, phone_number_id, s.whatsapp_token
        )
    return request.app.state.wa_sender


@router.post(
    "/channels/whatsapp/webhook",
    summary="Inbound WhatsApp messages (answered in the background)",
)
async def inbound(
    request: Request, background: BackgroundTasks, deps: Deps = Depends(deps_dep)
):
    s = request.app.state.settings
    body = await request.body()
    if not whatsapp.verify_signature(
        s.whatsapp_app_secret, body, request.headers.get("X-Hub-Signature-256")
    ):
        raise HTTPException(401, "Bad signature")
    accepted = 0
    for item in whatsapp.parse(json.loads(body or b"{}")):
        tenant = await asyncio.to_thread(
            whatsapp.tenant_for, s.database_url, item["phone_number_id"] or ""
        )
        if tenant is None or not item.get("from") or not item.get("id"):
            continue
        background.add_task(
            whatsapp.handle,
            deps,
            request.app.state.experience,
            sender_for(request, item["phone_number_id"]),
            tenant,
            item,
            s.contact_salt,
        )
        accepted += 1
    # Meta retries anything that is not a quick 200; work happens after the response.
    return {"accepted": accepted}


# ------------------------------------------------------------------ calls from Module C


class GuardianCodeIn(BaseModel):
    destination: str = Field(
        min_length=8, max_length=20, description="Guardian's WhatsApp number"
    )
    code: str = Field(pattern=r"^\d{4,8}$")
    expires_at: datetime
    language: str = Field(default="en", max_length=5)


@router.post(
    "/channels/whatsapp/guardian-code",
    summary="Hold a guardian's code until the guardian writes in on WhatsApp (PRD F5.3; Module C only)",
)
async def guardian_code(
    body: GuardianCodeIn, request: Request, who: Caller = Depends(service_only)
):
    s = request.app.state.settings
    _whatsapp_ready(s)
    if not 10 <= len(whatsapp.digits(body.destination)) <= 15:
        raise HTTPException(
            422, "Give the guardian's WhatsApp number with its country code"
        )
    if body.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(422, "The code has already expired")
    channel = await asyncio.to_thread(_channel_of, s.database_url, who.tenant_id)
    if channel is None:
        raise HTTPException(409, "This college has no WhatsApp number in Module B")
    await asyncio.to_thread(
        whatsapp.queue_guardian_code,
        s.database_url,
        who.tenant_id,
        body.destination,
        body.code,
        body.expires_at,
        body.language,
        s.contact_salt,
    )
    cfg = await asyncio.to_thread(store.tenant_config, s.database_url, who.tenant_id)
    return {
        "queued": True,
        "display_number": (cfg.get("whatsapp") or {}).get("display_number"),
        "expires_at": body.expires_at.isoformat(),
    }


class NotifyIn(BaseModel):
    to: str = Field(
        min_length=8, max_length=20, description="The person's WhatsApp number"
    )
    kind: str = Field(pattern=r"^(callback_ack|callback_reminder|staff_reply)$")
    text: str = Field(min_length=1, max_length=4000)


@router.post(
    "/channels/whatsapp/notify",
    summary="Send a staff reply or callback message on WhatsApp: text in the 24-hour window, else "
    "the college's approved template (PRD F1.11, F3.4, F3.6; Module C only)",
)
async def notify(body: NotifyIn, request: Request, who: Caller = Depends(service_only)):
    s = request.app.state.settings
    _whatsapp_ready(s)
    phone_number_id = await asyncio.to_thread(
        _channel_of, s.database_url, who.tenant_id
    )
    if phone_number_id is None:
        raise HTTPException(409, "This college has no WhatsApp number in Module B")
    try:
        return await asyncio.to_thread(
            whatsapp.notify,
            request.app.state.deps,
            sender_for(request, phone_number_id),
            who.tenant_id,
            body.to,
            body.kind,
            body.text,
            s.contact_salt,
        )
    except whatsapp.NotifyRefused as exc:
        raise HTTPException(409, str(exc)) from None


def _whatsapp_ready(s) -> None:
    if not s.contact_salt:
        raise HTTPException(503, "WhatsApp is not set up here (CONTACT_SALT)")
    if s.app_env != "development" and not s.whatsapp_token:
        # Without the Cloud API token messages would only reach the local outbox.
        raise HTTPException(503, "WhatsApp is not set up here (WHATSAPP_TOKEN)")


def _channel_of(database_url: str, tenant: str) -> str | None:
    from ..db import tx

    with tx(database_url, tenant) as cur:
        cur.execute(
            "select external_id from chat.channels where channel = 'whatsapp' limit 1"
        )
        row = cur.fetchone()
    return row["external_id"] if row else None
