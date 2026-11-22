"""WhatsApp channel (PRD F1.11, F1.12, F3.4 on WhatsApp, F8.1 reactions) with fake Module A, C and
model gateway. The number must never be stored by Module B."""

import hashlib
import hmac
import json

import psycopg

from assistant_api import whatsapp
from conftest import TEST_ADMIN_URL, FakeKnowledge, FakeLLM, needs_db

pytestmark = needs_db
SECRET = "wa-app-secret"
NUMBER = "919845012345"
PNID = "pnid-demo"


class FakeExperience:
    def __init__(self):
        self.calls: list[tuple] = []

    def open_session(self, tenant, channel):
        self.calls.append(("session", channel))
        return "11111111-1111-1111-1111-111111111111"

    def consent(self, tenant, session, purpose, text_version, channel):
        self.calls.append(("consent", purpose))

    def lead(self, tenant, session, body):
        self.calls.append(("lead", body))
        return "22222222-2222-2222-2222-222222222222"

    def handoff(self, tenant, session, body):
        self.calls.append(("handoff", body))
        return {"id": "h1", "sla_due_at": "2026-10-28T10:00:00+00:00"}

    def feedback(self, tenant, session, body):
        self.calls.append(("feedback", body))

    def erase(self, tenant, session):
        self.calls.append(("erase", session))
        return self.on_erase(tenant, session) if hasattr(self, "on_erase") else {}

    def withdraw(self, tenant, session, purposes):
        self.calls.append(("withdraw", tuple(purposes)))
        return {"withdrawn": len(purposes), "leads_anonymised": 0}

    def kinds(self):
        return [c[0] if c[0] != "consent" else f"consent:{c[1]}" for c in self.calls]


def payload(*messages):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": PNID},
                            "messages": list(messages),
                        }
                    }
                ]
            }
        ],
    }


def text(i, body):
    return {"id": f"wamid.{i}", "from": NUMBER, "type": "text", "text": {"body": body}}


def button(i, bid, title="x"):
    return {
        "id": f"wamid.{i}",
        "from": NUMBER,
        "type": "interactive",
        "interactive": {
            "type": "button_reply",
            "button_reply": {"id": bid, "title": title},
        },
    }


def post(client, body, secret=SECRET):
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/channels/whatsapp/webhook",
        content=raw,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )


def outbox(tenant):
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        return [
            r[0]
            for r in conn.execute(
                "select payload from chat.wa_outbox where tenant_id = %s order by id",
                (tenant,),
            )
        ]


def wa_client(settings, tenant, llm):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from assistant_api import store
    from assistant_api.engine import Deps
    from assistant_api.guard import RateLimiter
    from assistant_api.main import create_app

    s = replace(
        settings,
        whatsapp_app_secret=SECRET,
        whatsapp_verify_token="vt",
        contact_salt="salt",
    )
    store.register_channel(s.database_url, tenant, "whatsapp", PNID)
    exp = FakeExperience()
    app = create_app(s, Deps(s, FakeKnowledge(), llm, RateLimiter(100, 60)), exp)
    return TestClient(app), exp


def test_signature_and_subscription_check(settings, tenant):
    client, _ = wa_client(settings, tenant, FakeLLM())
    assert post(client, payload(text(1, "hi")), secret="wrong").status_code == 401
    assert client.post("/channels/whatsapp/webhook", content=b"{}").status_code == 401
    ok = client.get(
        "/channels/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "vt",
            "hub.challenge": "42",
        },
    )
    assert ok.status_code == 200 and ok.text == "42"
    bad = client.get(
        "/channels/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "no",
            "hub.challenge": "42",
        },
    )
    assert bad.status_code == 403


