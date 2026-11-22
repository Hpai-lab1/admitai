"""Calls out of Module B: the model gateway (OpenAI-compatible chat completions: LiteLLM on the lab
servers, Ollama locally) and the interfaces the engine uses for Module A (knowledge.py) and the
model. All are small so tests can substitute fakes."""

from __future__ import annotations

import asyncio
import time
import json
from contextvars import ContextVar
from typing import AsyncIterator, Protocol

import httpx

from .config import Settings


# PRD F9.4: the engine sets a fresh tally for each turn; every gateway call of that turn adds the
# tokens the gateway reports. A context variable, so concurrent turns never share one.
TOKEN_USAGE: ContextVar[dict | None] = ContextVar("token_usage", default=None)


def _count(usage: dict | None) -> None:
    tally = TOKEN_USAGE.get()
    if tally is None or not isinstance(usage, dict):
        return
    tally["prompt"] += int(usage.get("prompt_tokens") or 0)
    tally["completion"] += int(usage.get("completion_tokens") or 0)


class UpstreamError(RuntimeError):
    pass


class Knowledge(Protocol):
    """Module A, as the engine sees it (knowledge.KnowledgeClient over HTTP; fakes in tests)."""

    async def search(self, tenant: str, query: str, **kw) -> dict: ...
    async def programs(self, tenant: str) -> dict: ...

    # Optional: exact facts of one programme (GET /catalogue/programs/{id}/facts).
    # async def program_facts(self, tenant: str, program: str) -> dict | None: ...


class LLM(Protocol):
    def stream(
        self, model: str, messages: list[dict], max_tokens: int, timeout: float
    ) -> AsyncIterator[str]: ...
    async def complete(
        self, model: str, messages: list[dict], max_tokens: int, timeout: float
    ) -> str: ...


CONNECT_TIMEOUT_S = (
    3.0  # a gateway that is not there fails fast, so the fallback still has time
)


def _timeout(total: float) -> httpx.Timeout:
    """`total` bounds each read (the wait for the next chunk); connecting is bounded separately."""
    return httpx.Timeout(
        total, connect=min(CONNECT_TIMEOUT_S, total), pool=CONNECT_TIMEOUT_S
    )


def _gateway_error(exc: httpx.HTTPError) -> UpstreamError:
    if isinstance(exc, httpx.HTTPStatusError):
        return UpstreamError(f"Model gateway: HTTP {exc.response.status_code}")
    return UpstreamError(f"Model gateway: {exc.__class__.__name__}")


class OpenAICompatibleLLM:
    """The model gateway (PRD 9.5): LiteLLM's proxy on the lab servers, which fronts the shared
    Ollama service; Ollama's own OpenAI-compatible API on a laptop. Same wire format either way,
    so which one is used is configuration (LLM_BASE_URL), not code. Level 1 calls only local
    models: config.check_production refuses a gateway address outside the lab network."""

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        extra: dict | None = None,
    ):
        external = base_url is not None
        self.url = f"{(base_url or settings.llm_base_url).rstrip('/')}/chat/completions"
        self.headers = {"Authorization": f"Bearer {api_key or settings.llm_api_key}"}
        self.client = client or httpx.AsyncClient()
        # Keeps the model loaded between questions (Ollama); not sent when unset or external.
        # An external endpoint gets its own extras instead (Gemini: reasoning_effort).
        if external:
            self.extra = dict(extra or {})
        else:
            self.extra = (
                {"keep_alive": settings.model_keep_alive}
                if settings.model_keep_alive
                else {}
            )

    async def stream(
        self, model: str, messages: list[dict], max_tokens: int, timeout: float
    ) -> AsyncIterator[str]:
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream": True,
            **self.extra,
            # The last chunk then carries the token counts, with no choices (F9.4).
            "stream_options": {"include_usage": True},
        }
        try:
            async with self.client.stream(
                "POST",
                self.url,
                json=body,
                headers=self.headers,
                timeout=_timeout(timeout),
            ) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    _count(chunk.get("usage"))
                    choices = chunk["choices"]
                    if not choices:  # the usage chunk
                        continue
                    delta = choices[0].get("delta", {})
                    if delta.get("content"):
                        yield delta["content"]
        except httpx.HTTPError as exc:
            raise _gateway_error(exc) from exc
        except (ValueError, KeyError, IndexError) as exc:  # not an OpenAI-style stream
            raise UpstreamError("Model gateway: unreadable stream") from exc

    async def complete(
        self, model: str, messages: list[dict], max_tokens: int, timeout: float
    ) -> str:
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
            **self.extra,
        }
        try:
            r = await self.client.post(
                self.url, json=body, headers=self.headers, timeout=_timeout(timeout)
            )
            r.raise_for_status()
            reply = r.json()
            _count(reply.get("usage"))
            return reply["choices"][0]["message"]["content"] or ""
        except httpx.HTTPError as exc:
            raise _gateway_error(exc) from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise UpstreamError("Model gateway: unreadable reply") from exc


