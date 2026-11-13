"""Module A adapter: the only place Module B talks to Knowledge & Learning (PRD 21.4).

Agreed contract (contracts/knowledge.openapi.yaml):
  POST /kb/search                             {query, role, department, academic_year, top_k}
                                              -> ranked chunks and curated answers with citations
  GET  /catalogue/programs/{program_id}/facts -> exact fees, dates, eligibility, intake

One more read is needed to recognise programmes in a question ("CSE", "Mechanical"): the
programme list. Module A's prototype serves it as GET /catalogue/programs (optionally with every
programme's facts, ?facts=true); it is in docs/proposed-contracts/knowledge.openapi.json and must be
agreed with Module A's owner. When it is missing (404), Module B keeps answering from search alone
and says so in the trace, rather than failing.

Everything Module A returns is normalised here into the shapes the engine uses, so a Module A
built separately against the same contract plugs in by URL (KNOWLEDGE_API_URL) without changes to
the engine. Failures are UpstreamError: the engine then gives an honest "no reliable source" and
offers a person, never a guess.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from .clients import UpstreamError
from .config import Settings

log = logging.getLogger(__name__)
CACHE_S = 60.0  # programme facts change rarely and are read on every turn


class NotFound(UpstreamError):
    pass


# ------------------------------------------------------------------ normalisation


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalise_hit(raw: dict) -> dict | None:
    """One search result in the engine's shape, or None when it has no text to cite."""
    content = raw.get("content") or raw.get("text") or ""
    if not isinstance(content, str) or not content.strip():
        return None
    c = dict(raw.get("citation") or {})
    kind = raw.get("kind") or ("curated_qa" if c.get("qa_id") else "chunk")
    source_type = c.get("source_type") or (
        "curated_qa" if kind == "curated_qa" else "document"
    )
    score = _num(raw.get("score"))
    vector = _num(raw.get("vector_score"))
    if vector is None:
        vector = _num(raw.get("similarity"))
    if vector is None:
        vector = (
            score  # a Module A that returns one score only: the floor applies to it
        )
    ident = (
        raw.get("id") or c.get("qa_id") or c.get("document_id") or str(hash(content))
    )
    return {
        "id": str(ident),
        "kind": kind,
        "content": content,
        "score": score if score is not None else (vector or 0.0),
        "vector_score": vector if vector is not None else 0.0,
        "citation": {
            "source_type": source_type,
            "document_id": c.get("document_id"),
            "qa_id": c.get("qa_id"),
            "title": c.get("title") or raw.get("title") or "Source",
            "heading_path": c.get("heading_path") or raw.get("heading_path"),
            "page": c.get("page"),
            "academic_year": c.get("academic_year"),
            "department": c.get("department"),
            "url": c.get("url"),
        },
    }


def normalise_search(data: Any) -> dict:
    """{"items": [...], "embedder": "primary" | "multilingual"} from Module A's search reply."""
    if not isinstance(data, dict):
        raise UpstreamError("Knowledge service: search reply is not an object")
    raw = data.get("items")
    if raw is None:
        raw = data.get("chunks", data.get("results", []))
    items = [
        h for h in (normalise_hit(x) for x in raw or [] if isinstance(x, dict)) if h
    ]
    embedder = (
        data.get("embedder")
        if data.get("embedder") in ("primary", "multilingual")
        else "primary"
    )
    return {"items": items, "embedder": embedder}


def normalise_program(p: dict) -> dict | None:
    if not isinstance(p, dict) or not (p.get("code") or p.get("id")):
        return None
    out = {
        "id": str(p.get("id") or p.get("code")),
        "code": str(p.get("code") or p.get("id")),
        "name": p.get("name") or str(p.get("code")),
        "aliases": [
            a for a in (p.get("aliases") or []) if isinstance(a, str) and a.strip()
        ],
        "department": p.get("department"),
        "level": p.get("level") or "",
        "academic_year": p.get("academic_year") or "",
    }
    for key in ("duration_months", "intake", "eligibility", "contact", "citation"):
        if p.get(key) is not None:
            out[key] = p[key]
    if "fees" in p:  # facts present (not only the list entry)
        out["fees"] = [
            {
                "category": f.get("category") or "general",
                "component": f.get("component") or "fee",
                # Amounts are authoritative: passed on exactly as given, never re-formatted.
                "amount": f.get("amount"),
                "currency": f.get("currency") or "INR",
                "period": f.get("period") or "",
            }
            for f in p.get("fees") or []
            if isinstance(f, dict) and f.get("amount") is not None
        ]
        out["dates"] = [
            {"label": d.get("label") or "Date", "date": d["date"]}
            for d in p.get("dates") or []
            if isinstance(d, dict) and d.get("date")
        ]
    return out


