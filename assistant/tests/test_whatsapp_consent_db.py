"""WhatsApp: opting out (STOP), guardian codes for minors (PRD F5.3), and messages outside the
24-hour window (F1.11 approved templates). Module C and Meta are fakes; the outbox is the local
stand-in for the Cloud API."""

from datetime import datetime, timedelta, timezone

import psycopg

from assistant_api import store
from conftest import TEST_ADMIN_URL, FakeLLM, needs_db
from test_whatsapp_db import NUMBER, outbox, payload, post, text, wa_client

pytestmark = needs_db
GUARDIAN = "15550100042"  # the guardian's WhatsApp number, as Meta sends it (digits)
C_KEY = {"X-Service-Key": "key-from-c"}


def c_headers(tenant):
    return {**C_KEY, "X-Tenant-Id": tenant}


def queue_code(client, tenant, code="482913", minutes=10, number="+" + GUARDIAN):
    return client.post(
        "/channels/whatsapp/guardian-code",
        json={
            "destination": number,
            "code": code,
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=minutes)
            ).isoformat(),
        },
        headers=c_headers(tenant),
    )


def from_guardian(i, body="Hi"):
    m = text(i, body)
    m["from"] = GUARDIAN
    return m


# ------------------------------------------------------------------ STOP


def test_stop_withdraws_the_whatsapp_opt_in_and_writing_again_opts_back_in(
    settings, tenant
):
    client, exp = wa_client(
        settings, tenant, FakeLLM("Hostel gates close at 21:30 [1].")
    )
    post(client, payload(text(1, "STOP")))
    assert ("withdraw", ("whatsapp", "marketing")) in exp.calls
    assert "will not get WhatsApp messages" in outbox(tenant)[-1]["text"]

    refused = client.post(
        "/channels/whatsapp/notify",
        json={"to": "+" + NUMBER, "kind": "staff_reply", "text": "Hello"},
        headers=c_headers(tenant),
    )
    assert refused.status_code == 409 and "asked not to" in refused.json()["detail"]

    before = exp.kinds().count("consent:whatsapp")
    post(client, payload(text(2, "When do the hostel gates close?")))
    assert exp.kinds().count("consent:whatsapp") == before + 1  # a new opt-in
    assert "21:30" in outbox(tenant)[-1]["text"]


# ------------------------------------------------------------------ guardian codes (F5.3)


def test_the_guardian_gets_the_code_only_by_writing_in_from_the_given_number(
    settings, tenant
):
    client, _ = wa_client(settings, tenant, FakeLLM())
    assert queue_code(client, tenant).status_code == 200

    with psycopg.connect(TEST_ADMIN_URL) as conn:
        sealed = conn.execute(
            "select sealed from chat.guardian_codes where tenant_id = %s", (tenant,)
        ).fetchone()[0]
    assert "482913" not in sealed  # stored sealed, not as the code

    post(
        client, payload(text(1, "Hi"))
    )  # someone else (the student's number) writes in
    assert all("482913" not in (m.get("text") or "") for m in outbox(tenant))

    post(client, payload(from_guardian(2)))  # the guardian writes in
    sent = outbox(tenant)[-1]["text"]
    assert "482913" in sent and "Share it with them only if you agree" in sent
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        left = conn.execute(
            "select count(*) from chat.guardian_codes where tenant_id = %s", (tenant,)
        ).fetchone()[0]
    assert left == 0  # delivered once, then gone

    post(client, payload(from_guardian(3, "Hi again")))
    assert "482913" not in (outbox(tenant)[-1].get("text") or "")


def test_an_expired_code_is_not_sent(settings, tenant):
    client, _ = wa_client(settings, tenant, FakeLLM())
    assert queue_code(client, tenant, minutes=10).status_code == 200
    with psycopg.connect(TEST_ADMIN_URL, autocommit=True) as conn:
        conn.execute(
            "update chat.guardian_codes set expires_at = now() - interval '1 minute' where tenant_id = %s",
            (tenant,),
        )
    post(client, payload(from_guardian(1)))
    assert all("482913" not in (m.get("text") or "") for m in outbox(tenant))


def test_a_guardian_who_sends_stop_gets_no_code(settings, tenant):
    client, _ = wa_client(settings, tenant, FakeLLM())
    queue_code(client, tenant)
    post(client, payload(from_guardian(1, "STOP")))
    assert all("482913" not in (m.get("text") or "") for m in outbox(tenant))


