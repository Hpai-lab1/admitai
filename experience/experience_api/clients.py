"""Calls to Module A (Knowledge & Learning) and Module B (Assistant). The URLs come from
KNOWLEDGE_API_URL and ASSISTANT_API_URL; the mocks in experience/mocks/ still work for UI work."""

from __future__ import annotations

from uuid import UUID

import httpx

from .config import Settings
from .db import Context, Jsonb, tx

TIMEOUT = httpx.Timeout(5.0)


class UpstreamError(RuntimeError):
    """Module A or B could not be reached or refused the call."""


def knowledge_get(
    settings: Settings, path: str, tenant_id: UUID, params: dict | None = None
):
    try:
        r = httpx.get(
            f"{settings.knowledge_api_url}{path}",
            params=params,
            headers={
                "X-Tenant-Id": str(tenant_id),
                "X-Service-Key": settings.key_to_knowledge,
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as exc:
        raise UpstreamError(
            f"Knowledge service unavailable ({exc.__class__.__name__})"
        ) from exc


def knowledge_post(
    settings: Settings, path: str, tenant_id: UUID, body: dict, method: str = "POST"
):
    try:
        r = httpx.request(
            method,
            f"{settings.knowledge_api_url}{path}",
            json=body,
            headers={
                "X-Tenant-Id": str(tenant_id),
                "X-Service-Key": settings.key_to_knowledge,
            },
            timeout=TIMEOUT,
        )
        if 400 <= r.status_code < 500:
            # Module A's reason is meant for staff ("Write the answer in the note ..."); pass it on.
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = None
            raise UpstreamError(
                detail
                if isinstance(detail, str)
                else f"Knowledge service refused ({r.status_code})"
            )
        r.raise_for_status()
        return r.json() if r.content else {}
    except httpx.HTTPError as exc:
        raise UpstreamError(
            f"Knowledge service refused or unavailable ({exc.__class__.__name__})"
        ) from exc


class AssistantRefused(UpstreamError):
    """Module B answered but would not do it (opted out, no template, no WhatsApp number, not set
    up). `status` is B's HTTP status; retrying will not help unless something changes."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code


def assistant_post(settings: Settings, path: str, tenant_id: UUID, body: dict) -> dict:
    """A service call to Module B. Raises AssistantRefused when B says no (4xx, or 503 "not set
    up here"), UpstreamError when B cannot be reached or fails."""
    if not settings.key_to_assistant:
        raise UpstreamError("The assistant service is not configured (KEY_C_TO_B)")
    try:
        r = httpx.post(
            f"{settings.assistant_api_url}{path}",
            json=body,
            headers={
                "X-Service-Key": settings.key_to_assistant,
                "X-Tenant-Id": str(tenant_id),
            },
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise UpstreamError(
            f"Assistant service unavailable ({exc.__class__.__name__})"
        ) from exc
    if r.status_code < 400:
        return r.json() if r.content else {}
    try:
        detail = r.json().get("detail")
    except ValueError:
        detail = None
    detail = detail if isinstance(detail, str) else f"HTTP {r.status_code}"
    if r.status_code < 500 or r.status_code == 503:
        raise AssistantRefused(r.status_code, detail)
    raise UpstreamError(f"Assistant service error ({detail})")


def assistant_conversation(
    settings: Settings, tenant_id: UUID, conversation_id: str
) -> dict | None:
    """Module B's record of a conversation (transcript, sources, confidence, trace ids), or None if
    B is unavailable or does not know it. Used to build the handoff package (PRD F3.3)."""
    if not settings.key_to_assistant:
        return None
    try:
        r = httpx.get(
            f"{settings.assistant_api_url}/chat/{conversation_id}",
            headers={
                "X-Service-Key": settings.key_to_assistant,
                "X-Tenant-Id": str(tenant_id),
            },
            timeout=TIMEOUT,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError:
        return None


def assistant_analytics(settings: Settings, tenant_id: UUID, since) -> dict | None:
    """Module B's anonymous answer outcomes by channel and its WhatsApp conversations, or None if
    B is unavailable (PRD F9.1-F9.3: the dashboard covers every channel)."""
    if not settings.key_to_assistant:
        return None
    try:
        r = httpx.get(
            f"{settings.assistant_api_url}/analytics/turns",
            params={"since": since.isoformat()},
            headers={
                "X-Service-Key": settings.key_to_assistant,
                "X-Tenant-Id": str(tenant_id),
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError:
        return None


def handoff_package(view: dict) -> dict:
    """F3.3: transcript, the AI's last answer as a draft, the sources it used, trace and confidence."""
    msgs = view.get("messages", [])
    transcript = [
        {
            "role": m["sender"]
            if m["sender"] in ("user", "assistant", "staff")
            else "system",
            "text": m["content"],
        }
        for m in msgs[-40:]
    ]
    last = next((m for m in reversed(msgs) if m["sender"] == "assistant"), None)
    if last is None:
        return {"transcript": transcript}
    return {
        "transcript": transcript,
        "ai_draft": last["content"][:4000],
        "sources": [
            {
                "title": src["title"][:300],
                "url": src.get("url"),
                "section": (src.get("heading_path") or None),
                "academic_year": src.get("academic_year"),
            }
            for src in last.get("sources", [])[:20]
        ],
        "trace_id": str(last["trace_id"]) if last.get("trace_id") else None,
        "confidence": last.get("confidence"),
    }


def _erase_in_assistant(cur, settings: Settings, tenant_id: UUID, ev: dict) -> dict:
    """First half of an erasure: Module B blanks the session's transcripts and says which
    conversations they were. The ids are saved on the event, so a retry after Module A failed
    does not depend on Module B (which, being idempotent, would report nothing the second time)."""
    from .privacy import erase_conversations_feedback

    p = ev["payload"]
    if "conversation_ids" in p:
        return p
    r = httpx.post(
        f"{settings.assistant_api_url}/privacy/erase",
        json={"session_id": p["session_id"]},
        headers={
            "X-Service-Key": settings.key_to_assistant,
            "X-Tenant-Id": str(tenant_id),
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    ids = sorted(set(r.json()["conversations"]) | set(p.get("conversation_hint", [])))
    p = {"session_id": p["session_id"], "conversation_ids": ids}
    erase_conversations_feedback(cur, ids)
    cur.execute(
        "update app.outbox_events set payload = %s where id = %s", (Jsonb(p), ev["id"])
    )
    return p


def dispatch_events(settings: Settings, tenant_id: UUID, limit: int = 50) -> int:
    """Deliver pending events to Module A (at-least-once: Module A de-duplicates by event id).
    An erasure is first carried out by Module B. Returns how many were delivered."""
    delivered = 0
    with tx(settings.database_url, Context(tenant_id, "system")) as cur:
        cur.execute(
            "select id, type, payload, created_at from app.outbox_events"
            " where delivered_at is null and (attempts < 10 or type = 'erasure.requested') order by id limit %s for update skip locked",
            (limit,),
        )
        for ev in cur.fetchall():
            try:
                if ev["type"] == "erasure.requested":
                    ev["payload"] = _erase_in_assistant(cur, settings, tenant_id, ev)
                body = {
                    "event_id": ev["id"],
                    "type": ev["type"],
                    "tenant_id": str(tenant_id),
                    "occurred_at": ev["created_at"].isoformat(),
                    "data": ev["payload"],
                }
                r = httpx.post(
                    f"{settings.knowledge_api_url}/events",
                    json=body,
                    headers={"X-Service-Key": settings.key_to_knowledge},
                    timeout=TIMEOUT,
                )
                r.raise_for_status()
                cur.execute(
                    "update app.outbox_events set delivered_at = now(), attempts = attempts + 1,"
                    " last_error = null where id = %s",
                    (ev["id"],),
                )
                delivered += 1
            except httpx.HTTPError as exc:
                cur.execute(
                    "update app.outbox_events set attempts = attempts + 1, last_error = %s where id = %s",
                    (exc.__class__.__name__, ev["id"]),
                )
    return delivered
