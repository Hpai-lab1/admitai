"""Asynchronous grounding review (PRD F1.4b): every answered message is reviewed off the hot path,
in batch, against the exact source text the model saw. It never delays a visitor; unsupported
answers become signals for Module A's improvement loop.

Each factual sentence is tested twice, and must pass both:
  1. quote test: the model is asked to copy the words of the source that state the claim (or
     NONE); code then checks that the quote really is in the source and matches the claim. Small
     models are unreliable at yes/no judgements but can copy text, and copying can be verified.
  2. novelty test (no model): a claim with two or more key words that appear nowhere in its cited
     sources is unsupported, e.g. "fees can be paid in instalments" against a fee table that never mentions
     instalments.

python -m assistant_api.review run --tenant-id <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys

from .checks import _CITE, content_words, is_factual, split_sentences, strip_list_marker
from .clients import LLM, OpenAICompatibleLLM, UpstreamError
from . import store
from .config import Settings
from .db import Jsonb, tx

REVIEW_PROMPT = """Source:
{source}

Claim: {claim}

Copy the exact words from the source that state this claim. If the source does not state it, reply NONE.
Words from the source:"""

NOVELTY_LIMIT = 0.25  # share of a claim's key words that may be absent from its sources
GENERIC = set(
    "college university students student program programs programme course courses available offer "
    "offers provide provides also please contact details information admission admissions year "
    # filler words that add no claim of their own
    "including include includes several various many some other such different each every both "
    "there here there's which while where whether well just only more most very really".split()
)
# Words visitors and sources use for the same thing.
SYNONYMS = {
    "girls": "women",
    "girl": "women",
    "boys": "men",
    "boy": "men",
    "ladies": "women",
    "gents": "men",
    "freshers": "first",
    "fresher": "first",
    "rupees": "inr",
    "rs": "inr",
}


def _stem(w: str) -> str:
    w = SYNONYMS.get(w, w)
    return (w[:-1] if len(w) > 3 and w.endswith("s") else w)[:5]


def _stems(text: str) -> set[str]:
    return {_stem(w) for w in content_words(text)}


def novel_words(claim: str, sources: list[str]) -> list[str]:
    """Key words of the claim (4+ letters, not generic) that appear in none of its sources."""
    support = set().union(*(_stems(s) for s in sources)) if sources else set()
    return sorted(
        {
            w
            for w in content_words(claim)
            if w not in GENERIC and len(w) >= 4 and _stem(w) not in support
        }
    )


def novelty(claim: str, sources: list[str]) -> float:
    key = {w for w in content_words(claim) if w not in GENERIC and len(w) >= 4}
    return len(novel_words(claim, sources)) / len(key) if key else 0.0


def adds_claims(claim: str, sources: list[str]) -> bool:
    """Two or more new key words ("paid", "instalments"), or over a quarter of them, means the
    sentence says something its sources do not. A long program name must not dilute that."""
    return (
        len(novel_words(claim, sources)) >= 2 or novelty(claim, sources) > NOVELTY_LIMIT
    )


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_NEG = re.compile(
    r"\b(not|no|never|none|without|cannot|can't|isn't|aren't|doesn't|don't|won't)\b",
    re.I,
)


def _clauses(text: str) -> list[str]:
    return [c for c in re.split(r"(?<=[.;:\n])\s+|\n", text) if c.strip()]


def words_support(claim: str, sources: list[str]) -> bool:
    """Deterministic support, before any model is asked: the claim adds no key word its sources
    lack, each of its numbers stands in a source clause that shares a key word with the claim (so
    a number is not borrowed from another line), and it is not negated unless the source is.
    Small judges often answer NONE for claims the source states almost word for word; this keeps
    those from counting as unsupported, and leaves the model for claims that add something."""
    if not sources or novel_words(claim, sources):
        return False
    if _NEG.search(claim) and not any(_NEG.search(s) for s in sources):
        return False
    key = {_stem(w) for w in content_words(claim) if w not in GENERIC and len(w) >= 4}
    clauses = [c for s in sources for c in _clauses(s)]
    for n in {x.replace(",", "") for x in _NUM.findall(claim)}:
        if not any(
            n in {y.replace(",", "") for y in _NUM.findall(c)} and key & _stems(c)
            for c in clauses
        ):
            return False
    return True


def quote_supports(quote: str, claim: str, sources: list[str]) -> bool:
    # Small models often echo the prompt's label before the quote.
    q = re.sub(
        r"^\s*(words from the source|source words|answer|quote)\s*:\s*",
        "",
        quote.strip(),
        flags=re.I,
    )
    q = q.strip().strip('"').strip()
    if not q or q.upper().startswith("NONE"):
        return False
    q_words, claim_words = _stems(q), _stems(claim)
    if not q_words:
        return False
    in_source = max(
        (len(q_words & _stems(s)) / len(q_words) for s in sources), default=0
    )
    about_claim = len(q_words & claim_words) / max(
        1, min(len(q_words), len(claim_words))
    )
    return in_source >= 0.7 and about_claim >= 0.3


async def review_message(
    llm: LLM, model: str, answer: str, citations: list[dict], known: str = ""
) -> dict:
    """`known`: words that are never a new claim for this tenant (the college's own name)."""
    by_n = {c["n"]: c.get("content") or "" for c in citations}
    if not any(by_n.values()):
        return {"verdict": "skipped", "sentences": []}
    results = []
    for sentence in split_sentences(answer, final=True)[0]:
        if not is_factual(sentence):
            continue
        cited = [int(n) for n in _CITE.findall(sentence)]
        texts = [by_n[n] for n in cited if by_n.get(n)] or [
            t for t in by_n.values() if t
        ]
        claim = strip_list_marker(_CITE.sub("", sentence)).strip()
        if re.search(r"[ऀ-෿]", claim):
            results.append(
                {
                    "sentence": sentence,
                    "cited": cited,
                    "supported": None,
                    "note": "not English",
                }
            )
            continue
        words = texts + [known] if known else texts
        new = novelty(claim, words)
        if words_support(claim, words):
            results.append(
                {
                    "sentence": sentence,
                    "cited": cited,
                    "quote": "",
                    "novelty": round(new, 2),
                    "new_words": [],
                    "supported": True,
                    "method": "words",
                }
            )
            continue
        try:
            quote = await llm.complete(
                model,
                [
                    {
                        "role": "user",
                        "content": REVIEW_PROMPT.format(
                            source="\n\n".join(texts)[:4000], claim=claim
                        ),
                    }
                ],
                max_tokens=120,
                timeout=60,
            )
        except UpstreamError:
            quote = ""
        supported = not adds_claims(claim, words) and quote_supports(
            quote, claim, texts
        )
        results.append(
            {
                "sentence": sentence,
                "cited": cited,
                "quote": quote.strip()[:300],
                "novelty": round(new, 2),
                "new_words": novel_words(claim, words),
                "supported": supported,
            }
        )
    checked = [r for r in results if r["supported"] is not None]
    verdict = (
        "skipped"
        if not checked
        else ("supported" if all(r["supported"] for r in checked) else "unsupported")
    )
    return {"verdict": verdict, "sentences": results}


async def _review_and_store(
    settings: Settings, llm: LLM, tenant: str, messages: list[dict]
) -> dict:
    """Review these {id, content} messages against their stored sources; returns id -> review."""
    cites: dict = {}
    if messages:
        with tx(settings.database_url, tenant) as cur:
            cur.execute(
                "select message_id, n, content from chat.message_citations where message_id = any(%s)",
                ([m["id"] for m in messages],),
            )
            for c in cur.fetchall():
                cites.setdefault(c["message_id"], []).append(c)
    cfg = store.tenant_config(settings.database_url, tenant)
    known = " ".join(
        [str(cfg.get("college") or ""), *map(str, cfg.get("college_aliases") or [])]
    )
    out = {}
    for m in messages:
        r = await review_message(
            llm, settings.router_model, m["content"], cites.get(m["id"], []), known
        )
        out[str(m["id"])] = r
        with tx(settings.database_url, tenant) as cur:
            cur.execute(
                "insert into chat.grounding_reviews (message_id, tenant_id, verdict, sentences, model)"
                " values (%s,%s,%s,%s,%s) on conflict (message_id) do nothing",
                (
                    m["id"],
                    tenant,
                    r["verdict"],
                    Jsonb(r["sentences"]),
                    settings.router_model,
                ),
            )
    return out


async def run(settings: Settings, llm: LLM, tenant: str, limit: int = 50) -> dict:
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select m.id, m.content from chat.messages m where m.sender = 'assistant'"
            " and m.outcome in ('answered', 'partial') and m.content <> '[erased]'"
            " and exists (select 1 from chat.conversations c where c.id = m.conversation_id"
            "   and c.origin = 'visitor')"
            " and not exists (select 1 from chat.grounding_reviews r where r.message_id = m.id)"
            " order by m.created_at limit %s",
            (limit,),
        )
        messages = cur.fetchall()
    counts = {"supported": 0, "unsupported": 0, "skipped": 0}
    for r in (await _review_and_store(settings, llm, tenant, messages)).values():
        counts[r["verdict"]] += 1
    return counts