def test_question_is_answered_in_plain_text_with_sources_and_buttons(settings, tenant):
    client, exp = wa_client(
        settings,
        tenant,
        FakeLLM("**Yes.** Hostels are separate for men and women [1]."),
    )
    r = post(client, payload(text(1, "Is there a hostel?")))
    assert r.json() == {"accepted": 1}
    [msg] = outbox(tenant)
    assert msg["type"] == "interactive"
    assert msg["text"].startswith("Yes. Hostels are separate for men and women [1].")
    assert (
        "**" not in msg["text"] and "Sources:\n[1] Hostel rules 2027-28" in msg["text"]
    )
    assert [b["id"] for b in msg["buttons"]] == ["fb:up", "fb:down", "human"]
    assert all(len(b["title"]) <= 20 for b in msg["buttons"])
    assert exp.kinds() == [
        "session",
        "consent:whatsapp",
    ]  # opt-in recorded on the first message


def test_meta_retries_are_processed_once(settings, tenant):
    client, _ = wa_client(
        settings,
        tenant,
        FakeLLM("Hostels are separate for men and women [1].", "again"),
    )
    post(client, payload(text(1, "Is there a hostel?")))
    post(client, payload(text(1, "Is there a hostel?")))
    assert len(outbox(tenant)) == 1


def test_talk_to_a_person_asks_consent_then_creates_lead_and_callback(settings, tenant):
    client, exp = wa_client(settings, tenant, FakeLLM("NO_ANSWER"))
    post(client, payload(text(1, "Is there a swimming pool?")))
    post(client, payload(button(2, "human", "Talk to a person")))
    assert "Reply YES or NO" in outbox(tenant)[-1]["text"]
    post(client, payload(text(3, "yes please")))
    kinds = exp.kinds()
    assert kinds[-3:] == ["consent:contact", "lead", "handoff"]
    lead = next(c[1] for c in exp.calls if c[0] == "lead")
    handoff = next(c[1] for c in exp.calls if c[0] == "handoff")
    assert lead["phone"] == "+" + NUMBER
    assert (
        handoff["queue"] == "admission" and handoff["preferred_channel"] == "whatsapp"
    )
    assert handoff["context"]["transcript"][0]["text"] == "Is there a swimming pool?"
    assert "will contact you on this number" in outbox(tenant)[-1]["text"]
    # Module B itself never stored the number
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        for table in (
            "chat.conversations",
            "chat.messages",
            "chat.wa_messages",
            "chat.wa_outbox",
            "chat.traces",
        ):
            assert not conn.execute(
                f"select 1 from {table} t where t::text like %s", (f"%{NUMBER[2:]}%",)
            ).fetchone()


def test_declining_contact_creates_nothing(settings, tenant):
    client, exp = wa_client(settings, tenant, FakeLLM("NO_ANSWER"))
    post(client, payload(button(1, "human")))
    post(client, payload(text(2, "No")))
    assert "lead" not in exp.kinds() and "No problem" in outbox(tenant)[-1]["text"]


def test_minor_is_never_asked_for_their_own_contact(settings, tenant):
    client, exp = wa_client(
        settings, tenant, FakeLLM("Hostels are separate for men and women [1].")
    )
    post(client, payload(text(1, "I am 16 and in class 11. Is there a hostel?")))
    post(client, payload(button(2, "human")))
    assert "parent or guardian" in outbox(tenant)[-1]["text"]
    post(client, payload(text(3, "yes")))
    assert "lead" not in exp.kinds() and "handoff" not in exp.kinds()


def test_reactions_and_buttons_become_feedback(settings, tenant):
    client, exp = wa_client(
        settings, tenant, FakeLLM("Hostels are separate for men and women [1].")
    )
    post(client, payload(text(1, "Is there a hostel?")))
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        out_id, message_id = conn.execute(
            "select wa_message_id, message_id::text from chat.wa_messages where direction = 'out'"
        ).fetchone()
    post(
        client,
        payload(
            {
                "id": "wamid.2",
                "from": NUMBER,
                "type": "reaction",
                "reaction": {"message_id": out_id, "emoji": "👎"},
            }
        ),
    )
    fb = [c[1] for c in exp.calls if c[0] == "feedback"]
    assert (
        fb[-1]["value"] == -1
        and fb[-1]["message_id"] == message_id
        and fb[-1]["channel"] == "whatsapp"
    )
    post(client, payload(button(3, "fb:up")))
    assert [c[1]["value"] for c in exp.calls if c[0] == "feedback"] == [-1, 1]


