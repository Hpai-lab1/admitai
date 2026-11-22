"""Draft a curated answer for a "No reliable answer" proposal from the college's own approved
documents, so the approver edits a draft instead of writing from nothing.

Only on request (the approver presses "Draft from documents"), so no model time is spent on
proposals nobody opens, and none competes with live chat. Nothing goes live from here: the
approver edits and approves, and the evaluation gate still runs. A draft is refused, with the
reason, when the documents do not cover the question, when it cites nothing, or when it holds a
number that is not in the documents.
"""

from __future__ import annotations

import re
from typing import Protocol

import httpx

from . import search
from .config import Settings
from .embedder import Embedder

NO_ANSWER = "NO_ANSWER"
SYSTEM = (
    "You draft answers for a college admissions office, using ONLY the numbered sources. Write at "
    "most three short sentences in plain English, and end each factual sentence with its source "
    f"number, like [1]. If the sources do not answer the question, reply exactly {NO_ANSWER}."
)
NOT_COVERED = (
    "No approved document covers this question. Write the answer yourself, or upload the document "
    "that has it."
)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_CITE = re.compile(r"\s*\[(\d+)\]")


class DraftModel(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


class GatewayDraftModel:
    """The answer model behind the shared OpenAI-compatible gateway (Ollama locally)."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.name = settings.draft_model
        self.url = settings.judge_url.rstrip("/") + "/chat/completions"
        self.headers = (
            {"Authorization": f"Bearer {settings.judge_api_key}"}
            if settings.judge_api_key
            else {}
        )
        self.client = client or httpx.Client(timeout=120)

    def complete(self, system: str, user: str) -> str:
        r = self.client.post(
            self.url,
            headers=self.headers,
            json={
                "model": self.name,
                "temperature": 0,
                "max_tokens": 300,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def _digits(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER.findall(text)}


def draft(
    settings: Settings,
    embedder: Embedder,
    model: DraftModel,
    tenant: str,
    question: str,
) -> dict:
    found = search.search(
        settings.database_url,
        embedder,
        tenant,
        question,
        top_k=4,
        min_score=settings.draft_min_score,
    )
    items = found["items"] if found["answerable"] else []
    if not items:
        return {"text": None, "reason": NOT_COVERED, "sources": []}
    block = "\n\n".join(
        f"[{n}] {i['citation']['title']}: {i['content'][:1500]}"
        for n, i in enumerate(items, 1)
    )
    reply = model.complete(SYSTEM, f"Sources:\n{block}\n\nQuestion: {question}")
    reply = re.sub(r"<think>.*?</think>", "", reply or "", flags=re.S).strip()
    if not reply or NO_ANSWER in reply:
        return {
            "text": None,
            "reason": "The college's documents were searched, but they do not answer this "
            "question. Write the answer yourself.",
            "sources": [],
        }
    cited = sorted({int(n) for n in _CITE.findall(reply) if 1 <= int(n) <= len(items)})
    if not cited:
        return {
            "text": None,
            "reason": "The draft did not say which document it came from, so it was not used. "
            "Write the answer yourself.",
            "sources": [],
        }
    known = _digits(" ".join(items[n - 1]["content"] for n in cited))
    unknown = sorted(_digits(_CITE.sub("", reply)) - known)
    if unknown:
        return {
            "text": None,
            "reason": f"The draft had a number not found in the documents ({unknown[0]}), so it "
            "was not used. Write the answer yourself.",
            "sources": [],
        }
    sources = []
    for n in cited:
        c = items[n - 1]["citation"]
        sources.append(
            {
                "title": c["title"],
                "document_id": c["document_id"],
                "page": c["page"],
                "heading": c["heading_path"],
            }
        )
    # The approved answer becomes a curated answer that is itself the source, so the numbers of
    # this draft's sources are dropped from its text; the sources are listed beside it instead.
    return {
        "text": _CITE.sub("", reply).strip(),
        "sources": sources,
        "model": model.name,
    }
