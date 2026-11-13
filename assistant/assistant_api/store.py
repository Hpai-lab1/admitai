"""Database reads and writes for the engine. Every call is tenant-scoped through RLS."""

from __future__ import annotations

from .db import Jsonb, tx

DEFAULT_CONFIG = {
    "college": "the college",
    "assistant_name": "Admissions Assistant",
    "tone": "warm, clear and concise; explain terms for parents",
    "languages": ["en", "hi", "kn"],
    "sensitive_topics": [
        "refund",
        "reservation",
        "quota",
        "complaint",
        "grievance",
        "ragging",
        "harassment",
        "caste certificate",
    ],
    "escalation": {"confidence_below": 0.55, "default_queue": "admission"},
    # Below these vector scores a search hit is not relevant enough to answer from (PRD 7.3
    # min_score; F1 "retrieval returns nothing relevant"). Measured on the RUAS and demo sets:
    # nomic answerable >= 0.543, bge-m3 answerable >= 0.414 and must-refuse mostly < 0.40.
    "retrieval": {"min_score": 0.50, "min_score_multilingual": 0.40},
    "office_hours": None,
    "departments": [],
}


def tenant_config(database_url: str, tenant: str) -> dict:
    with tx(database_url, tenant) as cur:
        cur.execute("select config from chat.tenant_settings")
        row = cur.fetchone()
    cfg = {**DEFAULT_CONFIG, **(row["config"] if row else {})}
    cfg["escalation"] = {
        **DEFAULT_CONFIG["escalation"],
        **(cfg.get("escalation") or {}),
    }
    return cfg


# Set by Module B itself from evaluation results (F2.10), not by the Tenant Pack: kept when the
# pack's agent settings are saved again.
MANAGED_KEYS = ("voice_quality",)


def save_tenant_config(database_url: str, tenant: str, config: dict) -> dict:
    with tx(database_url, tenant) as cur:
        cur.execute("select config from chat.tenant_settings")
        old = (cur.fetchone() or {}).get("config") or {}
        config = {
            **config,
            **{k: old[k] for k in MANAGED_KEYS if k in old and k not in config},
        }
        cur.execute(
            "insert into chat.tenant_settings (tenant_id, config) values (%s, %s)"
            " on conflict (tenant_id) do update set config = excluded.config,"
            " version = chat.tenant_settings.version + 1, updated_at = now()"
            " returning version",
            (tenant, Jsonb(config)),
        )
        return {"version": cur.fetchone()["version"]}


def set_managed(database_url: str, tenant: str, key: str, value: dict) -> None:
    assert key in MANAGED_KEYS
    with tx(database_url, tenant) as cur:
        cur.execute(
            "insert into chat.tenant_settings (tenant_id, config) values (%s, %s)"
            " on conflict (tenant_id) do update set config = chat.tenant_settings.config || excluded.config,"
            " updated_at = now()",
            (tenant, Jsonb({key: value})),
        )


def open_conversation(
    database_url: str,
    tenant: str,
    conversation_id: str,
    session_id: str | None,
    channel: str,
    origin: str = "visitor",
) -> dict | None:
    """Create on first message; afterwards only the same session may continue it."""
    with tx(database_url, tenant) as cur:
        cur.execute(
            "insert into chat.conversations (tenant_id, id, session_id, channel, origin)"
            " values (%s,%s,%s,%s,%s) on conflict (tenant_id, id) do nothing",
            (tenant, conversation_id, session_id, channel, origin),
        )
        # A person who erased their data and writes again (same WhatsApp number or widget
        # conversation) starts new content, which erasure and retention must still find.
        cur.execute(
            "update chat.conversations set erased_at = null where id = %s and erased_at is not null",
            (conversation_id,),
        )
        cur.execute(
            "select * from chat.conversations where id = %s", (conversation_id,)
        )
        conv = cur.fetchone()
    if (
        conv["session_id"] is not None
        and session_id is not None
        and str(conv["session_id"]) != session_id
    ):
        return None
    return conv


def history(
    database_url: str, tenant: str, conversation_id: str, turns: int
) -> list[dict]:
    with tx(database_url, tenant) as cur:
        cur.execute(
            "select sender, content, created_at from chat.messages where conversation_id = %s"
            " and content <> '[erased]' order by created_at desc limit %s",
            (conversation_id, turns * 2),
        )
        return list(reversed(cur.fetchall()))


