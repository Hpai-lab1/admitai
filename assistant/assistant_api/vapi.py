"""Vapi (https://vapi.ai) phone calls, in and out, in English (routes/vapi.py).

Vapi does the telephony, the hearing and the speaking, with the college's saved Vapi assistant
(its voice, greeting and transcriber). With VAPI_ANSWERS=engine (the default) every answer comes
from this service's engine, so a phone answer is checked, cited and logged like a chat answer:
the assistant's model is swapped for Vapi's custom-LLM hook for that one call
(`assistantOverrides.model`), which Vapi documents as leaving the saved assistant unchanged.
Retell (retell.py) is a separate voice channel and is not touched by any of this.

- Outbound: `POST /voice/vapi/calls` (Module C or an operator, service key) -> Vapi
  `POST /call` with the assistant, the college's Vapi number and the overrides below.
- Inbound: the Vapi number has no assistant of its own and points at `/voice/vapi/webhook`;
  Vapi asks there (`assistant-request`) and gets the assistant plus the same overrides.
- Each turn: Vapi POSTs the conversation to `/voice/vapi/chat/completions` (OpenAI format) and
  reads the answer back as OpenAI streaming chunks.
- Only calls created here, or requests carrying VAPI_SERVER_SECRET, are answered.
- The caller's phone number is never stored or logged; the conversation is `vapi-<id>`.

    python -m assistant_api.vapi register <vapi-phone-number-id> --tenant-id <uuid>
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

import httpx

log = logging.getLogger(__name__)

API = "https://api.vapi.ai"
CALL_TTL_S = 2 * 60 * 60
RETRYABLE = {429, 500, 502, 503, 504}
E164 = r"^\+[1-9]\d{7,14}$"
MODEL_NAME = "admitai-engine"
CHANNEL = "vapi"


# ------------------------------------------------------------------ calls this service knows


@dataclass
class VapiCall:
    tenant: str
    conversation_id: str
    created: float
    direction: str  # inbound | outbound


CALLS: dict[str, VapiCall] = {}
_lock = threading.Lock()


def remember(call_id: str, call: VapiCall, database_url: str | None = None) -> None:
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
            "vapi",
            call_id,
            call.tenant,
            call.conversation_id,
            direction=call.direction,
        )


def lookup(call_id: str | None, database_url: str | None = None) -> VapiCall | None:
    if not call_id:
        return None
    with _lock:
        call = CALLS.get(call_id)
    if call is None and database_url:
        from . import voice_calls

        row = voice_calls.load(database_url, "vapi", call_id)
        if row:
            call = VapiCall(
                row["tenant_id"],
                row["conversation_id"],
                row["created"],
                row["direction"],
            )
            with _lock:
                CALLS[call_id] = call
    if call and time.time() - call.created > CALL_TTL_S:
        return None
    return call


def forget(call_id: str | None) -> None:
    with _lock:
        CALLS.pop(call_id or "", None)


# ------------------------------------------------------------------ Vapi's API


class VapiError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class VapiClient:
    """Vapi's REST API with the private key (server side only; never sent to a browser)."""

    api_key: str
    base_url: str = API
    timeout_s: float = 15.0
    retries: int = 1
    client: httpx.Client | None = None
    sleep: Callable[[float], None] = time.sleep

    @classmethod
    def from_settings(cls, s) -> "VapiClient | None":
        return cls(s.vapi_api_key) if s.vapi_api_key else None

    def create_call(
        self,
        assistant_id: str,
        phone_number_id: str,
        number: str,
        overrides: dict | None = None,
        name: str | None = None,
    ) -> dict:
        """Place a phone call from the college's Vapi number to `number` (E.164)."""
        if not re.match(E164, number or ""):
            raise VapiError(
                "The number must be in international form: +91 then the ten digits (+91XXXXXXXXXX)"
            )
        body: dict = {
            "assistantId": assistant_id,
            "phoneNumberId": phone_number_id,
            "customer": {"number": number},
        }
        if overrides:
            body["assistantOverrides"] = overrides
        if name:
            body["name"] = name[:40]
        return self.request("POST", "/call", body)

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        own = self.client is None
        c = self.client or httpx.Client(timeout=self.timeout_s)
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            for attempt in range(self.retries + 1):
                try:
                    r = c.request(
                        method, f"{self.base_url}{path}", headers=headers, json=body
                    )
                except httpx.HTTPError as exc:
                    if attempt < self.retries:
                        self.sleep(0.5 * 2**attempt)
                        continue
                    raise VapiError(
                        f"Vapi is unreachable: {type(exc).__name__}"
                    ) from None
                log.info("vapi %s %s -> %d", method, path, r.status_code)
                if r.status_code < 300:
                    return r.json() if r.content else {}
                if r.status_code in RETRYABLE and attempt < self.retries:
                    self.sleep(0.5 * 2**attempt)
                    continue
                raise VapiError(
                    f"Vapi refused ({r.status_code}): {_error(r)}", r.status_code
                )
        finally:
            if own:
                c.close()
        raise VapiError("Vapi: no attempt made")  # pragma: no cover


