"""Live voice calls in the database (migration 013), so a restart of this service or a second
worker does not drop a Retell or Vapi call in progress. retell.py and vapi.py keep their
in-memory registry as a cache and call these when they are given a database address.

Seen 18 Nov 2026: the registry was only a dict in one process, and calls failed after restarts.
"""

from __future__ import annotations

import logging
from datetime import datetime

from .db import tx

log = logging.getLogger("admitai.voice_calls")
# Learning a call's college needs no tenant: chat.voice_call() reads only that one row.
_NO_TENANT = "00000000-0000-0000-0000-000000000000"


def save(
    database_url: str,
    provider: str,
    call_id: str,
    tenant: str,
    conversation_id: str,
    session_id: str | None = None,
    language: str | None = None,
    direction: str | None = None,
) -> None:
    try:
        with tx(database_url, tenant) as cur:
            cur.execute(
                "insert into chat.voice_calls (call_id, provider, tenant_id, conversation_id,"
                " session_id, language, direction) values (%s, %s, %s, %s, %s, %s, %s)"
                " on conflict (call_id) do nothing",
                (
                    call_id,
                    provider,
                    tenant,
                    conversation_id,
                    session_id,
                    language,
                    direction,
                ),
            )
            # calls are short: what is a day old is no longer needed
            cur.execute(
                "delete from chat.voice_calls where created_at < now() - interval '1 day'"
            )
    except Exception:  # noqa: BLE001 - memory still has the call; a restart would lose it
        log.exception("voice call %s not saved", provider)


def load(database_url: str, provider: str, call_id: str) -> dict | None:
    try:
        with tx(database_url, _NO_TENANT) as cur:
            cur.execute("select * from chat.voice_call(%s, %s)", (provider, call_id))
            row = cur.fetchone()
    except Exception:  # noqa: BLE001 - treated as an unknown call
        log.exception("voice call %s not read", provider)
        return None
    if not row:
        return None
    created: datetime = row["created_at"]
    return {**row, "tenant_id": str(row["tenant_id"]), "created": created.timestamp()}
