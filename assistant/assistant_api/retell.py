"""Retell AI as the voice layer (web calls). Retell does speech-to-text, turn-taking and
text-to-speech; every answer still comes from this service's engine through Retell's "custom LLM"
WebSocket, so a spoken answer is checked, cited and logged exactly like a chat answer.

Flow:
1. The widget asks POST /voice/token; with RETELL_API_KEY and RETELL_AGENT_ID set, the answer
   also carries a Retell web-call access token (create_web_call below).
2. The widget starts the call with Retell's browser SDK.
3. Retell opens wss://<this service>/voice/retell/llm/{call_id} (set as the agent's Custom LLM
   URL in the Retell dashboard) and sends the transcript; routes/retell.py answers sentence by
   sentence through voice_turns.spoken_answer.

Only calls created here are answered: the call id must be in `CALLS` (written when the token is
issued), so nobody else's Retell agent can use this endpoint.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass

import httpx

API = "https://api.retellai.com"
CALL_TTL_S = 2 * 60 * 60


@dataclass
class RetellCall:
    tenant: str
    conversation_id: str
    session_id: str | None
    language: str | None
    created: float


CALLS: dict[str, RetellCall] = {}
_lock = threading.Lock()


class RetellError(Exception):
    pass


def create_web_call(
    api_key: str, agent_id: str, metadata: dict, client: httpx.Client | None = None
) -> dict:
    """Browser connection details for a call to `agent_id`: access_token, call_id, and (v3) the
    transport and ICE servers the browser SDK needs. v2 was retired by Retell in autumn 2026."""
    c = client or httpx.Client(timeout=15)
    try:
        r = c.post(
            f"{API}/v3/create-web-call",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"agent_id": agent_id, "metadata": metadata},
        )
    except httpx.HTTPError as exc:
        raise RetellError(f"Retell is unreachable: {exc}") from None
    if r.status_code >= 300:
        raise RetellError(f"Retell refused the call ({r.status_code}): {r.text[:200]}")
    body = r.json()
    return {
        "access_token": body["access_token"],
        "call_id": body["call_id"],
        "transport": body.get("transport"),
        "ice_servers": body.get("ice_servers") or [],
    }


_MARKERS = re.compile(r"\s*\[\d+(?:\s*,\s*\d+)*\]")


def speakable(sentence: str) -> str:
    """The sentence as Retell should say it: citation markers ("[1]") are for the screen; read
    aloud they would be "bracket one". Spaces between streamed pieces are kept."""
    if not sentence.strip():
        return ""
    return _MARKERS.sub("", sentence)


def remember(call_id: str, call: RetellCall, database_url: str | None = None) -> None:
    """Memory is the cache; with `database_url` the call is also kept in the database, so a
    restart or a second worker still knows it (voice_calls.py)."""
    now = time.time()
    with _lock:
        for k in [k for k, v in CALLS.items() if now - v.created > CALL_TTL_S]:
            CALLS.pop(k, None)
        CALLS[call_id] = call
    if database_url:
        from . import voice_calls

        voice_calls.save(
            database_url,
            "retell",
            call_id,
            call.tenant,
            call.conversation_id,
            call.session_id,
            call.language,
        )


def lookup(call_id: str, database_url: str | None = None) -> RetellCall | None:
    with _lock:
        call = CALLS.get(call_id)
    if call is None and database_url:
        from . import voice_calls

        row = voice_calls.load(database_url, "retell", call_id)
        if row:
            call = RetellCall(
                row["tenant_id"],
                row["conversation_id"],
                str(row["session_id"]) if row["session_id"] else None,
                row["language"],
                row["created"],
            )
            with _lock:
                CALLS[call_id] = call
    if call and time.time() - call.created > CALL_TTL_S:
        return None
    return call


def last_user_text(transcript: list[dict]) -> str:
    """Everything the visitor said since the assistant last spoke (Retell sends the whole call
    transcript each time, and may split one question: "What is the application fee" / "for the
    course?")."""
    parts: list[str] = []
    for turn in reversed(transcript or []):
        if turn.get("role") != "user":
            if parts:
                break
            continue
        if (turn.get("content") or "").strip():
            parts.append(turn["content"].strip())
    return " ".join(reversed(parts))


def response(
    response_id: int, content: str, complete: bool, end_call: bool = False
) -> dict:
    return {
        "response_type": "response",
        "response_id": response_id,
        "content": content,
        "content_complete": complete,
        "end_call": end_call,
    }
