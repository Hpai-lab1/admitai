"""Calls out of Module B: the model gateway (OpenAI-compatible chat completions: LiteLLM on the lab
servers, Ollama locally) and the interfaces the engine uses for Module A (knowledge.py) and the
model. All are small so tests can substitute fakes."""

from __future__ import annotations

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

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
        self.headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
        self.client = client or httpx.AsyncClient()
        # Keeps the model loaded between questions (Ollama); not sent when unset.
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
