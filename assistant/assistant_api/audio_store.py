"""Consented voice recordings (PRD F2.6), off by default.

A spoken question is kept only when both hold:
1. the college turned it on: pack.yaml `voice: {store_audio: true}`; and
2. the visitor consented: POST /voice/token with `audio_consent: true`, which records Module C's
   consent purpose `audio_recording` before anything is stored.

Recordings expire after `retention.audio_days` (default 30, PRD F2.6), are deleted by the nightly
retention sweep, by an erasure request (privacy.erase_conversations) and when the visitor withdraws
audio consent (POST /privacy/erase with scope "audio"). Recordings are never written to logs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import store
from .db import tx

DEFAULT_AUDIO_DAYS = 30  # PRD F2.6
MAX_AUDIO_DAYS = 365
MAX_BYTES = 6 * 1024 * 1024  # the table refuses more (009_voice_audio.sql)
CONSENT_PURPOSE = "audio_recording"  # Module C's consent purpose
CONSENT_TEXT_VERSION = "voice-audio-v1"


def policy(database_url: str, tenant: str) -> dict:
    cfg = store.tenant_config(database_url, tenant)
    voice = cfg.get("voice") or {}
    days = (cfg.get("retention") or {}).get("audio_days", DEFAULT_AUDIO_DAYS)
    return {"store_audio": voice.get("store_audio") is True, "audio_days": int(days)}


def problems(pack: dict) -> list[str]:
    """Pack checks: a wrong value must fail loudly, never store audio by accident."""
    out = []
    voice = pack.get("voice")
    if voice is not None:
        if not isinstance(voice, dict):
            out.append("voice must be a mapping")
        elif "store_audio" in voice and not isinstance(voice["store_audio"], bool):
            out.append("voice.store_audio must be true or false")
    days = (pack.get("retention") or {}).get("audio_days")
    if days is not None and (
        not isinstance(days, int)
        or isinstance(days, bool)
        or not 1 <= days <= MAX_AUDIO_DAYS
    ):
        out.append(
            f"retention.audio_days must be a whole number from 1 to {MAX_AUDIO_DAYS}"
        )
    return out


def save(
    database_url: str,
    tenant: str,
    conversation_id: str,
    session_id: str | None,
    channel: str,
    wav: bytes,
    transcript: str | None,
    days: int,
    now: datetime | None = None,
) -> str | None:
    # Consent belongs to a visitor session: without one there is nobody who consented.
    if not wav or len(wav) > MAX_BYTES or not session_id:
        return None
    now = now or datetime.now(timezone.utc)
    with tx(database_url, tenant) as cur:
        # One statement: a withdrawal recorded a moment ago always wins (no check-then-insert gap).
        cur.execute(
            "insert into chat.voice_audio (tenant_id, conversation_id, session_id, channel, audio,"
            " transcript, created_at, expires_at) select %s,%s,%s,%s,%s,%s,%s,%s"
            " where not exists (select 1 from chat.audio_withdrawals where session_id = %s)"
            " returning id",
            (
                tenant,
                conversation_id,
                session_id,
                channel,
                wav,
                transcript,
                now,
                now + timedelta(days=days),
                session_id,
            ),
        )
        row = cur.fetchone()
        return str(row["id"]) if row else None


def withdraw(cur, session_id: str) -> None:
    """Withdrawn consent or erasure: refuse this session's recordings from now on (the cursor is
    tenant-scoped), even with a voice token issued before."""
    cur.execute(
        "insert into chat.audio_withdrawals (tenant_id, session_id) values"
        " (chat.current_tenant(), %s) on conflict (tenant_id, session_id) do update"
        " set withdrawn_at = now()",
        (session_id,),
    )


def consented(database_url: str, tenant: str, session_id: str) -> None:
    """A new consent, recorded in Module C, lifts an earlier withdrawal."""
    with tx(database_url, tenant) as cur:
        cur.execute(
            "delete from chat.audio_withdrawals where session_id = %s", (session_id,)
        )


def delete_for_conversations(cur, ids: list[str]) -> int:
    if not ids:
        return 0
    cur.execute("delete from chat.voice_audio where conversation_id = any(%s)", (ids,))
    return cur.rowcount


def delete_for_session(database_url: str, tenant: str, session_id: str) -> int:
    """Withdrawn audio consent: every recording of this visitor goes, none is made from now on;
    transcripts stay."""
    with tx(database_url, tenant) as cur:
        withdraw(cur, session_id)
        cur.execute(
            "delete from chat.voice_audio where session_id = %s or conversation_id in"
            " (select id from chat.conversations where session_id = %s)",
            (session_id, session_id),
        )
        return cur.rowcount


def sweep(database_url: str, tenant: str, now: datetime | None = None) -> int:
    with tx(database_url, tenant) as cur:
        cur.execute(
            "delete from chat.voice_audio where expires_at <= %s",
            (now or datetime.now(timezone.utc),),
        )
        return cur.rowcount


def listing(database_url: str, tenant: str, conversation_id: str) -> list[dict]:
    """What is kept for a conversation (metadata only; for staff tooling and tests)."""
    with tx(database_url, tenant) as cur:
        cur.execute(
            "select id::text, channel, octet_length(audio) as bytes, transcript, created_at,"
            " expires_at from chat.voice_audio where conversation_id = %s order by created_at",
            (conversation_id,),
        )
        return list(cur.fetchall())
