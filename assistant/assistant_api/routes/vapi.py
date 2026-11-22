"""Vapi phone calls (vapi.py): the answer endpoint Vapi calls for every turn, the webhook it
sends call events to, and placing an outbound call. English only on this channel.

A turn: Vapi heard the caller and POSTs the conversation (OpenAI chat format) to
`/voice/vapi/chat/completions`; the engine answers the caller's last words in voice style, and each
checked sentence goes back at once as an OpenAI streaming chunk (citation markers removed, since
they would be read out). A slow answer starts with a short "One moment" so the line is never
silent. Only calls this service created or was asked about (`assistant-request`), or requests
with VAPI_SERVER_SECRET, are answered.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import re
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .. import policy, retell, store, vapi, voice
from ..auth import Caller, service_only
from ..engine import Deps, Forbidden, handle_turn
from ..prompts import message
from .chat import deps_dep

log = logging.getLogger(__name__)
router = APIRouter(tags=["voice"])

_YEARS = re.compile(r"\b((?:19|20)\d\d)\s*[-–]\s*(\d\d)\b")


def for_phone(text: str) -> str:
    """What Vapi's English voice should say: no citation markers, amounts in Indian words and
    academic years spelt out. Live on 17 Nov 2026 Vapi's voice read "₹6,00,000" as "$6,000" and
    "2026-27" as "20267"; now it gets "six lakh rupees" and "2026 to 27"."""
    spoken = voice.for_speech(_YEARS.sub(r"\1 to \2", retell.speakable(text)))
    return re.sub(
        r"\s*,\s*([.!?])", r"\1", spoken
    )  # "category, [1]." left "category,."


# Vapi's voice speaks text in chunks of at least about 30 characters ending at a punctuation mark
# (its chunkPlan): live on 17 Nov 2026 the 30-character "One moment, let me check that." reached
# Vapi after 1.5 s and was never spoken while a slow answer (15 s) was still coming, so the caller
# heard silence and hung up. Both lines are well over that, and the second keeps a slow line alive.
HOLD_ON = "One moment please, let me check that for you."
STILL_CHECKING = "Thanks for waiting, I'm still checking that for you."
HOLD_AFTER_S = (
    1.2  # as on Retell calls: "one moment" only if the answer is not ready by then
)
STILL_AFTER_S = 6.0  # after "one moment", still nothing: say so once
ONLY_LANGUAGE = "en"


def _secret_ok(request: Request, secret: str) -> bool:
    """Vapi sends the secret back as X-Vapi-Secret, or as a Bearer token (custom-LLM credential)."""
    if not secret:
        return False
    got = request.headers.get("x-vapi-secret") or ""
    auth = request.headers.get("authorization") or ""
    if not got and auth.lower().startswith("bearer "):
        got = auth[7:].strip()
    return bool(got) and hmac.compare_digest(got.encode(), secret.encode())


def _call_tenant(deps: Deps, call: dict, metadata: dict) -> str | None:
    """The college of a call this service has not met yet: the overrides' metadata, the Vapi
    number it came in on, else the one-college default (VAPI_TENANT_ID)."""
    s = deps.settings
    tenant = (
        metadata.get("tid")
        or vapi.tenant_for_number(s.database_url, call.get("phoneNumberId"))
        or s.vapi_default_tenant
        or None
    )
    return tenant if tenant and _vapi_allowed(deps, tenant) else None


def _vapi_allowed(deps: Deps, tenant: str) -> bool:
    """Telephone (Vapi) only for a college that opted in (model_policy.voice_external)."""
    cfg = store.tenant_config(deps.settings.database_url, tenant)
    return "vapi" in policy.voice_external(cfg.get("model_policy"))


# ------------------------------------------------------------------ every turn of a call


@router.post(
    "/voice/vapi/chat/completions",
    summary="Vapi custom LLM: answer one turn of a phone call",
)
@router.post(
    "/voice/vapi", include_in_schema=False
)  # if Vapi posts to the base URL itself
async def answer_turn(request: Request, deps: Deps = Depends(deps_dep)):
    body = await request.json()
    call = body.get("call") or {}
    metadata = {
        **((call.get("assistantOverrides") or {}).get("metadata") or {}),
        **(body.get("metadata") or {}),
    }
    known = await asyncio.to_thread(
        vapi.lookup, call.get("id"), deps.settings.database_url
    )
    authed = _secret_ok(request, deps.settings.vapi_server_secret)
    # The request's shape only (no words, no numbers): what Vapi sends is checked on real calls.
    log.info(
        "vapi turn: call %s known=%s secret=%s keys=%s stream=%s",
        call.get("id"),
        known is not None,
        authed,
        sorted(body)[:12],
        body.get("stream"),
    )
    if known is None and not authed:
        raise HTTPException(401, "Not a call this service knows")
    if known is None:
        tenant = await asyncio.to_thread(_call_tenant, deps, call, metadata)
        if not tenant:
            raise HTTPException(404, "No college is set up for this Vapi number")
        conv = metadata.get("cid") or "vapi-" + (call.get("id") or uuid.uuid4().hex)
        known = vapi.VapiCall(
            tenant, conv[:100], time.time(), call.get("type") or "inbound"
        )
        if call.get("id"):
            vapi.remember(call["id"], known, deps.settings.database_url)
    text = vapi.last_user_text(body.get("messages"))
    completion = vapi.Completion()
    sentences = _answer(deps, known, text)
    if body.get("stream") is False:
        said = [s async for s in sentences if s is not _HOLD and s is not _STILL]
        return JSONResponse(completion.whole(" ".join(said).strip()))

    async def stream():
        yield completion.chunk(role=True)
        async for sentence in sentences:
            spoken = {id(_HOLD): HOLD_ON, id(_STILL): STILL_CHECKING}.get(
                id(sentence), sentence
            )
            yield completion.chunk(spoken + " ")
        yield completion.chunk(finish="stop")
        yield vapi.DONE

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_HOLD = object()  # "one moment" marker
_STILL = object()  # "still checking" marker
_END = object()


async def _answer(deps: Deps, call: vapi.VapiCall, text: str):
    """The checked sentences to say, as the engine releases them; `_HOLD` first when the answer
    takes longer than HOLD_AFTER_S. The caller is a service-side visitor (no widget session)."""
    if not text:
        yield voice.msg("not_heard", ONLY_LANGUAGE)
        return
    queue: asyncio.Queue = asyncio.Queue()

    async def produce() -> None:
        try:
            async for ev in handle_turn(
                deps,
                Caller(call.tenant, None, "service"),
                call.conversation_id,
                text,
                ONLY_LANGUAGE,
                "phone",
                only_language=ONLY_LANGUAGE,
            ):
                if ev["event"] == "token":
                    spoken = for_phone(ev["data"]["text"])
                    if spoken.strip():
                        await queue.put(spoken.strip())
        except Forbidden:
            await queue.put(message("unavailable", ONLY_LANGUAGE))
        except Exception:  # noqa: BLE001 - one bad turn must not leave the caller in silence
            log.exception("vapi: turn failed (conversation %s)", call.conversation_id)
            await queue.put(message("unavailable", ONLY_LANGUAGE))
        finally:
            await queue.put(_END)

    task = asyncio.create_task(produce())
    # Fillers while nothing has been said yet: "one moment" after HOLD_AFTER_S, then "still
    # checking" STILL_AFTER_S later; after the first real sentence, none.
    fillers = [(HOLD_AFTER_S, _HOLD), (STILL_AFTER_S, _STILL)]
    try:
        while True:
            try:
                item = await (
                    asyncio.wait_for(queue.get(), fillers[0][0])
                    if fillers
                    else queue.get()
                )
            except asyncio.TimeoutError:
                yield fillers.pop(0)[1]
                continue
            if item is _END:
                return
            fillers = []
            yield item
    finally:
        if not task.done():  # Vapi hung up or the caller spoke over the answer
            task.cancel()


# ------------------------------------------------------------------ call events


@router.post(
    "/voice/vapi/webhook", summary="Vapi server messages: inbound calls and call status"
)
async def webhook(request: Request, deps: Deps = Depends(deps_dep)):
    s = deps.settings
    if not _secret_ok(request, s.vapi_server_secret):
        raise HTTPException(401, "Bad or missing Vapi secret")
    msg = (await request.json()).get("message") or {}
    kind = msg.get("type")
    call = msg.get("call") or {}
    if kind == "assistant-request":
        # An inbound call on a number with no assistant of its own: the college's assistant,
        # with this engine answering (7.5 s limit on Vapi's side; this takes milliseconds).
        tenant = await asyncio.to_thread(_call_tenant, deps, call, {})
        if not tenant or not s.vapi_assistant_id:
            return {
                "error": "Sorry, this line is not set up yet. Please call again later."
            }
        conv = "vapi-" + (call.get("id") or uuid.uuid4().hex)
        if call.get("id"):
            vapi.remember(
                call["id"],
                vapi.VapiCall(tenant, conv, time.time(), "inbound"),
                s.database_url,
            )
        base = vapi.public_base(s, request.headers.get("host"))
        return {
            "assistantId": s.vapi_assistant_id,
            "assistantOverrides": vapi.overrides(s, tenant, conv, base),
        }
    if kind == "status-update":
        log.info("vapi call %s: %s", call.get("id"), msg.get("status"))
        if msg.get("status") == "ended":
            vapi.forget(call.get("id"))
    elif kind == "end-of-call-report":
        # The engine already stored every turn; the number and Vapi's transcript are not kept.
        log.info("vapi call %s ended: %s", call.get("id"), msg.get("endedReason"))
        vapi.forget(call.get("id"))
    return {}


# ------------------------------------------------------------------ placing a call


class CallIn(BaseModel):
    to: str = Field(
        pattern=vapi.E164,
        description="The number to call, international form (+91XXXXXXXXXX). Module C calls this "
        "only for a person who agreed to be contacted; Module B does not store it.",
    )


@router.post(
    "/voice/vapi/calls",
    status_code=201,
    summary="Place an outbound phone call through Vapi (service callers: Module C, operators)",
)
async def place_call(
    body: CallIn,
    request: Request,
    who: Caller = Depends(service_only),
    deps: Deps = Depends(deps_dep),
):
    s = deps.settings
    client = getattr(request.app.state, "vapi", None)
    missing = [
        name
        for name, value in (
            ("VAPI_API_KEY", client),
            ("VAPI_ASSISTANT_ID", s.vapi_assistant_id),
            ("VAPI_PHONE_NUMBER_ID", s.vapi_phone_number_id),
        )
        if not value
    ]
    base = vapi.public_base(s)
    if s.vapi_answers == "engine" and not base:
        missing.append("VAPI_PUBLIC_URL")
    if missing:
        raise HTTPException(503, f"Vapi calls are not configured: {', '.join(missing)}")
    if not await asyncio.to_thread(_vapi_allowed, deps, who.tenant_id):
        raise HTTPException(
            403,
            "Telephone calls are not enabled for this college (model_policy.voice_external)",
        )
    conv = "vapi-out-" + uuid.uuid4().hex
    try:
        made = await asyncio.to_thread(
            client.create_call,
            s.vapi_assistant_id,
            s.vapi_phone_number_id,
            body.to,
            vapi.overrides(s, who.tenant_id, conv, base),
        )
    except vapi.VapiError as exc:
        raise HTTPException(502, str(exc)) from None
    vapi.remember(
        made["id"],
        vapi.VapiCall(who.tenant_id, conv, time.time(), "outbound"),
        s.database_url,
    )
    return {
        "call_id": made["id"],
        "status": made.get("status"),
        "conversation_id": conv,
    }
