"""Voice: the LiveKit room token from POST /voice/token (PRD F2.1, 12.1) and the F2.10 switch of
voice answers to the answer model when voice scores more than 3 points below chat.

No LiveKit server runs here: the token is checked against LiveKit's documented token format
(HS256 with the API secret; iss = API key, sub = identity, video grant)."""

import json
import re
from dataclasses import replace

import jwt
import pytest
from fastapi.testclient import TestClient

from assistant_api import store
from assistant_api.engine import Deps
from assistant_api.guard import RateLimiter
from assistant_api.main import create_app
from conftest import FakeKnowledge, FakeLLM, needs_db, visitor_token

pytestmark = needs_db
LK_SECRET = "lk-secret-" + "s" * 30


def client_for(settings, llm=None, **overrides):
    s = replace(settings, **overrides)
    deps = Deps(s, FakeKnowledge(), llm or FakeLLM(), RateLimiter(100, 60))
    return TestClient(create_app(s, deps)), deps


def test_voice_token_includes_a_livekit_room_token_when_livekit_is_set_up(
    settings, tenant
):
    client, _ = client_for(
        settings,
        livekit_url="wss://voice.lab.internal",
        livekit_api_key="APIadmitai",
        livekit_api_secret=LK_SECRET,
    )
    session = "11111111-2222-3333-4444-555555555555"
    r = client.post(
        "/voice/token",
        json={"conversation_id": "c-voice-1"},
        headers={"Authorization": f"Bearer {visitor_token(tenant, session)}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert (
        body["token"] and body["expires_in"] == 600
    )  # push-to-talk token, as the widget uses
    lk = body["livekit"]
    assert lk["url"] == "wss://voice.lab.internal" and lk["room"].startswith("admitai-")
    assert tenant not in lk["room"] and "c-voice-1" not in lk["room"]
    claims = jwt.decode(lk["token"], LK_SECRET, algorithms=["HS256"])
    assert claims["iss"] == "APIadmitai" and claims["sub"] == f"visitor-{session}"
    assert claims["exp"] - claims["nbf"] == 600
    assert claims["video"] == {
        "room": lk["room"],
        "roomJoin": True,
        "canPublish": True,
        "canSubscribe": True,
        "canPublishData": True,
    }
    assert json.loads(claims["metadata"]) == {
        "tid": tenant,
        "cid": "c-voice-1",
        "sid": session,
    }
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(lk["token"], "another-secret-" + "x" * 30, algorithms=["HS256"])


def test_without_livekit_the_token_reply_is_unchanged(settings, tenant):
    client, _ = client_for(settings)
    r = client.post(
        "/voice/token",
        json={"conversation_id": "c-voice-2"},
        headers={"Authorization": f"Bearer {visitor_token(tenant)}"},
    )
    assert set(r.json()) == {"token", "expires_in"}


def test_livekit_settings_go_together_in_production(monkeypatch, settings):
    from assistant_api.config import check_production

    for name in ("SERVICE_KEY", "JWT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    prod = replace(
        settings,
        app_env="production",
        jwt_public_key_file="/keys/visitor.pub",
        voice_token_secret="v" * 40,
        llm_base_url="http://127.0.0.1:8300/v1",
        livekit_api_key="APIadmitai",
        livekit_api_secret="",
    )
    with pytest.raises(
        RuntimeError, match="LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET"
    ):
        check_production(prod)


# ------------------------------------------------------------------ F2.10


def _report(client, deps, tenant, chat, voice, key=None):
    return client.post(
        "/voice/eval-result",
        json={
            "chat_accuracy": chat,
            "voice_accuracy": voice,
            "cases": 25,
            "run_id": "run-1",
        },
        headers={
            "X-Service-Key": key or deps.settings.key_from_knowledge,
            "X-Tenant-Id": tenant,
        },
    )


def _voice_turn(client, deps, tenant, conv):
    return client.post(
        f"/chat/{conv}/messages",
        json={"text": "When do the hostel gates close?", "channel": "web_voice"},
        headers={
            "X-Service-Key": deps.settings.key_from_knowledge,
            "X-Tenant-Id": tenant,
        },
    )


def test_voice_more_than_three_points_worse_moves_to_the_answer_model(settings, tenant):
    llm = FakeLLM(
        "Hostel gates close at 21:30 [1].", "Hostel gates close at 21:30 [1]."
    )
    client, deps = client_for(settings, llm)

    assert _voice_turn(client, deps, tenant, "c-f210-1").status_code == 200
    assert (
        llm.calls[-1]["model"] == "fallback-model"
    )  # voice starts on the fast 8B-class model

    r = _report(client, deps, tenant, 0.90, 0.85)
    assert (
        r.status_code == 200
        and r.json()["use_answer_model"] is True
        and r.json()["gap_points"] == 5.0
    )
    assert _voice_turn(client, deps, tenant, "c-f210-2").status_code == 200
    assert llm.calls[-1]["model"] == "answer-model"
    prompt = llm.calls[-1]["messages"][1]["content"]
    assert (
        1 <= len(re.findall(r"^\[\d+\] \(", prompt, re.M)) <= 3
    )  # shorter context: 3 sources at most

    # Reloading the Tenant Pack's agent settings keeps the decision (it is Module B's, not the pack's).
    client.put(
        "/tenant-settings",
        json={"college": "Demo College"},
        headers={
            "X-Service-Key": deps.settings.key_from_knowledge,
            "X-Tenant-Id": tenant,
        },
    )
    assert (
        store.tenant_config(settings.database_url, tenant)["voice_quality"][
            "use_answer_model"
        ]
        is True
    )


def test_three_points_or_less_keeps_voice_on_the_fast_model(settings, tenant):
    llm = FakeLLM("Hostel gates close at 21:30 [1].")
    client, deps = client_for(settings, llm)
    r = _report(client, deps, tenant, 0.90, 0.87)
    assert r.json()["use_answer_model"] is False
    _voice_turn(client, deps, tenant, "c-f210-3")
    assert llm.calls[-1]["model"] == "fallback-model"


def test_only_module_a_reports_voice_evaluations(settings, tenant):
    client, deps = client_for(settings)
    assert (
        _report(
            client, deps, tenant, 0.9, 0.5, key=deps.settings.key_from_experience
        ).status_code
        == 403
    )
