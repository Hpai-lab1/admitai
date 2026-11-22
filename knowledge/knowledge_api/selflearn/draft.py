"""Stage 5, draft the lesson. Each lesson becomes a proposal in learn.proposals, which is what
the staff approval inbox shows, what the evaluation gate tests and what rollback undoes.

| Lesson               | Proposal kind        | Live without a person? |
|----------------------|----------------------|------------------------|
| alias                | search_alias         | after the gate         |
| retrieval_boost      | retrieval_boost      | after the gate         |
| curated_from_source  | curated_answer       | after the gate, if it quotes no amount the catalogue lacks |
| catalogue_reextract  | catalogue_reextract  | never: protected values wait for approval |
| answer_hint          | prompt               | never: applied by Module B's owner |
"""

from __future__ import annotations

import re

from .. import health
from .evidence import Passage

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

KIND = {
    "alias": ("search_alias", "retrieval_miss", 2, 2),
    "retrieval_boost": ("retrieval_boost", "retrieval_miss", 2, 2),
    "curated_from_source": ("curated_answer", "knowledge_gap", 1, 2),
    "catalogue_reextract": ("catalogue_reextract", "wrong_fact", 1, 3),
    "answer_hint": ("prompt", "tone", 4, 1),
}


def numbers_supported(text: str, source: str) -> bool:
    """Every figure in `text` appears in `source` (the same check drafting.py makes)."""
    have = {n.replace(",", "") for n in _NUMBER.findall(source or "")}
    return all(n.replace(",", "") in have for n in _NUMBER.findall(text or ""))


def _ev(p: Passage) -> dict:
    return {
        "title": p.title,
        "url": p.url,
        "quote": p.quote,
        "document_id": p.document_id,
    }


def retrieval_lesson(
    question: str, passage: Passage, alias: tuple[str, str] | None
) -> dict:
    """An alias when the visitor's term is the only thing missing and the page uses the
    expansion; otherwise a boost of the evidence passage (with the alias, if there is one)."""
    page_words = passage.content.lower()
    use_alias = (
        alias and alias[1].lower() in page_words and alias[0] in question.lower()
    )
    t = "alias" if use_alias and passage.rank <= 3 else "retrieval_boost"
    payload = {
        "question": question,
        "chunk_id": passage.chunk_id,
        "document_url": passage.url,
        "title": passage.title,
        "quote": passage.quote,
        "evidence_rank_before": passage.rank,
    }
    if use_alias:
        payload["alias"] = {"phrase": alias[0], "expansion": alias[1]}
    return {"type": t, "payload": payload}


def curated_lesson(question: str, passage: Passage) -> dict:
    """The fallback when a boost fails the gate: a curated answer that is the page's own sentence."""
    return {
        "type": "curated_from_source",
        "payload": {
            "question": question,
            "answer": passage.quote,
            "title": passage.title,
            "document_url": passage.url,
            "quote": passage.quote,
            "chunk_id": passage.chunk_id,
        },
    }


def proposal(lesson: dict, examples: list[dict], cur=None) -> dict:
    """The learn.proposals fields for a lesson."""
    t, pl = lesson["type"], lesson["payload"]
    kind, cause, rung, severity = KIND[t]
    q = (pl.get("question") or "")[:90]
    src = f"'{pl.get('title')}'" if pl.get("title") else "the college's page"
    lid = str(lesson["id"])
    if t == "alias":
        a = pl["alias"]
        title = f"Learnt: '{a['phrase']}' means '{a['expansion']}' ({q})"
        text = (
            f"Visitors call it '{a['phrase']}'; {src} says '{a['expansion']}'. Search adds the page's "
            f"words to questions that use the visitor's. Evidence: \"{pl['quote']}\""
        )
        diff = {"lesson_id": lid, "question": pl["question"], **a, "evidence": pl}
    elif t == "retrieval_boost":
        title = f"Learnt: find {src} for '{q}'"
        text = (
            f"Questions like this one are answered from {src}, which says: \"{pl['quote']}\". "
            "Search puts that passage first for them."
        )
        diff = {"lesson_id": lid, **pl}
    elif t == "curated_from_source":
        title = f"Learnt answer from {src}: {q}"
        text = pl["answer"]
        diff = {
            "lesson_id": lid,
            "question": pl["question"],
            "answer": pl["answer"],
            "old_answer": (examples[-1] if examples else {}).get("answer"),
            "evidence": {k: pl[k] for k in ("title", "document_url", "quote")},
        }
    elif t == "catalogue_reextract":
        ch = pl.get("fee_changes") or []
        ic = pl.get("intake_change")
        what = (
            "; ".join(
                [
                    f"{c['program']} {c['category']} fee {c['old']} → {c['new']}"
                    for c in ch
                ]
                + ([f"{ic['program']} intake {ic['old']} → {ic['new']}"] if ic else [])
            )
            or "values on the programme page"
        )
        title = f"Catalogue differs from the programme page: {what}"[:200]
        text = (
            f"A visitor reported a wrong value. The programme page was fetched again "
            f"({pl.get('url')}) and re-extracted: {pl.get('reason')}. Approving copies the page's "
            "values into the catalogue, after the evaluation gate."
        )
        diff = {"lesson_id": lid, **pl}
    else:  # answer_hint
        title = f"Answer style ({len(examples)} visitors): {q}"
        text = pl.get("hint") or "Visitors found these answers hard to follow."
        diff = {"lesson_id": lid, "question": pl.get("question"), "suggestion": text}
    auto = t in ("alias", "retrieval_boost") or (
        t == "curated_from_source"
        and cur is not None
        and not health.conflicts_in(cur, f"{pl['question']} {pl['answer']}")
        and numbers_supported(pl["answer"], pl["quote"])
    )
    return {
        "kind": kind,
        "root_cause": cause,
        "rung": rung,
        "severity": severity,
        "title": title,
        "proposal": text,
        "diff": diff,
        "examples": examples[-5:],
        "auto_ok": auto,
    }