def summary(review: dict) -> dict:
    """Claim-level counts of one review: what an evaluation's groundedness is computed from."""
    checked = [x for x in review["sentences"] if x.get("supported") is not None]
    return {
        "verdict": review["verdict"],
        "claims": len(checked),
        "supported": sum(1 for x in checked if x["supported"]),
        "unsupported": [x["sentence"] for x in checked if not x["supported"]][:5],
    }


async def review_ids(
    settings: Settings, llm: LLM, tenant: str, message_ids: list[str]
) -> dict:
    """Review these answers now (evaluation runs, PRD 20: groundedness is measured, not assumed).
    Answers already reviewed are not reviewed again."""
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select message_id::text as id, verdict, sentences from chat.grounding_reviews"
            " where message_id::text = any(%s)",
            (message_ids,),
        )
        done = {r["id"]: r for r in cur.fetchall()}
        cur.execute(
            "select id, content from chat.messages where sender = 'assistant'"
            " and id::text = any(%s) and content <> '[erased]'",
            ([i for i in message_ids if i not in done],),
        )
        todo = cur.fetchall()
    done.update(await _review_and_store(settings, llm, tenant, todo))
    return {i: summary(r) for i, r in done.items()}


def unsupported(database_url: str, tenant: str, since: str | None) -> list[dict]:
    """Unsupported answers with their question, for Module A's improvement loop."""
    with tx(database_url, tenant) as cur:
        cur.execute(
            "select r.message_id as id, r.sentences, r.reviewed_at as created_at, m.conversation_id, m.trace_id,"
            " m.content as answer,"
            " (select u.content from chat.messages u where u.conversation_id = m.conversation_id"
            "   and u.sender = 'user' and u.created_at < m.created_at order by u.created_at desc limit 1)"
            "   as question"
            " from chat.grounding_reviews r join chat.messages m on m.id = r.message_id"
            " where r.verdict = 'unsupported' and m.content <> '[erased]'"
            " and exists (select 1 from chat.conversations c where c.id = m.conversation_id"
            "   and c.origin = 'visitor')"
            " and (%s::timestamptz is null or r.reviewed_at > %s::timestamptz)"
            " order by r.reviewed_at",
            (since, since),
        )
        return cur.fetchall()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Off-peak grounding review for one tenant")
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("--tenant-id", required=True)
    ap.add_argument("--limit", type=int, default=50)
    a = ap.parse_args()
    s = Settings()
    if not s.database_url:
        sys.exit("Set CHAT_DATABASE_URL (source local.env.sh)")
    print(asyncio.run(run(s, OpenAICompatibleLLM(s), a.tenant_id, a.limit)))
