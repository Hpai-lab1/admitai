"""Vapi phone calls (vapi.py, routes/vapi.py): placing a call, answering inbound calls, and every
turn answered by the engine as OpenAI streaming chunks. No network: Vapi's API is faked."""

import asyncio
import json
from dataclasses import replace

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from assistant_api import store, vapi
from assistant_api.prompts import message
from conftest import TEST_ADMIN_URL, FakeKnowledge, FakeLLM, needs_db, visitor_token

SECRET = "vapi-secret-" + "s" * 24
PUBLIC = "https://b.example.test"
# A sample mobile number, built so no phone-number-like text is in the source (guide-gate).
NUMBER = "+91" + "".join(str(digit) for digit in range(9, -1, -1))
TUITION = "The B.Tech CSE tuition fee is ₹2,50,000 per year [1]."


# ------------------------------------------------------------------ Vapi's API (no database)


def fake_api(handler, **kw):
    return vapi.VapiClient(
        "vk-private",
        "https://vapi.test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
        **kw,
    )


def test_a_call_is_placed_with_the_assistant_the_number_and_the_overrides():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(
            path=req.url.path,
            auth=req.headers["Authorization"],
            body=json.loads(req.content),
        )
        return httpx.Response(201, json={"id": "call-9", "status": "queued"})

    out = fake_api(handler).create_call(
        "asst-1", "pn-1", NUMBER, {"metadata": {"tid": "t"}}
    )
    assert out == {"id": "call-9", "status": "queued"}
    assert seen["path"] == "/call" and seen["auth"] == "Bearer vk-private"
    assert seen["body"] == {
        "assistantId": "asst-1",
        "phoneNumberId": "pn-1",
        "customer": {"number": NUMBER},
        "assistantOverrides": {"metadata": {"tid": "t"}},
    }


def test_a_number_that_is_not_international_is_refused_before_calling_vapi():
    calls = []
    api = fake_api(lambda r: calls.append(1) or httpx.Response(201, json={}))
    for bad in (NUMBER[3:], f"+91 {NUMBER[3:8]} {NUMBER[8:]}", "", "+0123456789"):
        with pytest.raises(vapi.VapiError):
            api.create_call("a", "p", bad)
    assert calls == []


def test_busy_vapi_is_retried_and_refusals_are_reported_without_the_key():
    tries = []

    def busy_then_ok(req):
        tries.append(1)
        return (
            httpx.Response(503)
            if len(tries) == 1
            else httpx.Response(201, json={"id": "c"})
        )

    assert fake_api(busy_then_ok).create_call("a", "p", NUMBER)["id"] == "c"
    assert len(tries) == 2
    refused = fake_api(
        lambda r: httpx.Response(
            400, json={"message": ["phoneNumberId must be a UUID"]}
        )
    )
    with pytest.raises(vapi.VapiError) as err:
        refused.create_call("a", "p", NUMBER)
    assert err.value.status == 400 and "phoneNumberId" in str(err.value)
    assert "vk-private" not in str(err.value)


def test_the_overrides_swap_in_this_engine_and_leave_everything_else_saved():
    s = type("S", (), {"vapi_answers": "engine", "vapi_server_secret": SECRET})()
    o = vapi.overrides(s, "t1", "vapi-c1", PUBLIC)
    assert (
        o["model"]["provider"] == "custom-llm"
        and o["model"]["url"] == f"{PUBLIC}/voice/vapi"
    )
    assert o["metadata"] == {"tid": "t1", "cid": "vapi-c1"}
    assert o["server"] == {
        "url": f"{PUBLIC}/voice/vapi/webhook",
        "headers": {"X-Vapi-Secret": SECRET},
    }
    assert set(o) == {
        "model",
        "metadata",
        "server",
        "serverMessages",
    }  # voice, greeting: saved
    s.vapi_answers = "assistant"  # the saved assistant's own model answers
    assert "model" not in vapi.overrides(s, "t1", "vapi-c1", PUBLIC)


def test_the_callers_latest_words_are_answered():
    msgs = [
        {"role": "system", "content": "x"},
        {"role": "assistant", "content": "Thank you for calling. How may I help you?"},
        {"role": "user", "content": "What is the fee"},
        {"role": "user", "content": [{"type": "text", "text": "for B.Tech CSE?"}]},
    ]
    assert vapi.last_user_text(msgs) == "What is the fee for B.Tech CSE?"
    assert vapi.last_user_text([]) == ""