def _error(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return r.text[:200]
    msg = body.get("message") if isinstance(body, dict) else body
    if isinstance(msg, list):
        msg = "; ".join(str(m) for m in msg)
    return re.sub(r"\s+", " ", str(msg or body))[:200]


# ------------------------------------------------------------------ per-call settings


def public_base(settings, host: str | None = None) -> str | None:
    """This service's public https address: VAPI_PUBLIC_URL, else the address Vapi itself just
    called (a webhook through the tunnel), else None."""
    if settings.vapi_public_url:
        return settings.vapi_public_url.rstrip("/")
    return f"https://{host}" if host else None


def overrides(settings, tenant: str, conversation_id: str, base: str | None) -> dict:
    """What one call changes on the saved assistant: the answers come from this engine (custom
    LLM) and the call's status comes back to the webhook. Everything else stays as saved."""
    out: dict = {"metadata": {"tid": tenant, "cid": conversation_id}}
    if settings.vapi_answers == "engine" and base:
        out["model"] = {
            "provider": "custom-llm",
            "url": f"{base}/voice/vapi",
            "model": MODEL_NAME,
            # Vapi replaces the whole model object for a different provider; the prompt here is
            # not used for answers (they come from the engine), only kept for Vapi's logs.
            "messages": [
                {
                    "role": "system",
                    "content": "Answers come from the college's AdmitAI service (checked against "
                    "its own sources).",
                }
            ],
        }
    if base and settings.vapi_server_secret:
        out["server"] = {
            "url": f"{base}/voice/vapi/webhook",
            "headers": {"X-Vapi-Secret": settings.vapi_server_secret},
        }
        out["serverMessages"] = ["status-update", "end-of-call-report"]
    return out


# ------------------------------------------------------------------ OpenAI-format replies


def last_user_text(messages: list | None) -> str:
    """Everything the caller said since the assistant last spoke (as retell.last_user_text)."""
    parts: list[str] = []
    for m in reversed(messages or []):
        if not isinstance(m, dict):
            continue
        if m.get("role") != "user":
            if parts:
                break
            continue
        content = m.get("content")
        if isinstance(content, list):  # [{"type": "text", "text": ...}]
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict)
            )
        if (content or "").strip():
            parts.append(content.strip())
    return " ".join(reversed(parts))


@dataclass
class Completion:
    id: str = field(default_factory=lambda: "chatcmpl-" + uuid.uuid4().hex[:24])
    created: int = field(default_factory=lambda: int(time.time()))

    def chunk(
        self, content: str | None = None, finish: str | None = None, role: bool = False
    ) -> str:
        delta: dict = {}
        if role:
            delta["role"] = "assistant"
        if content is not None:
            delta["content"] = content
        body = {
            "id": self.id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": MODEL_NAME,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"

    def whole(self, content: str) -> dict:
        return {
            "id": self.id,
            "object": "chat.completion",
            "created": self.created,
            "model": MODEL_NAME,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        }


DONE = "data: [DONE]\n\n"


# ------------------------------------------------------------------ numbers -> colleges


def tenant_for_number(database_url: str, phone_number_id: str | None) -> str | None:
    if not phone_number_id:
        return None
    import psycopg

    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            "select chat.tenant_for_channel(%s, %s)", (CHANNEL, phone_number_id)
        ).fetchone()
    return str(row[0]) if row and row[0] else None


def main() -> int:
    from . import store
    from .config import Settings

    ap = argparse.ArgumentParser(description="Vapi phone numbers for Module B")
    sub = ap.add_subparsers(dest="cmd", required=True)
    reg = sub.add_parser("register", help="answer calls on a Vapi number for a college")
    reg.add_argument("phone_number_id")
    reg.add_argument("--tenant-id", required=True)
    a = ap.parse_args()
    s = Settings()
    if a.cmd == "register":
        store.register_channel(s.database_url, a.tenant_id, CHANNEL, a.phone_number_id)
        # the number's id is not printed: it is in the command just typed
        print(f"Calls on that Vapi number are answered for {a.tenant_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
