"""Answer context (PRD 7.2 step 4, 9.4): program catalogue first (authoritative for every number
and date), then curated Q&A, then document chunks; numbered sources within ~1,500 tokens."""

from __future__ import annotations

import re
from datetime import date

from .checks import Source
from .display import duration, money, period
from .guard import mask_pii
from .router import RouteDecision

CATALOGUE_INTENTS = {"fees", "dates", "eligibility", "programs", "process", "hostel"}
_FOLLOW_UP = re.compile(
    # Not "there" or "this": "Is there a pool?" and "this year" are not follow-ups.
    r"\b(it|its|it's|those|these|they|them|same|what about|how about|and the|for that)\b"
    r"|इसकी|इसका|उसकी|उसका|इसमें|उसमें",
    re.IGNORECASE,
)
MAX_CONTEXT_WORDS = 1100  # ~1,500 tokens


def _human_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%B')} {d.year}"


def _fee_name(component: str) -> str:
    """'tuition' -> 'Tuition fee'; a component already named a fee or deposit stays as it is."""
    c = (component or "fee").strip()
    named = c.lower().endswith(("fee", "fees", "deposit", "charges", "charge"))
    return (c if named else f"{c} fee")[:1].upper() + (c if named else f"{c} fee")[1:]


def catalogue_text(p: dict, intents: set[str]) -> str:
    lines = [
        f"{p['name']} ({p['level']}, {p.get('department') or 'all departments'}), academic year {p['academic_year']}."
        + (f" Also called: {', '.join(p['aliases'])}." if p.get("aliases") else "")
    ]
    if p.get("duration_months"):
        lines.append(f"Duration: {duration(p['duration_months'])}.")
    if p.get("intake"):
        lines.append(f"Intake: {p['intake']} seats.")
    if p.get("eligibility"):
        lines.append(f"Eligibility: {p['eligibility']}.")
    if p.get("fees") and intents & {"fees", "hostel", "programs", "process"}:
        for f in p["fees"]:
            # "Hostel fee (general category): ..." names the component and the category next
            # to the amount, so the sentence checks can tell one fee from another.
            lines.append(
                f"{_fee_name(f['component'])} ({f['category']} category): {money(f['amount'], f['currency'])}"
                + (f" {period(f.get('period'))}" if period(f.get("period")) else "")
                + "."
            )
    if p.get("dates") and intents & {"dates", "process", "programs"}:
        for d in p["dates"]:
            lines.append(f"{d['label']}: {_human_date(d['date'])}.")
    if p.get("contact"):
        lines.append(f"Contact: {p['contact']}.")
    return " ".join(lines)


def is_follow_up(text: str, history: list[dict]) -> bool:
    return any(h["sender"] == "user" for h in history) and (
        len(text.split()) <= 3 or bool(_FOLLOW_UP.search(text))
    )


RECENT_MESSAGES = 6  # the last three exchanges go into the prompt word for word
SUMMARY_QUESTIONS = 7  # earlier questions (of the PRD's 10 turns) are summarised


def conversation_summary(earlier: list[dict], programmes: list[str]) -> str | None:
    """F1.3 conversation summary without a model call (the latency budget allows one answer call,
    PRD 7.5): the visitor's earlier questions, shortened, and the programmes discussed. Personal
    details are masked; the model does not need them to answer."""
    questions = [
        mask_pii(" ".join(h["content"].split()))[:160]
        for h in earlier
        if h["sender"] == "user" and h["content"].strip()
    ][-SUMMARY_QUESTIONS:]
    parts = []
    if questions:
        parts.append("the visitor earlier asked: " + " | ".join(questions))
    if programmes:
        parts.append("programmes discussed: " + ", ".join(programmes))
    return "; ".join(parts) + "." if parts else None


def fee_summary(programs: list[dict]) -> str:
    lines = []
    for p in programs[:10]:
        fees = "; ".join(
            f"{_fee_name(f['component']).lower()} ({f['category']}) {money(f['amount'], f['currency'])}"
            + (f" {period(f.get('period'))}" if period(f.get("period")) else "")
            for f in p.get("fees", [])
        )
        lines.append(
            f"{p['name']} ({p['level']}{', ' + duration(p.get('duration_months')) if p.get('duration_months') else ''}): {fees or 'no fees listed'}."
        )
    return "Fees by program. " + _shared_fees(programs) + " ".join(lines)


