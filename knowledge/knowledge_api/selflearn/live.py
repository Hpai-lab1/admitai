"""What search reads from live lessons (only with KNOWLEDGE_SELF_LEARNING_ENABLED on).

- Aliases: a college's own words for something ("PG" -> "postgraduate") are added to a question
  that uses the visitor's term, after the shared admissions vocabulary (paraphrase.py).
- Retrieval boosts: for questions close to a lesson's question cluster, the passage the evidence
  came from is put first. Its vector score is reported as it is, never raised, so Module B's own
  no-answer floor still decides whether it may be used.
"""

from __future__ import annotations

import os
import re

import numpy as np

from ..db import tx


def enabled() -> bool:
    return os.environ.get("KNOWLEDGE_SELF_LEARNING_ENABLED", "false").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def load(database_url: str, tenant: str) -> tuple[list[dict], list[dict]]:
    with tx(database_url, tenant) as cur:
        cur.execute(
            "select lesson_id::text, phrase, expansion from learn.lesson_aliases where active"
        )
        aliases = cur.fetchall()
        cur.execute(
            "select lesson_id::text, centroid, min_similarity, chunk_id::text, document_url,"
            " quote from learn.retrieval_boosts where active"
        )
        boosts = cur.fetchall()
    return aliases, boosts


def expand(query: str, asked: str, aliases: list[dict]) -> str:
    low = asked.lower()
    extra = [
        a["expansion"]
        for a in aliases
        if re.search(rf"(?<!\w){re.escape(a['phrase'])}(?!\w)", low)
        and a["expansion"].lower() not in low
    ]
    return f"{query} {' '.join(dict.fromkeys(extra))}" if extra else query


def _norm(t: str) -> str:
    return " ".join((t or "").lower().split())


def target(boost: dict, by_id: dict[str, dict]) -> str | None:
    """The boosted chunk if it is still servable; after a re-crawl, the chunk of the same page
    that still holds the quoted words."""
    if boost["chunk_id"] in by_id:
        return boost["chunk_id"]
    quote = _norm(boost["quote"])
    for cid, m in by_id.items():
        if (
            m["kind"] == "chunk"
            and (not boost["document_url"] or m.get("url") == boost["document_url"])
            and quote in _norm(m["content"])
        ):
            return cid
    return None


def matching(qvec: np.ndarray, boosts: list[dict]) -> list[tuple[float, dict]]:
    out = []
    for b in boosts:
        c = np.asarray(b["centroid"], dtype=np.float32)
        if c.shape != qvec.shape:
            continue
        sim = float(np.dot(qvec, c / max(float(np.linalg.norm(c)), 1e-9)))
        if sim >= b["min_similarity"]:
            out.append((sim, b))
    return sorted(out, key=lambda x: -x[0])