def test_unknown_business_number_is_ignored(settings, tenant):
    client, _ = wa_client(settings, tenant, FakeLLM())
    body = payload(text(1, "hi"))
    body["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"] = (
        "someone-else"
    )
    assert post(client, body).json() == {"accepted": 0}


def test_formatting_rules():
    long = " ".join(
        f"Sentence {i} about the admission process is here." for i in range(200)
    )
    parts = whatsapp.split_messages(long)
    assert (
        len(parts) == 3
        and all(len(p) <= whatsapp.MAX_CHARS for p in parts)
        and parts[-1].endswith("…")
    )
    assert (
        whatsapp.strip_markdown("## Fees\n| a | b |\n**Rs 250000**")
        == "Fees\n\nRs 250000"
    )
    sources = [{"n": 1, "title": "Prospectus", "url": "https://college.example/p"}]
    # The whole message: the sources stay on their own lines after a blank line, also when the
    # answer ends "[1]." (they were once joined to it with a space).
    assert whatsapp.format_answer("Yes [1].", sources, "hi") == [
        "Yes [1].\n\nस्रोत:\n[1] Prospectus https://college.example/p"
    ]
    assert whatsapp.format_answer(
        "Fee is 250000 INR [1]. Apply by June.", sources, "en"
    ) == [
        "Fee is 250000 INR [1]. Apply by June.\n\nSources:\n[1] Prospectus https://college.example/p"
    ]


def test_delete_my_data_on_whatsapp_goes_through_module_c(settings, tenant):
    from assistant_api import privacy

    client, exp = wa_client(
        settings, tenant, FakeLLM("Hostels are separate for men and women [1].")
    )
    # Module C coordinates the erasure and calls back into Module B for the transcripts.
    exp.on_erase = lambda t, sid: privacy.erase_session(settings.database_url, t, sid)
    post(client, payload(text(1, "I am Priya. Is there a hostel?")))
    post(client, payload(text(2, "Delete my data")))
    assert ("erase", "11111111-1111-1111-1111-111111111111") in exp.calls
    assert "being deleted" in outbox(tenant)[-1]["text"]
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        texts = [
            r[0]
            for r in conn.execute(
                "select content from chat.messages where tenant_id = %s", (tenant,)
            )
        ]
        conv = conn.execute(
            "select state, wa_contact_hash, erased_at is not null from chat.conversations"
            " where tenant_id = %s",
            (tenant,),
        ).fetchone()
    assert texts and all(t == "[erased]" for t in texts)
    assert conv == ({}, None, True)
    # Writing again starts afresh, with a new opt-in.
    post(client, payload(text(3, "Is there a hostel?")))
    assert exp.kinds().count("session") == 2
    # ...and that new content can be erased in turn.
    post(client, payload(text(4, "Delete my data")))
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        left = conn.execute(
            "select count(*) from chat.messages where tenant_id = %s and content <> '[erased]'",
            (tenant,),
        ).fetchone()[0]
    assert left == 0


def test_a_session_module_c_no_longer_knows_is_replaced_not_stuck(settings, tenant):
    from assistant_api.experience_client import ExperienceError

    client, exp = wa_client(settings, tenant, FakeLLM("NO_ANSWER"))
    post(client, payload(button(1, "human")))
    real_consent, failed = exp.consent, []

    def consent(t, session, purpose, version, channel):
        if purpose == "contact" and not failed:  # C was reset: the old session is gone
            failed.append(session)
            raise ExperienceError("Unknown visitor session")
        real_consent(t, session, purpose, version, channel)

    exp.consent = consent
    post(client, payload(text(2, "Yes")))
    assert failed and "lead" in exp.kinds() and "handoff" in exp.kinds()
    assert (
        exp.kinds().count("session") == 2
    )  # a fresh session, with the opt-in recorded again
    assert "will contact you" in outbox(tenant)[-1]["text"]