def _masked(obj):
    """Traces and logs never hold PII (PRD 16): mask every string in every span."""
    from .guard import mask_pii

    if isinstance(obj, str):
        return mask_pii(obj)
    if isinstance(obj, list):
        return [_masked(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _masked(v) for k, v in obj.items()}
    return obj


def save_turn(
    database_url: str,
    tenant: str,
    conversation_id: str,
    state: dict,
    language: str,
    user_text: str,
    assistant: dict,
    citations: list[dict],
    spans: list[dict],
    summary: str | None = None,
) -> None:
    with tx(database_url, tenant) as cur:
        cur.execute(
            "insert into chat.messages (tenant_id, conversation_id, sender, content, language)"
            " values (%s,%s,'user',%s,%s)",
            (tenant, conversation_id, user_text, language),
        )
        a = assistant
        cur.execute(
            "insert into chat.messages (id, tenant_id, conversation_id, sender, content, language, agent,"
            " agent_version, prompt_version, model, confidence, trace_id, latency_ms, total_ms, route, checks,"
            " outcome, prompt_tokens, completion_tokens, created_at)"
            " values (%s,%s,%s,'assistant',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
            " now() + interval '1 millisecond')",
            (
                a["id"],
                tenant,
                conversation_id,
                a["content"],
                a["language"],
                a["agent"],
                a["agent_version"],
                a["prompt_version"],
                a["model"],
                a["confidence"],
                a["trace_id"],
                a["latency_ms"],
                a["total_ms"],
                Jsonb(a["route"]) if a["route"] else None,
                Jsonb(a["checks"]),
                a["outcome"],
                a.get("prompt_tokens"),
                a.get("completion_tokens"),
            ),
        )
        for c in citations:
            cur.execute(
                "insert into chat.message_citations (tenant_id, message_id, n, source_type, ref_id, title,"
                " academic_year, heading_path, url, content) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    tenant,
                    a["id"],
                    c["n"],
                    c["source_type"],
                    c.get("ref_id"),
                    c["title"],
                    c.get("academic_year"),
                    c.get("heading_path"),
                    c.get("url"),
                    c.get("content"),
                ),
            )
        cur.execute(
            "insert into chat.traces (trace_id, tenant_id, message_id, spans) values (%s,%s,%s,%s)",
            (a["trace_id"], tenant, a["id"], Jsonb(_masked(spans))),
        )
        cur.execute(
            "update chat.conversations set state = %s, language = %s, last_message_at = now(),"
            " status = coalesce(%s, status), summary = coalesce(%s, summary) where id = %s",
            (Jsonb(state), language, a.get("status"), summary, conversation_id),
        )


def conversation_view(
    database_url: str, tenant: str, conversation_id: str
) -> dict | None:
    """Transcript with sources, confidence and versions (for the staff console, PRD F7.2)."""
    with tx(database_url, tenant) as cur:
        cur.execute(
            "select * from chat.conversations where id = %s", (conversation_id,)
        )
        conv = cur.fetchone()
        if conv is None:
            return None
        cur.execute(
            "select id, sender, content, language, agent, agent_version, prompt_version, model, confidence,"
            " trace_id, latency_ms, outcome, created_at from chat.messages where conversation_id = %s"
            " order by created_at",
            (conversation_id,),
        )
        messages = cur.fetchall()
        cur.execute(
            "select c.message_id, c.n, c.source_type, c.ref_id, c.title, c.academic_year, c.heading_path, c.url"
            " from chat.message_citations c join chat.messages m on m.id = c.message_id"
            " where m.conversation_id = %s order by c.n",
            (conversation_id,),
        )
        cites: dict = {}
        for c in cur.fetchall():
            cites.setdefault(c.pop("message_id"), []).append(c)
    for m in messages:
        m["sources"] = cites.get(m["id"], [])
    return {
        "id": conv["id"],
        "session_id": str(conv["session_id"]) if conv["session_id"] else None,
        "channel": conv["channel"],
        "language": conv["language"],
        "status": conv["status"],
        "state": conv["state"],
        "started_at": conv["started_at"],
        "erased_at": conv.get("erased_at"),
        "messages": messages,
    }


def trace(database_url: str, tenant: str, trace_id: str) -> dict | None:
    with tx(database_url, tenant) as cur:
        cur.execute("select * from chat.traces where trace_id::text = %s", (trace_id,))
        return cur.fetchone()


