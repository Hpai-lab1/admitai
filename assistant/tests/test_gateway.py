"""Model gateway (PRD 9.5, 7.5, F1 error handling): the HTTP client that talks to LiteLLM (lab) or
Ollama (laptop), its timeouts and errors, the engine's one fallback, latency in the trace, and the
Level 1 rule that only local models are ever called.

The gateway here is httpx's in-process MockTransport speaking the OpenAI streaming format that both
LiteLLM and Ollama use. No model runs in these tests."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import yaml
from conftest import needs_db

from assistant_api import policy
from assistant_api.clients import (
    CONNECT_TIMEOUT_S,
    OpenAICompatibleLLM,
    UpstreamError,
    _timeout,
)
from assistant_api.config import Settings, check_production, is_lab_address


def sse(*deltas: str) -> bytes:
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": d}}]}) for d in deltas
    ]
    return ("\n\n".join([*lines, "data: [DONE]"]) + "\n\n").encode()


def gateway(handler) -> OpenAICompatibleLLM:
    s = replace(Settings(), llm_base_url="http://gateway.test/v1", llm_api_key="k")
    return OpenAICompatibleLLM(
        s, httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


async def collect(llm, **kw):
    return [
        c
        async for c in llm.stream(
            "local/qwen2.5-14b-instruct",
            [{"role": "user", "content": "q"}],
            100,
            5,
            **kw,
        )
    ]


def test_streams_deltas_in_order_and_sends_the_openai_request():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=sse("The fee ", "is 2,50,000", " [1]."))

    chunks = asyncio.run(collect(gateway(handler)))
    assert "".join(chunks) == "The fee is 2,50,000 [1]."
    assert seen["url"] == "http://gateway.test/v1/chat/completions"
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["stream"] is True and seen["body"]["temperature"] == 0
    assert seen["body"]["model"] == "local/qwen2.5-14b-instruct"


@pytest.mark.parametrize("status", [429, 500, 503])
def test_gateway_errors_become_upstream_errors_with_the_status(status):
    llm = gateway(lambda r: httpx.Response(status, json={"error": "busy"}))
    with pytest.raises(UpstreamError, match=f"HTTP {status}"):
        asyncio.run(collect(llm))
    with pytest.raises(UpstreamError, match=f"HTTP {status}"):
        asyncio.run(llm.complete("m", [], 4, 5))


def test_a_garbled_stream_is_an_upstream_error_not_a_crash():
    llm = gateway(lambda r: httpx.Response(200, content=b"data: {not json}\n\n"))
    with pytest.raises(UpstreamError, match="unreadable stream"):
        asyncio.run(collect(llm))


def test_a_gateway_that_cannot_be_reached_is_an_upstream_error():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(UpstreamError, match="ConnectError"):
        asyncio.run(collect(gateway(handler)))


def test_connecting_has_its_own_short_timeout():
    """A gateway that is not there must fail in seconds, so the fallback still fits in the
    20-second budget (PRD F1), while a slow answer may take the full read timeout."""
    t = _timeout(20)
    assert t.connect == CONNECT_TIMEOUT_S <= 3 and t.read == 20


# ------------------------------------------------------------------ the engine's fallback


@needs_db
def test_primary_failure_before_any_sentence_falls_back_and_the_trace_shows_both(
    make_client, tenant
):
    from conftest import FakeLLM, visitor_token

    llm = FakeLLM(
        UpstreamError("Model gateway: HTTP 503"), "Hostel gates close at 21:30 [1]."
    )
    client, deps = make_client(llm)
    r = client.post(
        "/chat/c-gw-1/messages",
        json={"text": "When do the hostel gates close?"},
        headers={"Authorization": f"Bearer {visitor_token(tenant)}"},
    )
    events = [b for b in r.text.split("\n\n") if b.strip()]
    done = json.loads(events[-1].split("data: ", 1)[1])
    assert (
        done["outcome"] == "answered" and done["versions"]["model"] == "fallback-model"
    )
    trace = client.get(
        f"/traces/{done['trace_id']}",
        headers={"X-Service-Key": deps.settings.service_key, "X-Tenant-Id": tenant},
    ).json()
    spans = {s["name"]: s for s in trace["spans"]}
    assert (
        spans["answer_error"]["attempt"] == 0
        and "503" in spans["answer_error"]["error"]
    )
    assert (
        spans["answer"]["attempt"] == 1 and spans["answer"]["model"] == "fallback-model"
    )
    assert isinstance(spans["answer"]["ttft_ms"], int)


@needs_db
def test_both_models_failing_offers_a_callback_instead_of_an_error(make_client, tenant):
    from conftest import FakeLLM, visitor_token

    client, _ = make_client(FakeLLM(UpstreamError("a"), UpstreamError("b")))
    r = client.post(
        "/chat/c-gw-2/messages",
        json={"text": "When do the hostel gates close?"},
        headers={"Authorization": f"Bearer {visitor_token(tenant)}"},
    )
    assert "leave your number" in r.text.lower()
    assert '"outcome": "error"' in r.text and '"offer_handoff": true' in r.text


# ------------------------------------------------------------------ Level 1: local models only


@pytest.mark.parametrize(
    "url,ok",
    [
        ("http://127.0.0.1:11434/v1", True),
        ("http://localhost:8300/v1", True),
        ("http://litellm:4000/v1", True),  # a Docker Compose service
        ("http://[fd00::12]:8300/v1", True),  # a private (ULA) address
        ("http://gateway.lab.internal/v1", True),
        ("https://api.openai.com/v1", False),
        ("https://api.anthropic.com/v1", False),
        ("https://openrouter.ai/api/v1", False),
        ("https://8.8.8.8/v1", False),
        ("not a url", False),
    ],
)
def test_only_lab_addresses_count_as_the_gateway(url, ok):
    assert is_lab_address(url) is ok


def test_production_refuses_an_external_model_gateway(monkeypatch):
    for name in ("SERVICE_KEY", "JWT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    good = replace(
        Settings(),
        app_env="production",
        jwt_public_key_file="/keys/visitor.pub",
        voice_token_secret="v" * 40,
        key_from_experience="c",
        key_from_knowledge="a",
        llm_base_url="http://127.0.0.1:8300/v1",
    )
    check_production(good)  # no exception
    with pytest.raises(RuntimeError, match="no external model calls"):
        check_production(replace(good, llm_base_url="https://api.openai.com/v1"))
    with pytest.raises(RuntimeError, match="CONTACT_SALT"):
        check_production(
            replace(good, whatsapp_token="t", whatsapp_app_secret="s", contact_salt="")
        )


def test_the_litellm_config_offers_every_policy_name_and_only_local_models():
    cfg = yaml.safe_load(
        (Path(__file__).parent.parent / "gateway" / "litellm.config.yaml").read_text()
    )
    offered = {m["model_name"]: m["litellm_params"] for m in cfg["model_list"]}
    for name in policy.aliases(Settings()):
        assert (
            name in offered
        ), f"{name} is allowed in a Tenant Pack but not served by the gateway"
    for name, params in offered.items():
        assert params["model"].startswith(
            "ollama"
        ), f"{name} is not a local Ollama model"
        assert "api_key" not in params
    assert (
        cfg["litellm_settings"]["num_retries"] == 0
    )  # Module B does the PRD's one retry itself


def test_token_counts_are_kept_and_the_usage_chunk_is_not_an_error():
    """Asked for usage, Ollama and LiteLLM end the stream with a chunk that has no choices (F9.4).
    Read as an answer chunk it once made every answer fail as "unreadable stream"."""
    from assistant_api.clients import TOKEN_USAGE

    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        usage = {
            "choices": [],
            "usage": {"prompt_tokens": 31, "completion_tokens": 5, "total_tokens": 36},
        }
        body = sse("Hostels ", "exist [1].").replace(
            b"data: [DONE]",
            b"data: " + json.dumps(usage).encode() + b"\n\ndata: [DONE]",
        )
        return httpx.Response(200, content=body)

    async def run():
        tally = {"prompt": 0, "completion": 0}
        TOKEN_USAGE.set(tally)
        return await collect(gateway(handler)), tally

    chunks, tally = asyncio.run(run())
    assert chunks == ["Hostels ", "exist [1]."]
    assert tally == {"prompt": 31, "completion": 5}
    assert sent["stream_options"] == {"include_usage": True}
    # Without a tally (no turn), counting is skipped, never an error.
    assert asyncio.run(collect(gateway(handler))) == ["Hostels ", "exist [1]."]


def test_the_model_is_asked_to_stay_loaded_between_questions():
    """After a pause the first answer waited 8.3 s for the model to load (Ollama unloads it
    after 5 minutes by default)."""
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, content=sse("ok"))

    asyncio.run(collect(gateway(handler)))
    assert sent[0]["keep_alive"] == "30m"