# ------------------------------------------------------------------ through the API (database)


class FakeVapi:
    def __init__(self, fail=None):
        self.fail, self.calls = fail, []

    def create_call(
        self, assistant_id, phone_number_id, number, overrides=None, name=None
    ):
        self.calls.append((assistant_id, phone_number_id, number, overrides))
        if self.fail:
            raise self.fail
        return {"id": f"call-out-{len(self.calls)}", "status": "queued"}


class SlowLLM(FakeLLM):
    async def stream(self, model, messages, max_tokens, timeout):
        await asyncio.sleep(0.3)
        async for piece in super().stream(model, messages, max_tokens, timeout):
            yield piece


def vclient(settings, tenant, llm=None, api=None, voice_external=("vapi",), **s_kw):
    from assistant_api.engine import Deps
    from assistant_api.guard import RateLimiter
    from assistant_api.main import create_app

    configured = {
        "vapi_api_key": "vk-private",
        "vapi_assistant_id": "asst-ruas",
        "vapi_phone_number_id": "pn-out",
        "vapi_public_url": PUBLIC,
        "vapi_server_secret": SECRET,
    }
    s = replace(settings, **{**configured, **s_kw})
    app = create_app(
        s, Deps(s, FakeKnowledge(), llm or FakeLLM(TUITION), RateLimiter(100, 60))
    )
    app.state.vapi = api if api is not None else FakeVapi()
    client = TestClient(app)
    # The college opted in to the telephone (model_policy.voice_external), as RUAS's pack does.
    r = client.post(
        "/tenant-pack",
        json={
            "college": {"name": "Ramaiah University of Applied Sciences"},
            "model_policy": {"voice_external": list(voice_external)},
        },
        headers={"X-Service-Key": "key-from-c", "X-Tenant-Id": tenant},
    )
    assert r.status_code == 200, r.text
    return client, app


def turn(client, words, call_id, stream=True, headers=None, **call):
    return client.post(
        "/voice/vapi/chat/completions",
        json={
            "model": "admitai-engine",
            "stream": stream,
            "call": {"id": call_id, **call},
            "messages": [
                {"role": "system", "content": "ignored"},
                {"role": "assistant", "content": "Thank you for calling Ruas."},
                {"role": "user", "content": words},
            ],
        },
        headers=headers or {},
    )