def _shared_fees(programs: list[dict]) -> str:
    """A fee that is the same for every programme is said once, so the answer to "what is the
    hostel fee?" need not pick one programme as an example."""
    if len(programs) < 2:
        return ""
    amounts: dict[tuple, set] = {}
    for p in programs:
        for f in p.get("fees", []):
            key = (f["component"], f["category"], f["currency"], f.get("period"))
            amounts.setdefault(key, set()).add((p["code"], f["amount"]))
    out = []
    for (component, category, currency, per), seen in amounts.items():
        values = {amount for _, amount in seen}
        if len(values) == 1 and len({code for code, _ in seen}) == len(programs):
            out.append(
                f"The {_fee_name(component).lower()} ({category}) is {money(values.pop(), currency)}"
                f"{' ' + period(per) if period(per) else ''} for every program."
            )
    return " ".join(out) + (" " if out else "")


def retrieval_query(text: str, history: list[dict]) -> str:
    """F1.3: a short or pronoun-led follow-up ("what about its fees?") is searched together with
    the previous question, so it resolves to the same program or topic."""
    if not history:
        return text
    last_user = next(
        (h["content"] for h in reversed(history) if h["sender"] == "user"), ""
    )
    if last_user and (len(text.split()) <= 3 or _FOLLOW_UP.search(text)):
        return f"{last_user} {text}"
    return text


def build_sources(
    decision: RouteDecision,
    catalogue: dict,
    search_results: list[dict],
    max_sources: int,
) -> list[Source]:
    intents = {r.intent for r in decision.routes}
    sources: list[Source] = []
    # Every source knows the programme list, so a number can be tied to its programme.
    programs = catalogue.get("items", [])
    progs = decision.programs[:2]
    if intents & CATALOGUE_INTENTS or decision.programs:
        for p in progs:
            sources.append(
                Source(
                    len(sources) + 1,
                    catalogue_text(p, intents),
                    {
                        "source_type": "catalogue",
                        "title": f"Program catalogue {p['academic_year']}: {p['name']}",
                        "academic_year": p["academic_year"],
                        "ref_id": p["code"],
                    },
                    programs,
                )
            )
        items = catalogue.get("items", [])
        if not progs and items and intents & {"fees", "programs"}:
            # "What are the fees?" names no program: one compact summary source, so the catalogue
            # never crowds curated answers and documents out of the context.
            sources.append(
                Source(
                    len(sources) + 1,
                    fee_summary(items),
                    {
                        "source_type": "catalogue",
                        "title": f"Program catalogue {items[0]['academic_year']}: fees",
                        "academic_year": items[0]["academic_year"],
                        "ref_id": "fees",
                    },
                    programs,
                )
            )
        general = catalogue.get("general_dates", [])
        if general and intents & {"dates", "process"}:
            year = general[0]["academic_year"]
            text = " ".join(f"{d['label']}: {_human_date(d['date'])}." for d in general)
            sources.append(
                Source(
                    len(sources) + 1,
                    f"Admission calendar {year}. {text}",
                    {
                        "source_type": "catalogue",
                        "title": f"Admission calendar {year}",
                        "academic_year": year,
                        "ref_id": "calendar",
                    },
                    programs,
                )
            )
    seen: set[str] = set()
    words = sum(len(s.text.split()) for s in sources)
    for item in search_results:
        if len(sources) >= max_sources or item["id"] in seen:
            continue
        text = item["content"]
        if words + len(text.split()) > MAX_CONTEXT_WORDS:
            continue
        seen.add(item["id"])
        words += len(text.split())
        c = item["citation"]
        sources.append(
            Source(
                len(sources) + 1,
                text,
                {
                    "source_type": c["source_type"],
                    "title": c["title"],
                    "academic_year": c.get("academic_year"),
                    "heading_path": c.get("heading_path"),
                    "url": c.get("url"),
                    "ref_id": c.get("qa_id") or c.get("document_id"),
                    "vector_score": item.get("vector_score"),
                },
                programs,
            )
        )
    return sources


def sources_block(sources: list[Source]) -> str:
    out = []
    for s in sources:
        where = s.citation["title"]
        if s.citation.get("heading_path"):
            where += f" > {s.citation['heading_path']}"
        out.append(f"[{s.n}] ({where})\n{s.text}")
    return "\n\n".join(out)
