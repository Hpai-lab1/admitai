"""Erasure on request and retention (PRD 11.4, DPDP "erasure on request").

A conversation is erased by blanking everything a person wrote or could be identified by (message
text, summary, state, WhatsApp contact hash, traces, the source text kept for grounding review),
while the turn rows stay with their outcome, timings and versions: anonymous analytics are kept,
as the PRD asks. Module C decides when (it owns consent); this module does it for its own schema.

python -m assistant_api.privacy retention   # nightly: erase transcripts older than each tenant's limit
"""

from __future__ import annotations

import argparse
import sys

import psycopg

from . import store
from .config import Settings
from .db import tx

ERASED = "[erased]"
# "Admission cycle + 12 months" (PRD 11.4); a tenant sets retention.transcript_days in its pack.
DEFAULT_TRANSCRIPT_DAYS = 730


def erase_conversations(cur, ids: list[str]) -> dict:
    """Blank the personal content of these conversations (the cursor is already tenant-scoped)."""
    if not ids:
        return {"conversations": [], "messages": 0}
    cur.execute(
        "update chat.messages set content = %s where conversation_id = any(%s) and content <> %s"
        " returning id",
        (ERASED, ids, ERASED),
    )
    message_ids = [r["id"] for r in cur.fetchall()]
    cur.execute("select id from chat.messages where conversation_id = any(%s)", (ids,))
    all_ids = [r["id"] for r in cur.fetchall()]
    cur.execute(
        "update chat.message_citations set content = null where message_id = any(%s)",
        (all_ids,),
    )
    cur.execute(
        "update chat.grounding_reviews set sentences = '[]' where message_id = any(%s)",
        (all_ids,),
    )
    cur.execute("delete from chat.traces where message_id = any(%s)", (all_ids,))
    cur.execute(
        "delete from chat.wa_outbox where contact_hash in (select wa_contact_hash"
        " from chat.conversations where id = any(%s) and wa_contact_hash is not null)",
        (ids,),
    )
    cur.execute(
        "update chat.conversations set summary = null, state = '{}', wa_contact_hash = null,"
        " erased_at = now() where id = any(%s)",
        (ids,),
    )
    return {"conversations": ids, "messages": len(message_ids)}


def erase_session(database_url: str, tenant: str, session_id: str) -> dict:
    """Every conversation of one visitor session: web chat and voice carry the session id; a
    WhatsApp conversation records Module C's session in its state."""
    with tx(database_url, tenant) as cur:
        cur.execute(
            "select c.id from chat.conversations c where (c.session_id = %s"
            " or c.state->>'c_session' = %s) and (c.erased_at is null or exists (select 1"
            " from chat.messages m where m.conversation_id = c.id and m.content <> '[erased]'))",
            (session_id, session_id),
        )
        return erase_conversations(cur, [r["id"] for r in cur.fetchall()])


def retention_sweep(database_url: str, tenant: str) -> dict:
    days = (store.tenant_config(database_url, tenant).get("retention") or {}).get(
        "transcript_days", DEFAULT_TRANSCRIPT_DAYS
    )
    with tx(database_url, tenant) as cur:
        cur.execute(
            "select id from chat.conversations where erased_at is null"
            " and last_message_at < now() - make_interval(days => %s)",
            (int(days),),
        )
        result = erase_conversations(cur, [r["id"] for r in cur.fetchall()])
        # Guardian codes that were never collected (F5.3) go once they expire.
        cur.execute("delete from chat.guardian_codes where expires_at < now()")
        expired_codes = cur.rowcount
    return {"transcript_days": days, "guardian_codes_expired": expired_codes, **result}


def _tenants(admin_url: str) -> list[str]:
    # Listing tenants crosses RLS, so only this read uses the admin connection; the work itself
    # runs tenant by tenant as the restricted role.
    with psycopg.connect(admin_url) as conn:
        rows = conn.execute(
            "select tenant_id::text from chat.tenant_settings"
            " union select distinct tenant_id::text from chat.conversations"
        ).fetchall()
    return sorted(r[0] for r in rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["retention"])
    ap.add_argument("--tenant-id")
    a = ap.parse_args()
    s = Settings()
    if not s.database_url or not (a.tenant_id or s.admin_database_url):
        sys.exit(
            "Set CHAT_DATABASE_URL and CHAT_ADMIN_DATABASE_URL (source local.env.sh)"
        )
    for tid in [a.tenant_id] if a.tenant_id else _tenants(s.admin_database_url):
        r = retention_sweep(s.database_url, tid)
        print(
            tid,
            r["transcript_days"],
            "days:",
            len(r["conversations"]),
            "conversations erased",
        )