_TURNS_SQL = (
    "select a.id, a.conversation_id, a.content as answer, a.outcome, a.language, a.trace_id,"
    " a.confidence, a.route, a.created_at,"
    " (select u.content from chat.messages u where u.conversation_id = a.conversation_id"
    "   and u.sender = 'user' and u.created_at < a.created_at order by u.created_at desc limit 1)"
    "   as question,"
    " coalesce((select jsonb_agg(jsonb_build_object('n', c.n, 'source_type', c.source_type,"
    "   'ref_id', c.ref_id, 'title', c.title, 'heading_path', c.heading_path) order by c.n)"
    "   from chat.message_citations c where c.message_id = a.id), '[]') as sources"
    " from chat.messages a where a.sender = 'assistant' and a.content <> '[erased]'"
)
# Learning signals come from real visitors only, not from evaluation runs or operator scripts.
_VISITOR_ONLY = (
    " and exists (select 1 from chat.conversations c where c.id = a.conversation_id"
    " and c.origin = 'visitor')"
)


def signals(
    database_url: str, tenant: str, since: str | None, outcomes: list[str], limit: int
) -> list[dict]:
    """Assistant turns with the given outcomes (implicit signals for Module A, PRD 8.1), each with
    the visitor's question and the sources that were cited."""
    with tx(database_url, tenant) as cur:
        cur.execute(
            _TURNS_SQL
            + _VISITOR_ONLY
            + " and a.outcome = any(%s) and (%s::timestamptz is null or a.created_at > %s::timestamptz)"
            " order by a.created_at limit %s",
            (outcomes, since, since, limit),
        )
        return cur.fetchall()


def message(database_url: str, tenant: str, message_id: str) -> dict | None:
    """One assistant message with its question and citations (for feedback attribution)."""
    with tx(database_url, tenant) as cur:
        cur.execute(_TURNS_SQL + " and a.id::text = %s", (message_id,))
        return cur.fetchone()


def messages(database_url: str, tenant: str, message_ids: list[str]) -> list[dict]:
    """Several answers with their questions at once (the console's review queue)."""
    if not message_ids:
        return []
    with tx(database_url, tenant) as cur:
        cur.execute(_TURNS_SQL + " and a.id::text = any(%s)", (message_ids,))
        return cur.fetchall()


def last_answer(
    database_url: str, tenant: str, conversation_id: str, session_id: str
) -> dict | None:
    """The newest answer in a visitor's own conversation, so the widget can show an answer whose
    stream was cut off (PRD F1: reconnect and resume). Another session's conversation is None."""
    with tx(database_url, tenant) as cur:
        cur.execute(
            _TURNS_SQL
            + " and a.conversation_id = %s and exists (select 1 from chat.conversations c"
            " where c.id = a.conversation_id and c.session_id::text = %s)"
            " order by a.created_at desc limit 1",
            (conversation_id, session_id),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                "select n, source_type, ref_id, title, academic_year, heading_path, url"
                " from chat.message_citations where message_id = %s order by n",
                (row["id"],),
            )
            row["sources"] = cur.fetchall()
        return row


def delete_sandbox(database_url: str, tenant: str) -> dict | None:
    """Remove everything of a sandbox tenant (an evaluation copy). Refuses unless the tenant's
    settings say sandbox: true, so a real college can never be deleted this way."""
    with tx(database_url, tenant) as cur:
        cur.execute("select config from chat.tenant_settings")
        row = cur.fetchone()
        if not row or not row["config"].get("sandbox"):
            return None
        counts = {}
        for table in (
            "chat.message_citations",
            "chat.guardian_codes",
            "chat.grounding_reviews",
            "chat.traces",
            "chat.messages",
            "chat.conversations",
            "chat.tenant_settings",
        ):
            cur.execute(f"delete from {table}")
            counts[table] = cur.rowcount
        return counts


def register_channel(
    database_url: str, tenant: str, channel: str, external_id: str
) -> None:
    with tx(database_url, tenant) as cur:
        cur.execute(
            "insert into chat.channels (tenant_id, channel, external_id) values (%s,%s,%s)"
            " on conflict (channel, external_id) do update set tenant_id = excluded.tenant_id",
            (tenant, channel, external_id),
        )


def update_state(
    database_url: str, tenant: str, conversation_id: str, patch: dict
) -> None:
    with tx(database_url, tenant) as cur:
        cur.execute(
            "update chat.conversations set state = state || %s where id = %s",
            (Jsonb(patch), conversation_id),
        )