def test_guardian_codes_are_only_for_module_c_and_only_where_whatsapp_is_set_up(
    settings, tenant
):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from assistant_api.engine import Deps
    from assistant_api.guard import RateLimiter
    from assistant_api.main import create_app
    from conftest import FakeKnowledge

    client, _ = wa_client(settings, tenant, FakeLLM())
    wrong = client.post(
        "/channels/whatsapp/guardian-code",
        json={
            "destination": "+" + GUARDIAN,
            "code": "123456",
            "expires_at": datetime.now(timezone.utc).isoformat(),
        },
        headers={"X-Service-Key": "key-from-a", "X-Tenant-Id": tenant},
    )
    assert wrong.status_code == 403  # Module A may not
    assert queue_code(client, tenant, number="12345").status_code == 422
    other = str(__import__("uuid").uuid4())  # a college with no WhatsApp number
    assert queue_code(client, other).status_code == 409
    no_salt = replace(settings, contact_salt="")
    bare = TestClient(
        create_app(
            no_salt, Deps(no_salt, FakeKnowledge(), FakeLLM(), RateLimiter(100, 60))
        )
    )
    assert queue_code(bare, tenant).status_code == 503


# ------------------------------------------------------------------ outside the 24-hour window


def _age_conversation(tenant, hours_ago):
    with psycopg.connect(TEST_ADMIN_URL, autocommit=True) as conn:
        conn.execute(
            "update chat.conversations set state = jsonb_set(state, '{last_inbound_at}', to_jsonb(%s::text))"
            " where tenant_id = %s",
            (
                (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(),
                tenant,
            ),
        )


def test_inside_the_window_a_staff_reply_is_plain_text(settings, tenant):
    client, _ = wa_client(settings, tenant, FakeLLM("Hostel gates close at 21:30 [1]."))
    post(client, payload(text(1, "When do the hostel gates close?")))
    r = client.post(
        "/channels/whatsapp/notify",
        json={
            "to": "+" + NUMBER,
            "kind": "staff_reply",
            "text": "**Yes**, hostel rooms are available.",
        },
        headers=c_headers(tenant),
    )
    assert r.status_code == 200 and r.json()["sent"] == "text"
    assert outbox(tenant)[-1] == {
        "type": "text",
        "text": "Yes, hostel rooms are available.",
        "buttons": [],
    }


def test_outside_the_window_only_an_approved_template_is_sent(settings, tenant):
    client, _ = wa_client(settings, tenant, FakeLLM("Hostel gates close at 21:30 [1]."))
    post(client, payload(text(1, "When do the hostel gates close?")))
    _age_conversation(tenant, 30)
    body = {"to": "+" + NUMBER, "kind": "staff_reply", "text": "Rooms are available."}
    refused = client.post(
        "/channels/whatsapp/notify", json=body, headers=c_headers(tenant)
    )
    assert (
        refused.status_code == 409 and "approved template" in refused.json()["detail"]
    )

    cfg = store.tenant_config(settings.database_url, tenant)
    store.save_tenant_config(
        settings.database_url,
        tenant,
        {
            **cfg,
            "college": "Demo College",
            "whatsapp": {
                "templates": {
                    "staff_reply": {"name": "admissions_reply", "language": "en"}
                }
            },
        },
    )
    ok = client.post("/channels/whatsapp/notify", json=body, headers=c_headers(tenant))
    assert ok.status_code == 200 and ok.json()["sent"] == "template"
    assert outbox(tenant)[-1] == {
        "type": "template",
        "template": "admissions_reply",
        "language": "en",
        "params": ["Demo College", "Rooms are available."],
    }


def test_the_cloud_api_template_payload_is_metas_format(monkeypatch):
    from assistant_api import whatsapp

    sent = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"messages": [{"id": "wamid.out"}]}

    def fake_post(url, headers, timeout, json):
        sent.update(url=url, body=json)
        return Response()

    monkeypatch.setattr(whatsapp.httpx, "post", fake_post)
    s = whatsapp.CloudApiSender("https://graph.facebook.com/v21.0", "pnid", "token")
    assert (
        s.send_template(
            "t", "15550100001", "h", "admissions_reply", "en", ["Demo", "Hi"]
        )
        == "wamid.out"
    )
    assert sent["body"] == {
        "messaging_product": "whatsapp",
        "to": "15550100001",
        "type": "template",
        "template": {
            "name": "admissions_reply",
            "language": {"code": "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": "Demo"},
                        {"type": "text", "text": "Hi"},
                    ],
                }
            ],
        },
    }