def normalise_catalogue(data: Any) -> dict:
    if not isinstance(data, dict):
        raise UpstreamError("Knowledge service: catalogue reply is not an object")
    items = [x for x in (normalise_program(p) for p in data.get("items") or []) if x]
    general = [
        {
            "label": d.get("label") or "Date",
            "date": d["date"],
            "academic_year": d.get("academic_year")
            or (items[0]["academic_year"] if items else ""),
        }
        for d in data.get("general_dates") or []
        if isinstance(d, dict) and d.get("date")
    ]
    return {"items": items, "general_dates": general}


# ------------------------------------------------------------------ the client


class KnowledgeClient:
    """Module A over HTTP with this module's own key (KEY_B_TO_A) and the tenant in X-Tenant-Id;
    Module A's row-level security limits every read to that tenant.

    Timeouts are short (retrieval's budget is 350 ms on the lab GPU, PRD 7.5): a slow Module A
    costs the visitor seconds, not the full answer budget. A request that never reached Module A
    (connection refused or timed out while connecting) is tried once more; nothing else is retried.
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.base = settings.knowledge_api_url.rstrip("/")
        self.key = settings.key_to_knowledge
        t = settings.knowledge_timeout_s
        self.timeout = httpx.Timeout(t, connect=min(2.0, t))
        self.client = client or httpx.AsyncClient(timeout=self.timeout)
        self._programs: dict[str, tuple[float, dict]] = {}
        self._facts: dict[tuple[str, str], tuple[float, dict | None]] = {}

    async def _call(self, method: str, path: str, tenant: str, **kw) -> Any:
        url = f"{self.base}{path}"
        headers = {"X-Tenant-Id": tenant, "X-Service-Key": self.key}
        for attempt in (1, 2):
            try:
                r = await self.client.request(
                    method, url, headers=headers, timeout=self.timeout, **kw
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                if attempt == 1:
                    continue
                raise UpstreamError(
                    f"Knowledge service: {exc.__class__.__name__}"
                ) from exc
            except httpx.HTTPError as exc:
                raise UpstreamError(
                    f"Knowledge service: {exc.__class__.__name__}"
                ) from exc
            if r.status_code == 404:
                raise NotFound(f"Knowledge service: {path} not found")
            if r.status_code >= 400:
                raise UpstreamError(f"Knowledge service: HTTP {r.status_code}")
            try:
                return r.json()
            except ValueError as exc:
                raise UpstreamError("Knowledge service: reply is not JSON") from exc
        raise UpstreamError("Knowledge service: unreachable")  # pragma: no cover

    async def search(
        self,
        tenant: str,
        query: str,
        *,
        top_k: int = 6,
        role: str | None = None,
        department: str | None = None,
        academic_year: str | None = None,
    ) -> dict:
        body = {"query": query[:2000], "top_k": top_k}
        for key, value in (
            ("role", role),
            ("department", department),
            ("academic_year", academic_year),
        ):
            if value:
                body[key] = value
        return normalise_search(
            await self._call("POST", "/kb/search", tenant, json=body)
        )

    async def programs(self, tenant: str) -> dict:
        """The programme list (with facts when Module A includes them), cached for a minute."""
        hit = self._programs.get(tenant)
        if hit and time.monotonic() - hit[0] < CACHE_S:
            return hit[1]
        try:
            data = normalise_catalogue(
                await self._call(
                    "GET", "/catalogue/programs", tenant, params={"facts": "true"}
                )
            )
        except NotFound:
            log.warning(
                "Module A has no GET /catalogue/programs: programmes cannot be recognised"
            )
            data = {"items": [], "general_dates": [], "unavailable": "programme list"}
        self._programs[tenant] = (time.monotonic(), data)
        return data

    async def program_facts(self, tenant: str, program: str) -> dict | None:
        """Exact facts of one programme (agreed contract). None when Module A does not know it."""
        key = (tenant, program)
        hit = self._facts.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_S:
            return hit[1]
        try:
            facts = normalise_program(
                await self._call("GET", f"/catalogue/programs/{program}/facts", tenant)
            )
        except NotFound:
            facts = None
        self._facts[key] = (time.monotonic(), facts)
        return facts


async def with_facts(
    knowledge, tenant: str, programs: list[dict], limit: int = 10
) -> list[dict]:
    """The given programmes, each with its fees and dates. A list entry without facts is completed
    from GET /catalogue/programs/{id}/facts (in parallel); one whose facts cannot be read is kept
    as it is (named, but with no numbers to quote)."""
    fetch = getattr(knowledge, "program_facts", None)
    need = [p for p in programs[:limit] if "fees" not in p]
    if not need or fetch is None:
        return programs
    results = await asyncio.gather(
        *(fetch(tenant, p["code"]) for p in need), return_exceptions=True
    )
    filled = {
        p["code"]: r
        for p, r in zip(need, results)
        if isinstance(r, dict) and r.get("fees") is not None
    }
    return [{**p, **filled[p["code"]]} if p["code"] in filled else p for p in programs]
