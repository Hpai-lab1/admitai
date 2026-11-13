"""Anonymous numbers for Module C's dashboards (PRD F9.1-F9.3, 3.5). The web widget reports its
own conversation events to Module C; WhatsApp does not pass through the widget, so its
conversations would be invisible there. This module supplies them, and answer outcomes and
latency for every channel. Evaluation runs and operator scripts are left out. `turns` sends no
message text; `topics` sends only questions repeated across conversations, masked."""

from __future__ import annotations

from .db import tx
from .guard import mask_pii

# Channels whose conversations Module C does not see through widget events.
OFF_WIDGET = ("whatsapp", "phone")
# One visitor asking twice in two chats is one person, so it does not make a question "trending".
PERSON = "coalesce(c.session_id::text, c.wa_contact_hash, c.id::text)"


def parse_prices(text: str) -> dict[str, tuple[float, float]]:
    """ "model=input:output,..." (INR per 1,000 tokens) -> {model: (input, output)}. Bad entries are
    skipped, so a typo never breaks the dashboard."""
    out = {}
    for part in (text or "").split(","):
        model, _, price = part.strip().rpartition("=")
        try:
            i, o = (float(x) for x in price.split(":"))
        except ValueError:
            continue
        if model and i >= 0 and o >= 0:
            out[model] = (i, o)
    return out


def with_cost(rows: list[dict], prices: dict[str, tuple[float, float]]) -> list[dict]:
    """Adds `cost_inr` to the models that have a price; None for the others."""
    for r in rows:
        p = prices.get(r["model"])
        r["cost_inr"] = (
            round((r["prompt_tokens"] * p[0] + r["completion_tokens"] * p[1]) / 1000, 2)
            if p
            else None
        )
    return rows


def turns(database_url: str, tenant: str, since: str, prices: str = "") -> dict:
    with tx(database_url, tenant) as cur:
        cur.execute(
            "select c.channel, count(*) as answers,"
            " count(*) filter (where m.outcome = 'answered') as answered,"
            " count(*) filter (where m.outcome = 'partial') as partial,"
            " count(*) filter (where m.outcome = 'no_answer') as no_answer,"
            " count(*) filter (where m.outcome = 'handoff') as handoff,"
            " count(*) filter (where m.outcome in ('blocked', 'refused')) as blocked,"
            " count(*) filter (where m.outcome = 'error') as error,"
            " percentile_cont(0.5) within group (order by m.latency_ms) as latency_p50_ms,"
            " percentile_cont(0.95) within group (order by m.latency_ms) as latency_p95_ms"
            " from chat.messages m join chat.conversations c on c.id = m.conversation_id"
            " where m.sender = 'assistant' and c.origin = 'visitor'"
            " and m.created_at >= %s::timestamptz"
            " group by c.channel order by c.channel",
            (since,),
        )
        by_channel = cur.fetchall()
        cur.execute(
            "select c.id as conversation_id, c.channel,"
            " count(*) filter (where m.sender = 'user') as user_msgs,"
            " max(m.created_at) filter (where m.sender = 'user') as last_user_at,"
            " max(m.created_at) filter (where m.sender = 'assistant') as last_answer_at,"
            " (array_agg(m.created_at order by m.created_at desc)"
            "   filter (where m.sender = 'user'))[2] as second_last_user_at,"
            " (array_agg(m.id::text order by m.created_at desc)"
            "   filter (where m.sender = 'assistant'))[1] as final_answer,"
            " bool_or(m.outcome = 'handoff') as asked_human"
            " from chat.conversations c join chat.messages m on m.conversation_id = c.id"
            " where c.channel = any(%s) and c.origin = 'visitor'"
            " and m.created_at >= %s::timestamptz"
            " group by c.id, c.channel",
            (list(OFF_WIDGET), since),
        )
        conversations = cur.fetchall()
        cur.execute(
            "select m.model, count(*) as answers,"
            " coalesce(sum(m.prompt_tokens), 0)::int as prompt_tokens,"
            " coalesce(sum(m.completion_tokens), 0)::int as completion_tokens,"
            " count(m.prompt_tokens)::int as answers_with_tokens"
            " from chat.messages m join chat.conversations c on c.id = m.conversation_id"
            " where m.sender = 'assistant' and c.origin = 'visitor' and m.model is not null"
            " and m.created_at >= %s::timestamptz group by m.model order by answers desc",
            (since,),
        )
        by_model = cur.fetchall()
    for row in by_channel:
        for k in ("latency_p50_ms", "latency_p95_ms"):
            row[k] = round(row[k]) if row[k] is not None else None
    return {
        "by_channel": by_channel,
        "conversations": conversations,
        "by_model": with_cost(by_model, parse_prices(prices)),
    }


def topics(database_url: str, tenant: str, since: str, limit: int = 10) -> dict:
    """Top intents and trending questions (PRD F9.2), each compared with the period of the same
    length before `since`. A question appears only when at least two visitors asked it and more than
    before, with personal details masked: one person's message is never shown."""
    with tx(database_url, tenant) as cur:
        cur.execute(
            "select r->>'intent' as intent,"
            " count(*) filter (where m.created_at >= %(since)s::timestamptz) as current,"
            " count(*) filter (where m.created_at < %(since)s::timestamptz) as previous"
            " from chat.messages m join chat.conversations c on c.id = m.conversation_id"
            " cross join lateral jsonb_array_elements("
            "   case when jsonb_typeof(m.route->'routes') = 'array'"
            "   then m.route->'routes' else '[]'::jsonb end) r"
            " where m.sender = 'assistant' and c.origin = 'visitor'"
            " and m.created_at >= %(since)s::timestamptz"
            "   - (now() - %(since)s::timestamptz)"
            " and r->>'intent' is not null"
            " group by 1 having count(*) filter (where m.created_at >= %(since)s::timestamptz) > 0"
            " order by current desc, intent limit %(limit)s",
            {"since": since, "limit": limit},
        )
        intents = cur.fetchall()
        cur.execute(
            "select min(m.content) as question,"
            f" count(distinct {PERSON})"
            "   filter (where m.created_at >= %(since)s::timestamptz) as current,"
            f" count(distinct {PERSON})"
            "   filter (where m.created_at < %(since)s::timestamptz) as previous"
            " from chat.messages m join chat.conversations c on c.id = m.conversation_id"
            " where m.sender = 'user' and c.origin = 'visitor'"
            " and m.created_at >= %(since)s::timestamptz"
            "   - (now() - %(since)s::timestamptz)"
            # What the guard refused (for example prompt injection) is not a trend to show staff.
            " and coalesce((select a.outcome from chat.messages a"
            "   where a.tenant_id = m.tenant_id and a.conversation_id = m.conversation_id"
            "   and a.sender = 'assistant' and a.created_at >= m.created_at"
            "   order by a.created_at limit 1), '') <> 'refused'"
            " group by lower(trim(regexp_replace(m.content, '[[:space:][:punct:]]+', ' ', 'g')))"
            f" having count(distinct {PERSON})"
            "   filter (where m.created_at >= %(since)s::timestamptz) >= 2"
            f" and count(distinct {PERSON})"
            "   filter (where m.created_at >= %(since)s::timestamptz)"
            f" > count(distinct {PERSON})"
            "   filter (where m.created_at < %(since)s::timestamptz)"
            " order by current desc, question limit %(limit)s",
            {"since": since, "limit": limit},
        )
        trending = [
            {**row, "question": mask_pii(" ".join(row["question"].split()))[:160]}
            for row in cur.fetchall()
        ]
    return {"intents": intents, "trending": trending}
