"""Retell AI voice layer (no network: Retell's API is faked)."""

import json
import time

import httpx
import pytest

from assistant_api import retell


def test_web_call_is_created_with_the_agent_and_key():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers["Authorization"]
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        ice = [{"urls": ["stun:x"]}]
        return httpx.Response(
            201,
            json={
                "access_token": "tok",
                "call_id": "call_1",
                "transport": "gateway",
                "ice_servers": ice,
                "expires_at": 1,
            },
        )

    out = retell.create_web_call(
        "key_x",
        "agent_y",
        {"tid": "t"},
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert out == {
        "access_token": "tok",
        "call_id": "call_1",
        "transport": "gateway",
        "ice_servers": [{"urls": ["stun:x"]}],
    }
    assert seen["path"] == "/v3/create-web-call"  # v2 was retired by Retell
    assert seen["auth"] == "Bearer key_x" and seen["body"]["agent_id"] == "agent_y"


def test_a_refused_call_is_an_error_not_a_crash():
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(401, text="bad key"))
    )
    with pytest.raises(retell.RetellError):
        retell.create_web_call("k", "a", {}, client)


def test_only_calls_created_here_are_known():
    retell.remember("c-known", retell.RetellCall("t", "conv", "s", "en", time.time()))
    assert retell.lookup("c-known").conversation_id == "conv"
    assert retell.lookup("c-other") is None
    retell.remember(
        "c-old", retell.RetellCall("t", "conv", "s", "en", time.time() - 3 * 3600)
    )
    assert retell.lookup("c-old") is None


def test_citation_markers_are_not_spoken():
    assert (
        retell.speakable("The fee is Rs. 2000 per program. [1]")
        == "The fee is Rs. 2000 per program."
    )
    assert retell.speakable("Hostels exist [1, 2] for all.") == "Hostels exist for all."
    assert retell.speakable("  ") == ""


def test_the_latest_user_utterance_is_answered():
    transcript = [
        {"role": "agent", "content": "Hello"},
        {"role": "user", "content": "What is the fee?"},
        {"role": "agent", "content": "It is..."},
        {"role": "user", "content": " Is there a hostel? "},
    ]
    assert retell.last_user_text(transcript) == "Is there a hostel?"
    assert retell.last_user_text([]) == ""


def test_a_question_split_by_retell_is_joined_again():
    # seen in a real call: Retell sent one question as two user turns
    transcript = [
        {"role": "agent", "content": "Hello!"},
        {"role": "user", "content": "What is the application fee"},
        {"role": "user", "content": "for the course?"},
    ]
    assert (
        retell.last_user_text(transcript)
        == "What is the application fee for the course?"
    )


def test_an_unknown_call_gets_no_answers():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from assistant_api.routes import retell as routes

    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app).websocket_connect("/voice/retell/llm/not-ours") as ws:
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