GEMINI_PREFIX = "gemini/"
# Room for Gemini's thinking on top of the answer budget (reasoning_effort "low").
GEMINI_THINKING_TOKENS = 1024


def _masked(messages: list[dict]) -> list[dict]:
    """What may leave the platform: e-mail addresses, phone and ID numbers replaced."""
    from .guard import mask_pii

    return [
        {**m, "content": mask_pii(m["content"])}
        if isinstance(m.get("content"), str)
        else m
        for m in messages
    ]


async def _next(chunks) -> tuple[bool, str | None]:
    """The stream's next chunk, as (True, chunk), or (False, None) once it has ended."""
    try:
        return True, await chunks.__anext__()
    except StopAsyncIteration:
        return False, None


class RoutingLLM:
    """Local models through the gateway; a model named "gemini/<name>" (only ever chosen for a
    college whose pack opts in: policy.py) goes to Google AI Studio, with personal details
    masked first. If Gemini fails, the engine's next model (the college's local one) answers."""

    def __init__(
        self,
        local: LLM,
        gemini: LLM | None = None,
        first_token_s: float = 6.0,
        hedge_s: float = 0.0,
        stream_hedge_s: float = 0.0,
    ):
        self.local, self.gemini, self.first_token_s = local, gemini, first_token_s
        self.hedge_s, self.stream_hedge_s = hedge_s, stream_hedge_s

    # After Google refuses for quota (HTTP 429), Gemini is skipped for this many seconds so each
    # turn goes straight to the fallbacks (seen 19 Nov 2026: with the free quota used up, every
    # turn still waited for two or three refusals).
    QUOTA_PAUSE_S = 300.0
    _quota_until = 0.0

    def _route(self, model: str, messages: list[dict]) -> tuple[LLM, str, list[dict]]:
        if model.startswith(GEMINI_PREFIX):
            if self.gemini is None:
                raise UpstreamError("Gemini is not configured (GEMINI_API_KEY)")
            if time.monotonic() < self._quota_until:
                raise UpstreamError(
                    "Gemini: quota used up (HTTP 429); skipped for a few minutes"
                )
            return self.gemini, model[len(GEMINI_PREFIX) :], _masked(messages)
        return self.local, model, messages

    def _note(self, model: str, exc: UpstreamError) -> None:
        if model.startswith(GEMINI_PREFIX) and "429" in str(exc):
            self._quota_until = time.monotonic() + self.QUOTA_PAUSE_S

    async def stream(
        self, model: str, messages: list[dict], max_tokens: int, timeout: float
    ) -> AsyncIterator[str]:
        try:
            async for chunk in self._stream(model, messages, max_tokens, timeout):
                yield chunk
        except UpstreamError as exc:
            self._note(model, exc)
            raise

    async def complete(
        self, model: str, messages: list[dict], max_tokens: int, timeout: float
    ) -> str:
        try:
            return await self._complete(model, messages, max_tokens, timeout)
        except UpstreamError as exc:
            self._note(model, exc)
            raise

    async def _stream(
        self, model: str, messages: list[dict], max_tokens: int, timeout: float
    ) -> AsyncIterator[str]:
        llm, name, msgs = self._route(model, messages)
        if llm is self.local:
            async for chunk in llm.stream(name, msgs, max_tokens, timeout):
                yield chunk
            return
        # Gemini thinks before it writes, and on Google's OpenAI-compatible API the thinking
        # counts against max_tokens: seen live, answers stopped mid-word after 84 characters.
        max_tokens += GEMINI_THINKING_TOKENS
        opened: list = []
        waiting: dict[asyncio.Future, int] = {}

        def open_one() -> None:
            chunks = llm.stream(name, msgs, max_tokens, timeout)
            opened.append(chunks)
            waiting[asyncio.ensure_future(_next(chunks))] = len(opened) - 1

        loop = asyncio.get_running_loop()
        began = loop.time()
        deadline = began + self.first_token_s
        open_one()
        winner: tuple[int, str] | None = None
        try:
            while winner is None:
                now = loop.time()
                if now >= deadline:
                    # If Google is overloaded and says nothing, fail fast so the local model
                    # answers.
                    raise UpstreamError(
                        f"Gemini: no answer within {self.first_token_s:g} s"
                    )
                wait = deadline - now
                hedge = 0 < self.stream_hedge_s and len(opened) == 1
                if hedge:
                    wait = min(wait, max(began + self.stream_hedge_s - now, 0))
                done, _ = await asyncio.wait(
                    waiting, timeout=wait, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    i = waiting.pop(task)
                    exc = task.exception()
                    if exc is None:
                        more, first = task.result()
                        if not more:
                            if not waiting:
                                return
                            continue
                        winner = (i, first)
                        break
                    if waiting:
                        continue  # the other request may still answer
                    # A connection that failed before any answer is tried once more (seen live:
                    # one ConnectTimeout, then the local model on a laptop CPU timed out too), and
                    # so is "overloaded" (HTTP 503, seen live 17 Nov 2026: the local model then
                    # took 4 s and its answer failed the checks).
                    said = str(exc).lower()
                    if len(opened) == 1 and ("connect" in said or "503" in said):
                        open_one()
                        continue
                    raise exc
                if not done and hedge and loop.time() < deadline:
                    # The first word is late (Google's response time spikes: seen live, 10 s
                    # once and about 1.2 s otherwise): the same request is sent again and the
                    # first to start answering is used; the other is closed.
                    open_one()
            for task in waiting:
                task.cancel()
            await asyncio.gather(*waiting, return_exceptions=True)
            waiting.clear()
            i, first = winner
            for j, other in enumerate(opened):
                if j != i:
                    await other.aclose()
            yield first
            async for chunk in opened[i]:
                yield chunk
        finally:
            for task in waiting:
                task.cancel()
            await asyncio.gather(*waiting, return_exceptions=True)
            for chunks in opened:
                await chunks.aclose()

    async def _complete(
        self, model: str, messages: list[dict], max_tokens: int, timeout: float
    ) -> str:
        llm, name, msgs = self._route(model, messages)
        if llm is self.local:
            return await llm.complete(name, msgs, max_tokens, timeout)
        timeout = min(timeout, self.first_token_s * 2)
        max_tokens += GEMINI_THINKING_TOKENS
        return await self._hedged(
            lambda: llm.complete(name, msgs, max_tokens, timeout), timeout
        )

    async def _hedged(self, call, timeout: float) -> str:
        """Google's response time spikes now and then (seen live: the same Kannada translation
        took 37 s once and 3.5-4.4 s otherwise). If no reply has come within `hedge_s`, the
        same request is sent again and whichever answers first is used; the other is cancelled.
        The cost is an occasional duplicate request, never a different answer."""
        first = asyncio.ensure_future(call())
        if self.hedge_s <= 0 or self.hedge_s >= timeout:
            return await first
        done, _ = await asyncio.wait({first}, timeout=self.hedge_s)
        if done:
            return first.result()
        second = asyncio.ensure_future(call())
        pending = {first, second}
        error: BaseException | None = None
        try:
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    if task.exception() is None:
                        return task.result()
                    error = task.exception()
            raise error  # both failed: the engine falls back as before
        finally:
            for task in pending:
                task.cancel()


def make_llm(settings: Settings) -> RoutingLLM:
    gemini = (
        OpenAICompatibleLLM(
            settings,
            base_url=settings.gemini_base_url,
            api_key=settings.gemini_api_key,
            extra=(
                {"reasoning_effort": settings.gemini_reasoning_effort}
                if settings.gemini_reasoning_effort
                else None
            ),
        )
        if settings.gemini_api_key
        else None
    )
    return RoutingLLM(
        OpenAICompatibleLLM(settings),
        gemini,
        settings.gemini_first_token_s,
        settings.gemini_hedge_s,
        settings.gemini_stream_hedge_s,
    )
