"""Erasure on request and retention (PRD 11.4; DPDP Act "erasure on request").

Module C owns consent, so it coordinates erasure: it blanks its own rows for a visitor session at
once, then queues an `erasure.requested` event. Delivering that event asks Module B to blank the
session's transcripts and passes the erased conversation ids to Module A for its learning signals.
Anonymous counts (outcomes, stages, scores) are kept, as the PRD allows.

python -m experience_api.privacy outbox      # nightly: retry undelivered events (erasures too)
python -m experience_api.privacy retention   # nightly: anonymise leads inactive past the limit,
                                             # remove audit rows older than 3 years
"""

from __future__ import annotations

import argparse
import sys
from uuid import UUID

import psycopg

from .config import Settings
from .db import Context, Jsonb, audit, tx
from .routes.consents_leads import anonymise_session_leads

# PRD 11.4 defaults; a college may set retention.leads_inactive_days in its tenant config.
DEFAULT_LEAD_INACTIVE_DAYS = 548  # 18 months


def erase_session(cur, ctx: Context, session_id: UUID, requested_by: str) -> dict:
    """Blank this session's personal data in Module C and queue the erasure for Modules B and A.
    The cursor must run as 'system' in the session's tenant. Idempotent."""
    cur.execute(
        "update app.consents set withdrawn_at = now() where session_id = %s and withdrawn_at is null",
        (session_id,),
    )
    consents = cur.rowcount
    leads = anonymise_session_leads(cur, ctx, session_id)
    cur.execute(
        "update app.guardian_verifications set destination = '[erased]'"
        " where session_id = %s and destination <> '[erased]'",
        (session_id,),
    )
    cur.execute(
        "update app.handoffs set context = jsonb_build_object('erased', true),"
        " preferred_time = null, updated_at = now()"
        " where session_id = %s and not coalesce((context->>'erased')::boolean, false)"
        " returning id, conversation_id",
        (session_id,),
    )
    handoffs = cur.fetchall()
    if handoffs:
        cur.execute(
            "update app.handoff_events set body = null where handoff_id = any(%s)",
            ([h["id"] for h in handoffs],),
        )
    cur.execute(
        "update app.visitor_sessions set referrer = null, erased_at = now()"
        " where id = %s and erased_at is null",
        (session_id,),
    )
    first_time = cur.rowcount == 1
    if first_time:
        cur.execute(
            "insert into app.outbox_events (tenant_id, type, payload) values (%s, 'erasure.requested', %s)",
            (
                ctx.tenant_id,
                Jsonb(
                    {
                        "session_id": str(session_id),
                        # Known from handoffs; Module B adds the rest when the event is delivered.
                        "conversation_hint": sorted(
                            {h["conversation_id"] for h in handoffs}
                        ),
                    }
                ),
            ),
        )
    result = {
        "consents_withdrawn": consents,
        "leads_anonymised": leads,
        "handoffs_cleared": len(handoffs),
        "queued": first_time,
    }
    audit(
        cur,
        ctx,
        "privacy.erasure_requested",
        "visitor_session",
        session_id,
        after={**result, "requested_by": requested_by},
    )
    return result


def erase_conversations_feedback(cur, conversation_ids: list[str]) -> int:
    """Free-text feedback comments on erased conversations (ratings themselves are kept)."""
    if not conversation_ids:
        return 0
    cur.execute(
        "update app.feedback set comment = null where conversation_id = any(%s) and comment is not null",
        (conversation_ids,),
    )
    return cur.rowcount


def retention_sweep(settings: Settings, tenant_id: UUID) -> dict:
    ctx = Context(tenant_id, "system")
    with tx(settings.database_url, ctx) as cur:
        cur.execute("select config from app.tenants where id = %s", (tenant_id,))
        row = cur.fetchone()
        days = (
            ((row or {}).get("config") or {})
            .get("retention", {})
            .get("leads_inactive_days", DEFAULT_LEAD_INACTIVE_DAYS)
        )
        cur.execute(
            "select distinct session_id from app.leads where anonymised_at is null"
            " and updated_at < now() - make_interval(days => %s)",
            (int(days),),
        )
        leads = sum(
            anonymise_session_leads(cur, ctx, r["session_id"]) for r in cur.fetchall()
        )
    return {"leads_inactive_days": days, "leads_anonymised": leads}


def purge_old_audit(admin_url: str) -> int:
    """Audit rows past 3 years (PRD 11.4). Needs the migration role: the API role cannot delete."""
    with psycopg.connect(admin_url) as conn:
        return conn.execute(
            "delete from app.audit_logs where at < now() - interval '3 years'"
        ).rowcount


if __name__ == "__main__":
    from .clients import dispatch_events

    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["retention", "outbox"])
    a = ap.parse_args()
    s = Settings()
    if not (s.database_url and s.admin_database_url):
        sys.exit("Set DATABASE_URL and ADMIN_DATABASE_URL (source local.env.sh)")
    with psycopg.connect(s.admin_database_url) as conn:
        tenants = [
            r[0] for r in conn.execute("select id from app.tenants order by slug")
        ]
    for tid in tenants:
        if (
            a.command == "outbox"
        ):  # retry whatever a module was down for (erasures included)
            print(tid, "delivered:", dispatch_events(s, tid, limit=500))
        else:
            print(tid, retention_sweep(s, tid))
    if a.command == "retention":
        print("audit rows past 3 years removed:", purge_old_audit(s.admin_database_url))
