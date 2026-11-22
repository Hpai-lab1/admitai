"""Stage 3, verify against evidence (mandatory). The only sources of truth are the college's own
crawled pages, documents and the catalogue extracted from them.

- Retrieval miss / missing answer: search the college's indexed pages and documents for a passage
  that answers the question; the quoted span must be copied word for word from it. A page whose
  copy is stale is fetched again; if only the fresh page answers, the lesson goes to a person
  (the page must be crawled again before search can use it).
- Wrong fact claim: fetch the programme's own page again and re-extract it with the crawler's
  extractor (site_kb.programme). The page decides: it supports the claim (the lesson is a
  re-extraction, with the page's value, never the visitor's), contradicts it (rejected) or cannot
  tell (a person decides). A number from feedback is never written anywhere.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Callable, Protocol
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from .. import health, ingest, paraphrase, search
from ..config import Settings
from ..db import tx
from ..embedder import Embedder

STOP = set(
    "a an the of to in on for and or is are was were be been do does did what which who whom "
    "when where why how i me my we our you your it its this that these those there here can could "
    "will would shall should may might must have has had at by from with about as into than then "
    "so if not no yes any some all please tell know want need get give much many is there "
    "college university ruas".split()
)
_WORD = re.compile(r"[a-z0-9]+")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def words(text: str) -> set[str]:
    return {
        w for w in _WORD.findall((text or "").lower()) if w not in STOP and len(w) > 1
    }


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+|;\s+", text or "")
    return [p.strip() for p in parts if len(p.strip()) >= 8]


def norm_ws(t: str) -> str:
    return " ".join((t or "").split())


def is_verbatim(quote: str, source: str) -> bool:
    return bool(quote) and norm_ws(quote).lower() in norm_ws(source).lower()


def digest(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()[:32]


@dataclass
class Passage:
    chunk_id: str
    document_id: str | None
    title: str
    url: str | None
    content: str
    quote: str
    rank: int  # position in today's search for the question (0 = first)
    overlap: float
    content_hash: str


class EvidenceModel(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


PICK_SYSTEM = (
    "You check whether a college's own web pages answer a visitor's question. Use ONLY the "
    'numbered passages. Reply with JSON only: {"passage": the number of the passage that '
    'answers the question, or 0 if none does, "quote": the sentence that answers it, copied '
    "exactly from that passage}."
)


def _best_sentence(question: str, content: str) -> tuple[str, float]:
    q = words(paraphrase.expand(question))
    best, score = "", 0.0
    for s in sentences(content):
        w = words(s)
        if not w:
            continue
        sc = len(q & w) / max(len(q), 1)
        if sc > score:
            best, score = s, sc
    return best, score


def find_passage(
    settings: Settings,
    embedder: Embedder,
    tenant: str,
    question: str,
    model: EvidenceModel | None = None,
    min_overlap: float = 0.5,
    exclude_docs: frozenset[str] | set[str] = frozenset(),
) -> Passage | None:
    """The indexed passage of the college's own pages or documents that answers the question.
    `exclude_docs`: documents the disliked answers cited; visitors said they were not the answer."""
    found = search.search(
        settings.database_url, embedder, tenant, question, top_k=10, lessons=False
    )
    ranked = [i for i in found["items"] if i["kind"] == "chunk"]
    pos = {i["id"]: n for n, i in enumerate(found["items"])}
    items = [i for i in ranked if i["citation"]["document_id"] not in exclude_docs]
    if not items:
        return None
    q_words = words(paraphrase.expand(question))
    scored = []
    for it in items:
        rank = pos[it["id"]]
        quote, ov = _best_sentence(question, it["content"])
        whole = len(q_words & words(it["content"])) / max(len(q_words), 1)
        scored.append((rank, it, quote, max(ov, whole)))
    chosen = None
    if model is not None:
        block = "\n\n".join(
            f"[{n}] {it['citation']['title']}: {it['content'][:1200]}"
            for n, (_, it, _, _) in enumerate(scored[:6], 1)
        )
        try:
            import json

            reply = model.complete(
                PICK_SYSTEM, f"Passages:\n{block}\n\nQuestion: {question}"
            )
            reply = re.sub(r"<think>.*?</think>", "", reply or "", flags=re.S)
            data = json.loads(re.search(r"\{.*\}", reply, flags=re.S).group())
            n = int(data.get("passage") or 0)
            quote = str(data.get("quote") or "")
            if 1 <= n <= min(6, len(scored)):
                rank, it, _, ov = scored[n - 1]
                if is_verbatim(quote, it["content"]):
                    chosen = (rank, it, norm_ws(quote), ov)
        except Exception:  # noqa: BLE001 - the word-overlap check below decides
            chosen = None
    if chosen is None:
        candidates = [
            s
            for s in scored
            if s[3] >= min_overlap
            and len(q_words & words(s[1]["content"])) >= 2
            and s[2]
        ]
        if not candidates:
            return None
        chosen = max(candidates, key=lambda s: (s[3], -s[0]))
    rank, it, quote, ov = chosen
    c = it["citation"]
    return Passage(
        chunk_id=it["id"],
        document_id=c["document_id"],
        title=c["title"],
        url=c.get("url"),
        content=it["content"],
        quote=quote,
        rank=rank,
        overlap=round(ov, 3),
        content_hash=digest(it["content"]),
    )


# ------------------------------------------------------------------ fetching a page again


@dataclass
class Fetched:
    url: str
    title: str
    blocks: list
    text: str
    content_hash: str


Fetcher = Callable[[str], "Fetched | None"]
USER_AGENT = "AdmitAI-KnowledgeBot/0.1"


def http_fetcher(timeout: float = 20.0) -> Fetcher:
    """Fetch one page politely: robots.txt is obeyed, HTML only."""

    def fetch(url: str) -> Fetched | None:
        try:
            with httpx.Client(
                timeout=timeout,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                host = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
                robots = RobotFileParser()
                r = client.get(urljoin(host, "/robots.txt"))
                robots.parse(r.text.splitlines() if r.status_code == 200 else [])
                if not robots.can_fetch(USER_AGENT, url):
                    return None
                resp = client.get(url)
                if resp.status_code != 200 or "html" not in resp.headers.get(
                    "content-type", "html"
                ):
                    return None
                blocks, title, _links = ingest.parse_html(resp.text)
        except httpx.HTTPError:
            return None
        text = "\n".join(b.text for b in blocks)
        return Fetched(str(resp.url), title or url, blocks, text, digest(text))

    return fetch


def stale(settings: Settings, tenant: str, document_id: str | None) -> str | None:
    """The page address of a document whose copy is older than LEARNING_REFETCH_AFTER_H."""
    if not document_id:
        return None
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select url from kb.documents where id::text = %s and url like 'http%%'"
            " and updated_at < now() - make_interval(secs => %s)",
            (document_id, settings.learning_refetch_after_h * 3600),
        )
        row = cur.fetchone()
    return row["url"] if row else None


def fresh_passage(question: str, page: Fetched, min_overlap: float = 0.5) -> str | None:
    quote, ov = _best_sentence(question, page.text)
    return (
        quote
        if ov >= min_overlap and len(words(question) & words(quote)) >= 2
        else None
    )


# ------------------------------------------------------------------ fact claims

PROTECTED = ("fee", "intake", "date", "credits", "eligibility", "duration")
_CURRENCY = re.compile(
    r"(?:₹|rs\.?|inr|rupees?)\s*(\d[\d,]*(?:\.\d+)?)\s*(lakhs?|lacs?|l\b|k\b|thousand)?"
    r"|(\d[\d,]*(?:\.\d+)?)\s*(lakhs?|lacs?|thousand|rupees|inr)",
    re.I,
)
_INTAKE = re.compile(
    r"(\d{1,4})\s*(?:seats?|students?\b)|intake\D{0,20}(\d{1,4})", re.I
)
_FIELD_WORDS = {
    "fee": re.compile(r"\bfees?|tuition|cost|price|₹|\brs\b|inr|lakh|rupee", re.I),
    "intake": re.compile(r"\bintake|seats?\b", re.I),
    "date": re.compile(r"deadline|last date|closes?|opens?|\bdate\b", re.I),
    "credits": re.compile(r"\bcredits?\b", re.I),
    "eligibility": re.compile(r"eligib|marks|percent|%|cut-?off", re.I),
    "duration": re.compile(r"duration|\byears? (course|programme|program)", re.I),
}


def _money(m: re.Match) -> Decimal | None:
    raw = (m.group(1) or m.group(3) or "").replace(",", "")
    unit = (m.group(2) or m.group(4) or "").lower()
    try:
        v = Decimal(raw)
    except InvalidOperation:
        return None
    if unit.startswith(("lakh", "lac")) or unit == "l":
        v *= 100000
    elif unit in ("k", "thousand"):
        v *= 1000
    return v.normalize()


@dataclass
class Claim:
    field: str  # fee | intake | date | credits | eligibility | duration | other
    program: dict | None
    value: Decimal | int | None
    text: str


def parse_claims(cur, claim: str, question: str = "") -> list[Claim]:
    """The protected values a visitor's claim is about, tied to a programme when it names one
    (in the claim or in the question it was about)."""
    cur.execute("select * from kb.programs where status = 'approved'")
    programmes = cur.fetchall()
    named = [p for p in programmes if health._mentions(claim, p)] or [
        p for p in programmes if health._mentions(question or "", p)
    ]
    program = named[0] if len(named) == 1 else None
    out: list[Claim] = []
    for m in _CURRENCY.finditer(claim):
        v = _money(m)
        if v is not None:
            out.append(Claim("fee", program, v, claim))
    if not out and _FIELD_WORDS["fee"].search(claim):
        for n in _NUMBER.findall(claim):
            try:
                v = Decimal(n.replace(",", "")).normalize()
            except InvalidOperation:
                continue
            if not 1990 <= v <= 2099:
                out.append(Claim("fee", program, v, claim))
    for m in _INTAKE.finditer(claim):
        if _FIELD_WORDS["intake"].search(claim):
            out.append(Claim("intake", program, int(m.group(1) or m.group(2)), claim))
    if not out:
        fieldname = next(
            (k for k, rx in _FIELD_WORDS.items() if rx.search(claim)), "other"
        )
        out.append(Claim(fieldname, program, None, claim))
    return out


@dataclass
class FactVerdict:
    decision: str  # supported | contradicted | page_changed | unverifiable
    reason: str
    evidence: list[dict] = field(
        default_factory=list
    )  # quote, url, supports, content_hash
    changes: dict = field(
        default_factory=dict
    )  # fee_changes / intake_change, values from the page


def _quote_with(page: Fetched, value) -> str | None:
    digits = re.sub(r"\D", "", str(value).split(".")[0])
    for b in page.blocks:
        t = b.text
        for n in _NUMBER.findall(t):
            if re.sub(r"\D", "", n.split(".")[0]) == digits:
                return norm_ws(t)[:500]
    return None


def verify_fact(
    settings: Settings, tenant: str, claim_text: str, question: str, fetch: Fetcher
) -> FactVerdict:
    from .. import site_kb

    with tx(settings.database_url, tenant) as cur:
        claims = parse_claims(cur, claim_text, question)
        c = next((x for x in claims if x.value is not None), claims[0])
        if c.value is None or c.field not in ("fee", "intake"):
            return FactVerdict(
                "unverifiable",
                f"the claim names no {c.field if c.field != 'other' else 'specific'} value "
                "that can be checked against a programme page automatically",
            )
        if c.program is None:
            return FactVerdict(
                "unverifiable", "the claim does not name exactly one programme"
            )
        p = c.program
        cur.execute(
            "select id::text, category, component, amount from kb.program_fees"
            " where program_id = %s and (valid_to is null or valid_to >= current_date)"
            " order by category, component",
            (p["id"],),
        )
        fees = cur.fetchall()
    if not p.get("source_url"):
        return FactVerdict(
            "unverifiable", f"{p['name']} has no source page to check against"
        )
    page = fetch(p["source_url"])
    if page is None:
        return FactVerdict(
            "unverifiable", f"the page {p['source_url']} could not be fetched"
        )
    sk_page = site_kb.Page(page.url, page.title, page.blocks, content=page.blocks)
    extracted = site_kb.programme(sk_page, p["academic_year"])
    if extracted is None:
        return FactVerdict(
            "unverifiable", "the page no longer reads as a programme page"
        )
    prog, page_fees = extracted
    if c.field == "intake":
        page_v, cat_v = prog.get("intake"), p.get("intake")
        ev = []
        if page_v is not None:
            q = _quote_with(page, page_v)
            ev.append(
                {
                    "url": page.url,
                    "quote": q or f"Intake: {page_v}",
                    "supports": page_v == c.value,
                    "content_hash": page.content_hash,
                }
            )
        if page_v is None:
            return FactVerdict("unverifiable", "the page states no intake", ev)
        if page_v == c.value and page_v != cat_v:
            return FactVerdict(
                "supported",
                f"the page says {page_v} seats; the catalogue has {cat_v}",
                ev,
                {
                    "intake_change": {
                        "program_id": str(p["id"]),
                        "code": p["code"],
                        "program": p["name"],
                        "old": cat_v,
                        "new": page_v,
                    }
                },
            )
        if page_v == cat_v:
            return FactVerdict(
                "contradicted",
                f"the page says {page_v} seats, as the catalogue does",
                ev,
            )
        return FactVerdict(
            "page_changed",
            f"the page says {page_v} seats: neither the claim ({c.value}) nor the catalogue ({cat_v})",
            ev,
            {
                "intake_change": {
                    "program_id": str(p["id"]),
                    "code": p["code"],
                    "program": p["name"],
                    "old": cat_v,
                    "new": page_v,
                }
            },
        )
    # fees
    page_amounts = {Decimal(f["amount"]).normalize(): f for f in page_fees}
    cat_amounts = {Decimal(f["amount"]).normalize(): f for f in fees}
    ev = [
        {
            "url": page.url,
            "quote": _quote_with(page, a) or f"{f['category']}: {a}",
            "supports": a == c.value,
            "content_hash": page.content_hash,
        }
        for a, f in page_amounts.items()
    ][:6]
    if not page_amounts:
        return FactVerdict("unverifiable", "the page states no fee", ev)
    if c.value in page_amounts and c.value not in cat_amounts:
        pf = page_amounts[c.value]
        target = next(
            (f for f in fees if f["category"].lower() == str(pf["category"]).lower()),
            fees[0] if len(fees) == 1 else None,
        )
        if target is None:
            return FactVerdict(
                "unverifiable",
                "the page has this fee but it cannot be tied to one catalogue fee",
                ev,
            )
        return FactVerdict(
            "supported",
            f"the page lists {pf['amount']} ({pf['category']}); the catalogue has {target['amount']}",
            ev,
            {
                "fee_changes": [
                    {
                        "fee_id": target["id"],
                        "program": p["name"],
                        "code": p["code"],
                        "component": target["component"],
                        "category": target["category"],
                        "old": _plain(target["amount"]),
                        # The page's value, never the visitor's.
                        "new": _plain(pf["amount"]),
                    }
                ]
            },
        )
    if c.value not in page_amounts and set(cat_amounts) & set(page_amounts):
        return FactVerdict(
            "contradicted",
            f"the page lists {', '.join(_plain(a) for a in page_amounts)}, as the catalogue does; "
            f"not {_plain(c.value)}",
            ev,
        )
    return FactVerdict(
        "page_changed",
        "the page's fees differ from the catalogue and from the claim",
        ev,
    )


def _plain(amount) -> str:
    d = Decimal(str(amount)).normalize()
    return f"{d:f}".rstrip("0").rstrip(".") if "." in f"{d:f}" else f"{d:f}"