def said(response) -> tuple[str, list[dict]]:
    lines = [ln[6:] for ln in response.text.split("\n\n") if ln.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    chunks = [json.loads(x) for x in lines[:-1]]
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    return text.strip(), chunks


@needs_db
def test_a_call_turn_streams_the_engines_checked_answer_in_english(settings, tenant):
    client, _ = vclient(settings, tenant)
    vapi.remember(
        "call-1",
        vapi.VapiCall(tenant, "vapi-call-1", __import__("time").time(), "outbound"),
    )
    r = turn(client, "What is the tuition fee for B.Tech CSE?", "call-1")
    assert r.status_code == 200 and r.headers["content-type"].startswith(
        "text/event-stream"
    )
    text, chunks = said(r)
    assert (
        text == "The B.Tech CSE tuition fee is two lakh fifty thousand rupees per year."
    )  # "[1]" is not spoken
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        channel = conn.execute(
            "select channel from chat.conversations where id = 'vapi-call-1'"
        ).fetchone()[0]
    assert channel == "phone"


@needs_db
def test_a_slow_answer_starts_with_one_moment(settings, tenant, monkeypatch):
    from assistant_api.routes import vapi as routes

    monkeypatch.setattr(routes, "HOLD_AFTER_S", 0.05)
    client, _ = vclient(settings, tenant, llm=SlowLLM(TUITION))
    vapi.remember(
        "call-slow",
        vapi.VapiCall(tenant, "vapi-slow", __import__("time").time(), "inbound"),
    )
    text, _ = said(turn(client, "What is the tuition fee for B.Tech CSE?", "call-slow"))
    assert text.startswith(routes.HOLD_ON) and "two lakh fifty thousand rupees" in text
    # Long enough for Vapi's voice to speak it at once (chunks of about 30 characters or more;
    # the 30-character version was never spoken on a live call).
    assert len(routes.HOLD_ON) >= 40 and len(routes.STILL_CHECKING) >= 40


@needs_db
def test_a_very_slow_answer_also_says_it_is_still_checking(
    settings, tenant, monkeypatch
):
    from assistant_api.routes import vapi as routes

    monkeypatch.setattr(routes, "HOLD_AFTER_S", 0.05)
    monkeypatch.setattr(routes, "STILL_AFTER_S", 0.1)
    client, _ = vclient(settings, tenant, llm=SlowLLM(TUITION))
    vapi.remember(
        "call-vslow",
        vapi.VapiCall(tenant, "vapi-vslow", __import__("time").time(), "inbound"),
    )
    text, _ = said(
        turn(client, "What is the tuition fee for B.Tech CSE?", "call-vslow")
    )
    assert text.startswith(f"{routes.HOLD_ON} {routes.STILL_CHECKING}")
    assert text.endswith("two lakh fifty thousand rupees per year.")
    assert text.count(routes.STILL_CHECKING) == 1  # said once, not every few seconds


@needs_db
def test_a_call_this_service_does_not_know_needs_the_secret(settings, tenant):
    client, _ = vclient(settings, tenant, vapi_default_tenant=tenant)
    assert turn(client, "Is there a hostel?", "call-stranger").status_code == 401
    r = turn(
        client,
        "Is there a hostel?",
        "call-stranger2",
        headers={"X-Vapi-Secret": SECRET},
    )
    assert r.status_code == 200
    r = turn(
        client,
        "Is there a hostel?",
        "call-3",
        headers={"Authorization": f"Bearer {SECRET}"},
    )
    assert r.status_code == 200
    assert (
        turn(client, "Hi", "c4", headers={"X-Vapi-Secret": "wrong"}).status_code == 401
    )


@needs_db
def test_the_phone_line_answers_in_english_whatever_the_caller_speaks(settings, tenant):
    llm = FakeLLM("Separate hostels are available for men and women [1].")
    client, _ = vclient(settings, tenant, llm=llm)
    vapi.remember(
        "call-kn",
        vapi.VapiCall(tenant, "vapi-kn", __import__("time").time(), "inbound"),
    )
    text, _ = said(turn(client, "ಹಾಸ್ಟೆಲ್ ಇದೆಯೆ?", "call-kn"))
    assert text == "Separate hostels are available for men and women."
    text, _ = said(turn(client, "Kannada please", "call-kn"))
    assert text == message("language_only", "en")


@needs_db
def test_a_non_streaming_request_gets_one_completion(settings, tenant):
    client, _ = vclient(settings, tenant)
    vapi.remember(
        "call-ns",
        vapi.VapiCall(tenant, "vapi-ns", __import__("time").time(), "inbound"),
    )
    r = turn(client, "What is the tuition fee for B.Tech CSE?", "call-ns", stream=False)
    body = r.json()
    assert body["object"] == "chat.completion"
    assert (
        body["choices"][0]["message"]["content"]
        == "The B.Tech CSE tuition fee is two lakh fifty thousand rupees per year."
    )


@needs_db
def test_an_inbound_call_gets_the_saved_assistant_with_this_engine_answering(
    settings, tenant
):
    client, _ = vclient(settings, tenant)
    store.register_channel(settings.database_url, tenant, "vapi", "pn-in")
    r = client.post(
        "/voice/vapi/webhook",
        json={
            "message": {
                "type": "assistant-request",
                "call": {
                    "id": "call-in-1",
                    "phoneNumberId": "pn-in",
                    "customer": {"number": NUMBER},
                },
            }
        },
        headers={"X-Vapi-Secret": SECRET},
    )
    out = r.json()
    assert out["assistantId"] == "asst-ruas"
    model = out["assistantOverrides"]["model"]
    assert model["provider"] == "custom-llm" and model["url"] == f"{PUBLIC}/voice/vapi"
    assert out["assistantOverrides"]["metadata"]["tid"] == tenant
    # The call is now known: its turns are answered without the secret, for this college.
    text, _ = said(turn(client, "What is the tuition fee for B.Tech CSE?", "call-in-1"))
    assert "two lakh fifty thousand rupees" in text
    # The caller's number is never stored by Module B.
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        for table in ("chat.conversations", "chat.messages", "chat.traces"):
            row = conn.execute(
                f"select count(*) from {table} t where t::text like '%%{NUMBER[3:]}%%'"
            ).fetchone()
            assert row[0] == 0, table


@needs_db
def test_a_number_no_college_uses_is_told_so_and_the_webhook_needs_the_secret(
    settings, tenant
):
    client, _ = vclient(settings, tenant)
    ask = {
        "message": {
            "type": "assistant-request",
            "call": {"id": "c", "phoneNumberId": "pn-x"},
        }
    }
    assert client.post("/voice/vapi/webhook", json=ask).status_code == 401
    r = client.post("/voice/vapi/webhook", json=ask, headers={"X-Vapi-Secret": SECRET})
    assert "not set up" in r.json()["error"]
    status = {
        "message": {"type": "status-update", "status": "ended", "call": {"id": "c"}}
    }
    assert (
        client.post(
            "/voice/vapi/webhook", json=status, headers={"X-Vapi-Secret": SECRET}
        ).json()
        == {}
    )


@needs_db
def test_staff_place_a_call_and_its_turns_are_answered_for_their_college(
    settings, tenant
):
    api = FakeVapi()
    client, _ = vclient(settings, tenant, api=api)
    service = {"X-Service-Key": "key-from-c", "X-Tenant-Id": tenant}
    r = client.post("/voice/vapi/calls", json={"to": NUMBER}, headers=service)
    assert r.status_code == 201, r.text
    out = r.json()
    [(assistant, number_id, number, overrides)] = api.calls
    assert (assistant, number_id, number) == ("asst-ruas", "pn-out", NUMBER)
    assert overrides["model"]["provider"] == "custom-llm"
    assert overrides["metadata"] == {"tid": tenant, "cid": out["conversation_id"]}
    assert vapi.lookup(out["call_id"]).tenant == tenant
    text, _ = said(
        turn(client, "What is the tuition fee for B.Tech CSE?", out["call_id"])
    )
    assert "two lakh fifty thousand rupees" in text


@needs_db
def test_placing_a_call_is_for_services_with_a_valid_number_and_a_configured_vapi(
    settings, tenant
):
    client, _ = vclient(settings, tenant)
    service = {"X-Service-Key": "key-from-c", "X-Tenant-Id": tenant}
    visitor = {"Authorization": f"Bearer {visitor_token(tenant)}"}
    assert (
        client.post(
            "/voice/vapi/calls", json={"to": NUMBER}, headers=visitor
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/voice/vapi/calls", json={"to": "98123"}, headers=service
        ).status_code
        == 422
    )
    unset, _ = vclient(settings, tenant, vapi_phone_number_id="")
    r = unset.post("/voice/vapi/calls", json={"to": NUMBER}, headers=service)
    assert r.status_code == 503 and "VAPI_PHONE_NUMBER_ID" in r.text
    failing, _ = vclient(
        settings,
        tenant,
        api=FakeVapi(fail=vapi.VapiError("Vapi refused (400): x", 400)),
    )
    r = failing.post("/voice/vapi/calls", json={"to": NUMBER}, headers=service)
    assert r.status_code == 502


def test_amounts_and_years_are_spoken_in_words_on_the_phone():
    """Live, 17 Nov 2026: Vapi's voice read "₹6,00,000" as "$6,000" and "2026-27" as "20267"."""
    from assistant_api.routes.vapi import for_phone

    assert for_phone(
        "For B.Tech (2026-27), the total fee is ₹6,00,000 per year [1]."
    ) == ("For B.Tech (2026 to 27), the total fee is six lakh rupees per year.")
    assert for_phone("The application fee is Rs. 1,800 [2].") == (
        "The application fee is one thousand eight hundred rupees."
    )
    assert for_phone("The COMEDK code is E197 [1].") == "The COMEDK code is E197."


def test_a_comma_after_an_amount_stays_after_the_rupees():
    from assistant_api.routes.vapi import for_phone

    assert for_phone(
        "The application fee is Rs. 1,800, and it is non-refundable [1]."
    ) == (
        "The application fee is one thousand eight hundred rupees, and it is non-refundable."
    )


@needs_db
def test_a_college_that_did_not_opt_in_gets_no_phone_calls(settings, tenant):
    """PRD 16: an outside voice service only for a college that agreed (voice_external)."""
    client, app = vclient(settings, tenant, voice_external=())
    r = client.post(
        "/voice/vapi/calls",
        json={"to": "+91" + "9" * 10},
        headers={"X-Service-Key": "key-from-c", "X-Tenant-Id": tenant},
    )
    assert r.status_code == 403 and "not enabled" in r.json()["detail"]
    assert app.state.vapi.calls == []
