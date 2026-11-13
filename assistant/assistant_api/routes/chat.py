"""Chat API (PRD 12.1, 21.4): streamed, checked answers; conversation view for the console."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import privacy, store
from ..auth import Caller, caller, service_only, visitor_limits
from ..guard import mask_pii
from ..leads import scoring_problems
from ..engine import Deps, Forbidden, handle_turn, live_turns

router = APIRouter(tags=["chat"])
log = logging.getLogger(__name__)
# Turns still running after their visitor disconnected (kept so they are not garbage-collected).
_running: set[asyncio.Task] = set()


class MessageIn(BaseModel):
    text: str = Field(max_length=4000)
    language: str | None = Field(default=None, max_length=5)
    channel: str = "web_chat"


def deps_dep(request: Request) -> Deps:
    return request.app.state.deps


def _sse(ev: dict) -> str:
    return f"event: {ev['event']}\ndata: {json.dumps(ev['data'], default=str, ensure_ascii=False)}\n\n"


@router.post(
    "/chat/{conversation_id}/messages",
    summary="Send a message; SSE stream of checked sentences, events and a final 'done'",
)
async def send_message(
    conversation_id: str,
    body: MessageIn,
    request: Request,
    who: Caller = Depends(caller),
    deps: Deps = Depends(deps_dep),
):
    if who.kind == "visitor":
        visitor_limits(request, who.tenant_id)
    if not 1 <= len(conversation_id) <= 100:
        raise HTTPException(422, "conversation_id must be 1-100 characters")
    if body.channel != "web_chat" and who.kind != "service":
        raise HTTPException(403, "Only services may post on other channels")
    events = handle_turn(
        deps, who, conversation_id, body.text, body.language, body.channel
    )
    try:
        first = await events.__anext__()
    except Forbidden as exc:
        raise HTTPException(403, str(exc)) from None

    # The rest of the turn runs on its own: if the visitor's connection drops mid-answer, the
    # answer still finishes and is saved, and the widget reads it back (GET /chat/{id}/last).
    queue: asyncio.Queue = asyncio.Queue()

    async def pump():
        try:
            async for ev in events:
                queue.put_nowait(ev)
        except Exception:
            log.exception("turn failed in conversation %s", conversation_id)
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(pump())
    _running.add(task)
    task.add_done_callback(_running.discard)

    async def stream():
        yield _sse(first)
        while (ev := await queue.get()) is not None:
            yield _sse(ev)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/chat/{conversation_id}/last",
    summary="The newest answer in the visitor's own conversation (recovery after a dropped stream)",
)
def last_answer(
    conversation_id: str,
    who: Caller = Depends(caller),
    deps: Deps = Depends(deps_dep),
):
    if who.kind != "visitor" or not who.session_id:
        raise HTTPException(403, "Only the visitor's own session may read this")
    row = store.last_answer(
        deps.settings.database_url, who.tenant_id, conversation_id, who.session_id
    )
    if row is None:
        raise HTTPException(404, "No answer yet")
    return {
        "message_id": str(row["id"]),
        "conversation_id": conversation_id,
        "question": row["question"],
        "text": row["answer"],
        "trace_id": row["trace_id"],
        "sources": row["sources"],
        "outcome": row["outcome"],
        "offer_handoff": row["outcome"] not in ("answered", "refused"),
    }


@router.get(
    "/chat/{conversation_id}",
    summary="Transcript with sources, confidence and versions (services)",
)
def conversation(
    conversation_id: str,
    who: Caller = Depends(service_only),
    deps: Deps = Depends(deps_dep),
):
    view = store.conversation_view(
        deps.settings.database_url, who.tenant_id, conversation_id
    )
    if view is None:
        raise HTTPException(404, "Conversation not found")
    return view


OUTCOMES = {
    "answered",
    "partial",
    "no_answer",
    "blocked",
    "handoff",
    "refused",
    "error",
}


@router.get(
    "/signals",
    summary="Turns by outcome since a time: implicit signals for Module A (services)",
)
def get_signals(
    since: str | None = None,
    outcomes: str = "no_answer,blocked,partial",
    limit: int = 500,
    who: Caller = Depends(service_only),
    deps: Deps = Depends(deps_dep),
):
    wanted = [o for o in outcomes.split(",") if o in OUTCOMES]
    items = store.signals(
        deps.settings.database_url, who.tenant_id, since, wanted, min(limit, 2000)
    )
    return {"items": items}


@router.post(
    "/reviews/run", summary="Run the grounding review now (services; normally off-peak)"
)
async def run_reviews(
    limit: int = 50, who: Caller = Depends(service_only), deps: Deps = Depends(deps_dep)
):
    from .. import review

    return await review.run(deps.settings, deps.llm, who.tenant_id, min(limit, 500))


@router.get(
    "/analytics/turns",
    summary="Anonymous answer outcomes by channel and off-widget conversations (Module C)",
)
def analytics_turns(
    since: str, who: Caller = Depends(service_only), deps: Deps = Depends(deps_dep)
):
    from .. import analytics

    return analytics.turns(
        deps.settings.database_url,
        who.tenant_id,
        since,
        deps.settings.token_prices_inr,
    )


@router.get(
    "/analytics/topics",
    summary="Top intents and trending questions, compared with the previous period (Module C, F9.2)",
)
def analytics_topics(
    since: datetime, who: Caller = Depends(service_only), deps: Deps = Depends(deps_dep)
):
    from .. import analytics

    return analytics.topics(
        deps.settings.database_url, who.tenant_id, since.isoformat()
    )


class ReviewIn(BaseModel):
    message_ids: list[str] = Field(max_length=500)


@router.post(
    "/reviews/messages",
    summary="Review these answers now and return claim-level results (Module A's evaluation runs)",
)
async def review_messages(
    body: ReviewIn, who: Caller = Depends(service_only), deps: Deps = Depends(deps_dep)
):
    from .. import review

    return {
        "items": await review.review_ids(
            deps.settings, deps.llm, who.tenant_id, body.message_ids
        )
    }


@router.get(
    "/reviews/unsupported",
    summary="Answers the grounding review found unsupported (services)",
)
def unsupported_reviews(
    since: str | None = None,
    who: Caller = Depends(service_only),
    deps: Deps = Depends(deps_dep),
):
    from .. import review

    return {
        "items": review.unsupported(deps.settings.database_url, who.tenant_id, since)
    }


@router.get(
    "/messages/{message_id}",
    summary="One answer with its question and citations (services)",
)
def get_message(
    message_id: str, who: Caller = Depends(service_only), deps: Deps = Depends(deps_dep)
):
    m = store.message(deps.settings.database_url, who.tenant_id, message_id)
    if m is None:
        raise HTTPException(404, "Message not found")
    return m


class LookupIn(BaseModel):
    ids: list[str] = Field(max_length=200)


@router.post(
    "/messages/lookup",
    summary="Several answers with the question each answered (services; the console's review "
    "queue). Phone numbers and e-mail addresses are masked",
)
def lookup_messages(
    body: LookupIn, who: Caller = Depends(service_only), deps: Deps = Depends(deps_dep)
):
    rows = store.messages(deps.settings.database_url, who.tenant_id, body.ids)
    return {
        "items": {
            str(r["id"]): {
                "question": mask_pii(r["question"] or ""),
                "answer": mask_pii(r["answer"] or ""),
                "outcome": r["outcome"],
            }
            for r in rows
        }
    }


@router.get(
    "/load",
    summary="Answers being written for visitors right now (services): Module A's evaluation "
    "gate waits while this is above zero",
)
def load(who: Caller = Depends(service_only)):
    return {"live_turns": live_turns()}


@router.get("/traces/{trace_id}", summary="Spans and timings of one turn (services)")
def get_trace(
    trace_id: str, who: Caller = Depends(service_only), deps: Deps = Depends(deps_dep)
):
    t = store.trace(deps.settings.database_url, who.tenant_id, trace_id)
    if t is None:
        raise HTTPException(404, "Trace not found")
    return t


@router.put(
    "/tenant-settings", summary="Agent configuration from the Tenant Pack (services)"
)
def put_settings(
    config: dict, who: Caller = Depends(service_only), deps: Deps = Depends(deps_dep)
):
    found = scoring_problems(config.get("lead_scoring"))
    if found:
        raise HTTPException(422, "; ".join(found))
    return store.save_tenant_config(deps.settings.database_url, who.tenant_id, config)


@router.delete(
    "/tenant-data",
    summary="Delete a sandbox tenant's data after an evaluation (services)",
)
def delete_tenant_data(
    who: Caller = Depends(service_only), deps: Deps = Depends(deps_dep)
):
    deleted = store.delete_sandbox(deps.settings.database_url, who.tenant_id)
    if deleted is None:
        raise HTTPException(409, "Only sandbox tenants can be deleted")
    return {"deleted": deleted}


class EraseIn(BaseModel):
    session_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")


@router.post(
    "/privacy/erase",
    summary="Erase every conversation of a visitor session (Module C, on an erasure request)",
)
def erase(
    body: EraseIn, who: Caller = Depends(service_only), deps: Deps = Depends(deps_dep)
):
    return privacy.erase_session(
        deps.settings.database_url, who.tenant_id, body.session_id
    )


@router.post(
    "/tenant-pack",
    summary="Agent part of a Tenant Pack: the parsed pack.yaml (services)",
)
def put_tenant_pack(
    pack: dict, who: Caller = Depends(service_only), deps: Deps = Depends(deps_dep)
):
    from .. import pack as pack_loader

    try:
        return pack_loader.apply(deps.settings, who.tenant_id, pack)
    except pack_loader.PackProblems as exc:
        raise HTTPException(422, {"problems": exc.problems}) from None


@router.post(
    "/tenant-pack/validate",
    summary="Check the agent part of a Tenant Pack before anything is loaded (services)",
)
def validate_tenant_pack(
    pack: dict, who: Caller = Depends(service_only), deps: Deps = Depends(deps_dep)
):
    from .. import pack as pack_loader

    return {"problems": pack_loader.validate(deps.settings, pack)}


@router.get("/tenant-settings", summary="Effective agent configuration (services)")
def get_settings(who: Caller = Depends(service_only), deps: Deps = Depends(deps_dep)):
    return store.tenant_config(deps.settings.database_url, who.tenant_id)
