"""Gemini (Google AI Studio) as an opt-in answer model: only "gemini/..." models go there, with
personal details masked first; everything else stays on the local gateway."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from assistant_api.clients import (
    OpenAICompatibleLLM,
    RoutingLLM,
    UpstreamError,
    make_llm,
)
from assistant_api.config import Settings


class Recorder:
    def __init__(self, reply="ok"):
        self.calls, self.reply = [], reply

    async def complete(self, model, messages, max_tokens, timeout):
        self.calls.append((model, messages))
        return self.reply

    async def stream(self, model, messages, max_tokens, timeout):
        self.calls.append((model, messages))
        yield self.reply


# A sample mobile number, built so no phone-number-like text is in the source (guide-gate).
PHONE = "".join(str(digit) for digit in range(9, -1, -1))
MSGS = [
    {"role": "system", "content": "Answer from the sources."},
    {
        "role": "user",
        "content": f"My number is {PHONE} and mail priya@example.com. What is the fee?",
    },
]


def test_gemini_models_go_to_gemini_with_personal_details_masked():
    local, gemini = Recorder(), Recorder()
    llm = RoutingLLM(local, gemini)
    asyncio.run(llm.complete("gemini/gemini-flash", MSGS, 100, 5))
    assert local.calls == []
    model, sent = gemini.calls[0]
    assert model == "gemini-flash"
    text = sent[1]["content"]
    assert PHONE not in text and "priya@example.com" not in text
    assert "[phone]" in text and "[email]" in text and "What is the fee?" in text
    assert MSGS[1]["content"].startswith(
        f"My number is {PHONE}"
    )  # caller's copy untouched


def test_local_models_stay_local_and_unmasked():
    local, gemini = Recorder(), Recorder()
    llm = RoutingLLM(local, gemini)

    async def drain():
        return [c async for c in llm.stream("qwen2.5:3b", MSGS, 100, 5)]

    assert asyncio.run(drain()) == ["ok"]
    assert gemini.calls == [] and local.calls[0] == ("qwen2.5:3b", MSGS)


def test_without_a_key_a_gemini_model_fails_so_the_local_fallback_answers():
    llm = RoutingLLM(Recorder(), None)
    with pytest.raises(UpstreamError, match="GEMINI_API_KEY"):
        asyncio.run(llm.complete("gemini/gemini-flash", MSGS, 100, 5))


def test_the_gemini_client_calls_google_ai_studio_with_the_key():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(req.url), req.headers["Authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Hi"}}]})

    s = replace(Settings(), gemini_api_key="g-key", model_keep_alive="30m")
    gemini = OpenAICompatibleLLM(
        s,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        base_url=s.gemini_base_url,
        api_key=s.gemini_api_key,
    )
    assert asyncio.run(gemini.complete("gemini-flash", MSGS, 50, 5)) == "Hi"
    assert seen["url"].startswith("https://generativelanguage.googleapis.com/")
    assert seen["auth"] == "Bearer g-key"
    assert (
        "keep_alive" not in seen["body"]
    )  # an Ollama-only setting is not sent to Google
    assert make_llm(replace(Settings(), gemini_api_key="")).gemini is None


def test_gemini_is_asked_not_to_think_so_short_answers_are_not_empty():
    # seen live: with thinking on, a short answer budget was spent thinking and the reply was empty
    llm = make_llm(
        replace(Settings(), gemini_api_key="k", gemini_reasoning_effort="none")
    )
    assert llm.gemini.extra == {"reasoning_effort": "none"}
    assert "reasoning_effort" not in llm.local.extra


class Silent(Recorder):
    """Gemini when Google is overloaded: connected, but nothing comes."""

    async def stream(self, model, messages, max_tokens, timeout):
        await asyncio.sleep(5)
        yield "too late"


def test_a_silent_gemini_fails_fast_so_the_local_model_answers():
    # seen live: overloaded, Gemini said nothing for the full 20 s before the fallback answered
    llm = RoutingLLM(Recorder(), Silent(), first_token_s=0.05)

    async def drain():
        return [c async for c in llm.stream("gemini/gemini-flash", MSGS, 100, 20)]

    with pytest.raises(UpstreamError, match="no answer within"):
        asyncio.run(drain())


class Spiky(Recorder):
    """Gemini whose response time spikes: the listed delays, one per call, in order."""

    def __init__(self, delays, fail=False):
        super().__init__()
        self.delays, self.fail = list(delays), fail

    async def complete(self, model, messages, max_tokens, timeout):
        n = len(self.calls)
        self.calls.append(model)
        await asyncio.sleep(self.delays[n])
        if self.fail:
            raise UpstreamError(f"Model gateway: HTTP 503 (call {n})")
        return f"reply {n}"


def test_a_stuck_gemini_call_is_overtaken_by_a_second_one():
    # seen live: the same Kannada translation took 37 s once and about 4 s otherwise
    gemini = Spiky([5.0, 0.05])
    llm = RoutingLLM(Recorder(), gemini, first_token_s=30, hedge_s=0.1)
    assert asyncio.run(llm.complete("gemini/m", MSGS, 100, 60)) == "reply 1"
    assert len(gemini.calls) == 2


def test_a_quick_gemini_reply_sends_no_second_request():
    gemini = Spiky([0.01])
    llm = RoutingLLM(Recorder(), gemini, first_token_s=30, hedge_s=0.5)
    assert asyncio.run(llm.complete("gemini/m", MSGS, 100, 60)) == "reply 0"
    assert len(gemini.calls) == 1


def test_when_both_requests_fail_the_error_reaches_the_engine():
    gemini = Spiky([0.3, 0.05], fail=True)
    llm = RoutingLLM(Recorder(), gemini, first_token_s=30, hedge_s=0.1)
    with pytest.raises(UpstreamError, match="503"):
        asyncio.run(llm.complete("gemini/m", MSGS, 100, 60))


def test_local_models_are_never_duplicated():
    local = Recorder()
    llm = RoutingLLM(local, Spiky([0.0]), first_token_s=30, hedge_s=0.01)
    asyncio.run(llm.complete("qwen2.5:3b", MSGS, 100, 60))
    assert len(local.calls) == 1


class SpikyStream(Recorder):
    """A Gemini stream whose first word comes after the listed delay, one delay per request."""

    def __init__(self, delays):
        super().__init__()
        self.delays, self.closed = list(delays), []

    async def stream(self, model, messages, max_tokens, timeout):
        n = len(self.calls)
        self.calls.append(model)
        try:
            await asyncio.sleep(self.delays[n])
            yield f"reply {n}."
            yield " more"
        finally:
            self.closed.append(n)


def drain(llm, model="gemini/m"):
    async def run():
        return [c async for c in llm.stream(model, MSGS, 100, 60)]

    return asyncio.run(run())


def test_a_late_first_word_is_overtaken_by_a_second_stream():
    # measured 17 Nov 2026: the first word usually came after ~1.2 s, now and then after 10 s
    gemini = SpikyStream([5.0, 0.05])
    llm = RoutingLLM(Recorder(), gemini, first_token_s=30, stream_hedge_s=0.1)
    assert drain(llm) == ["reply 1.", " more"]
    assert len(gemini.calls) == 2 and sorted(gemini.closed) == [0, 1]


def test_a_quick_stream_sends_no_second_request():
    gemini = SpikyStream([0.01])
    llm = RoutingLLM(Recorder(), gemini, first_token_s=30, stream_hedge_s=0.5)
    assert drain(llm) == ["reply 0.", " more"]
    assert len(gemini.calls) == 1


def test_two_silent_streams_still_fail_so_the_local_model_answers():
    llm = RoutingLLM(
        Recorder(), SpikyStream([5.0, 5.0]), first_token_s=0.2, stream_hedge_s=0.05
    )
    with pytest.raises(UpstreamError, match="no answer within"):
        drain(llm)


def test_local_streams_are_never_duplicated():
    local = Recorder()
    llm = RoutingLLM(local, SpikyStream([0.0]), first_token_s=30, stream_hedge_s=0.01)
    assert drain(llm, "qwen2.5:3b") == ["ok"] and len(local.calls) == 1


class Overloaded(SpikyStream):
    """The first request is refused as overloaded (HTTP 503); the next one answers."""

    async def stream(self, model, messages, max_tokens, timeout):
        if not self.calls:
            self.calls.append(model)
            raise UpstreamError("Model gateway: HTTP 503")
            yield  # an async generator
        async for chunk in super().stream(model, messages, max_tokens, timeout):
            yield chunk


def test_an_overloaded_gemini_is_asked_once_more_before_the_local_model():
    gemini = Overloaded([0.0, 0.0])
    llm = RoutingLLM(Recorder(), gemini, first_token_s=30, stream_hedge_s=5)
    assert drain(llm) == ["reply 1.", " more"] and len(gemini.calls) == 2


class Quota(Recorder):
    """Gemini with its quota used up."""

    async def complete(self, model, messages, max_tokens, timeout):
        self.calls.append(model)
        raise UpstreamError("Model gateway: HTTP 429")


def test_after_a_quota_refusal_gemini_is_skipped_for_a_while():
    # seen 19 Nov 2026: every turn still waited for two or three 429 refusals
    gemini = Quota()
    llm = RoutingLLM(Recorder(), gemini, first_token_s=30)
    with pytest.raises(UpstreamError, match="429"):
        asyncio.run(llm.complete("gemini/m", MSGS, 100, 60))
    with pytest.raises(UpstreamError, match="quota used up"):
        asyncio.run(llm.complete("gemini/m", MSGS, 100, 60))
    assert len(gemini.calls) == 1  # the second call never reached Google
    assert (
        asyncio.run(llm.complete("qwen2.5:3b", MSGS, 100, 60)) == "ok"
    )  # local still works