def test_stop_sent_while_an_answer_is_being_written_is_not_lost(settings, tenant):
    """Messages from one person are handled one at a time, in order: a STOP that arrives while
    the previous answer is still being generated must still stand afterwards."""
    import asyncio
    from dataclasses import replace

    from assistant_api import whatsapp
    from assistant_api.engine import Deps
    from assistant_api.guard import RateLimiter
    from conftest import FakeKnowledge
    from test_whatsapp_db import PNID, FakeExperience

    class SlowLLM(FakeLLM):
        async def stream(self, model, messages, max_tokens, timeout):
            await asyncio.sleep(0.3)  # the model is still writing when STOP arrives
            async for chunk in super().stream(model, messages, max_tokens, timeout):
                yield chunk

    s = replace(settings, contact_salt="salt")
    store.register_channel(s.database_url, tenant, "whatsapp", PNID)
    deps = Deps(
        s,
        FakeKnowledge(),
        SlowLLM("Hostel gates close at 21:30 [1]."),
        RateLimiter(100, 60),
    )
    exp, sender = FakeExperience(), whatsapp.OutboxSender(s.database_url)
    question = whatsapp.parse(payload(text(1, "When do the hostel gates close?")))[0]
    stop = whatsapp.parse(payload(text(2, "STOP")))[0]

    async def both():
        first = asyncio.create_task(
            whatsapp.handle(deps, exp, sender, tenant, question, "salt")
        )
        await asyncio.sleep(0.05)
        second = asyncio.create_task(
            whatsapp.handle(deps, exp, sender, tenant, stop, "salt")
        )
        return await first, await second

    asyncio.run(both())
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        state = conn.execute(
            "select state from chat.conversations where tenant_id = %s", (tenant,)
        ).fetchone()[0]
    assert state.get("opted_out") is True
    texts = [m.get("text") or "" for m in outbox(tenant)]
    assert (
        "21:30" in texts[-2] and "will not get WhatsApp" in texts[-1]
    )  # answered, then STOP


def test_another_worker_handling_the_same_person_is_waited_for(settings, tenant):
    """Several workers: one person's messages still go one at a time, through a PostgreSQL
    advisory lock (a lock held by "another worker" here) and not only the in-process lock."""
    import asyncio
    import time
    from dataclasses import replace

    from assistant_api import whatsapp
    from assistant_api.engine import Deps
    from assistant_api.guard import RateLimiter
    from conftest import FakeKnowledge
    from test_whatsapp_db import NUMBER, PNID, FakeExperience

    s = replace(settings, contact_salt="salt")
    store.register_channel(s.database_url, tenant, "whatsapp", PNID)
    deps = Deps(s, FakeKnowledge(), FakeLLM("unused"), RateLimiter(100, 60))
    exp, sender = FakeExperience(), whatsapp.OutboxSender(s.database_url)
    stop = whatsapp.parse(payload(text(1, "STOP")))[0]
    key = whatsapp.person_lock_key(
        f"{tenant}:{whatsapp.contact_hash(tenant, NUMBER, 'salt')}"
    )

    other = psycopg.connect(TEST_ADMIN_URL, autocommit=True)  # the other worker
    other.execute("select pg_advisory_lock(%s)", (key,))
    released_at = []

    async def run():
        task = asyncio.create_task(
            whatsapp.handle(deps, exp, sender, tenant, stop, "salt")
        )
        await asyncio.sleep(0.4)
        assert not task.done() and outbox(tenant) == []  # waiting for the other worker
        released_at.append(time.monotonic())
        other.execute("select pg_advisory_unlock(%s)", (key,))
        return await task

    try:
        asyncio.run(run())
    finally:
        other.close()
    assert (
        "will not get WhatsApp" in outbox(tenant)[-1]["text"]
    )  # handled once released
    # the lock is free again: another worker can take it at once
    with psycopg.connect(TEST_ADMIN_URL, autocommit=True) as again:
        assert again.execute("select pg_try_advisory_lock(%s)", (key,)).fetchone()[0]


def test_lock_keys_are_stable_and_separate_per_person():
    from assistant_api.whatsapp import person_lock_key

    assert person_lock_key("t:a") == person_lock_key("t:a")
    assert person_lock_key("t:a") != person_lock_key("t:b")
    assert -(2**63) <= person_lock_key("t:a") < 2**63
