"""Erasure on request and transcript retention (PRD 11.4)."""

import uuid

import psycopg

from assistant_api import privacy, store
from conftest import TEST_URL, FakeLLM, needs_db
from test_engine_db import ask

pytestmark = needs_db

ANSWER = "The B.Tech CSE tuition fee is 250000 INR per year [1]."


def _sql(tenant, query, args=()):
    with psycopg.connect(TEST_URL) as conn:
        conn.execute("select set_config('app.tenant_id', %s, false)", (tenant,))
        return conn.execute(query, args).fetchall()


def test_erasing_a_session_blanks_its_conversations_and_keeps_anonymous_turns(
    make_client, tenant
):
    client, _ = make_client(FakeLLM(ANSWER, ANSWER))
    mine, other = str(uuid.uuid4()), str(uuid.uuid4())
    ask(
        client, tenant, "Fee for CSE? I am Priya, priya@example.com", "me", session=mine
    )
    ask(client, tenant, "Fee for CSE?", "them", session=other)

    r = client.post(
        "/privacy/erase",
        json={"session_id": mine},
        headers={"X-Service-Key": "key-from-c", "X-Tenant-Id": tenant},
    )
    assert r.status_code == 200 and r.json() == {"conversations": ["me"], "messages": 2}

    rows = _sql(
        tenant,
        "select conversation_id, content, outcome, latency_ms is not null"
        " from chat.messages order by conversation_id, created_at",
    )
    assert [(c, t) for c, t, *_ in rows if c == "me"] == [
        ("me", "[erased]"),
        ("me", "[erased]"),
    ]
    # Anonymous analytics stay: the turn, its outcome and timings.
    assert [o for c, _, o, _ in rows if c == "me"] == [None, "answered"]
    assert all(t != "[erased]" for c, t, *_ in rows if c == "them")
    assert _sql(
        tenant,
        "select count(*) from chat.traces t join chat.messages m on m.id = t.message_id"
        " where m.conversation_id = 'me'",
    ) == [(0,)]
    assert _sql(
        tenant,
        "select count(*) from chat.message_citations c join chat.messages m on m.id = c.message_id"
        " where m.conversation_id = 'me' and c.content is not null",
    ) == [(0,)]
    # Module A no longer receives the erased turns as signals.
    assert all(
        t["conversation_id"] != "me"
        for t in store.signals(TEST_URL, tenant, None, ["answered"], 50)
    )
    # Idempotent: a retried request erases nothing more.
    again = client.post(
        "/privacy/erase",
        json={"session_id": mine},
        headers={"X-Service-Key": "key-from-c", "X-Tenant-Id": tenant},
    )
    assert again.json() == {"conversations": [], "messages": 0}


def test_whatsapp_conversation_is_found_by_the_session_in_its_state(settings, tenant):
    sid = str(uuid.uuid4())
    store.open_conversation(settings.database_url, tenant, "wa-abc", None, "whatsapp")
    with psycopg.connect(TEST_URL) as conn:
        conn.execute("select set_config('app.tenant_id', %s, false)", (tenant,))
        conn.execute(
            "update chat.conversations set state = jsonb_build_object('c_session', %s::text),"
            " wa_contact_hash = 'h1'",
            (sid,),
        )
        conn.execute(
            "insert into chat.wa_outbox (tenant_id, contact_hash, payload) values (%s, 'h1', '{}')",
            (tenant,),
        )
    out = privacy.erase_session(settings.database_url, tenant, sid)
    assert out["conversations"] == ["wa-abc"]
    assert _sql(tenant, "select state, wa_contact_hash from chat.conversations") == [
        ({}, None)
    ]
    assert _sql(tenant, "select count(*) from chat.wa_outbox") == [(0,)]


def test_only_module_c_may_request_erasure(make_client, tenant):
    client, _ = make_client()
    body = {"session_id": str(uuid.uuid4())}
    from_a = client.post(
        "/privacy/erase",
        json=body,
        headers={"X-Service-Key": "key-from-a", "X-Tenant-Id": tenant},
    )
    assert from_a.status_code == 403


def test_retention_erases_only_conversations_past_the_tenants_limit(
    make_client, settings, tenant
):
    client, _ = make_client(FakeLLM(ANSWER, ANSWER))
    ask(client, tenant, "Fee for CSE?", "old")
    ask(client, tenant, "Fee for CSE?", "new")
    _sql(
        tenant,
        "update chat.conversations set last_message_at = now() - interval '40 days'"
        " where id = 'old' returning id",
    )
    store.save_tenant_config(
        settings.database_url, tenant, {"retention": {"transcript_days": 30}}
    )
    out = privacy.retention_sweep(settings.database_url, tenant)
    assert out["transcript_days"] == 30 and out["conversations"] == ["old"]
    assert privacy.retention_sweep(settings.database_url, tenant)["conversations"] == []
