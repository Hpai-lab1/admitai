"""A knowledge base that builds itself from the college's website (PRD F4.2, extended).

    python -m knowledge_api.site_kb --tenant-id <uuid> --start-url https://www.example.ac.in/ \
        --include 'https://www.example.ac.in/course/*' --include 'https://www.example.ac.in/admissions/*'

Nothing about one college is written into the code; everything comes from the pages:

1. Discover pages from the site's sitemap (named in robots.txt), filtered by include/exclude
   patterns and robots.txt; without a sitemap, follow links from the start page.
2. Fetch them politely and drop the site's furniture: text blocks that open or close most pages
   (menus, footers) are learned from the crawl itself, so only each page's own content is indexed.
3. Index every page as a document (chunked, embedded in both models) and approve it, so search
   finds it at once. A changed page becomes a new version; an unchanged one is skipped.
4. Derive the structured knowledge from the same pages:
   - the programme catalogue (name, level, duration, faculty/department, eligibility, intake,
     fees) from pages that have a title and sections such as "Eligibility" or "Fee Structure";
   - curated answers from FAQ pages (a heading that asks a question, answered below it).
   Every value is copied from the page text, never generated, so a fee can only be one the site
   states. The catalogue is replaced only when the crawl found at least as many programmes as
   MIN_SHARE of the current catalogue, so a broken crawl cannot empty it.
5. Regenerate the catalogue test cases, so the evaluation gate tests the new catalogue.
6. Tables are indexed row by row, each value next to its column heading; a programme page with a
   credits table per semester also gets a curriculum summary (curriculum.py); same-site files the
   pages link to (PDF, DOCX, PPTX, XLSX) are indexed as documents.

Run nightly (scripts/nightly_local.sh) for every tenant whose pack.yaml has `website:`.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Callable
from urllib.parse import unquote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from . import catalogue, curated, curriculum, documents, ingest, search
from .config import Settings
from .crawl import USER_AGENT, _allowed, _fetch
from .db import Jsonb, tx
from .embedder import Embedder, make_embedder

MIN_SHARE = (
    0.5  # replace the catalogue only if the crawl found >= half as many programmes
)
BOILERPLATE_SHARE = (
    0.4  # a block opening/closing at least this share of pages is furniture
)
POLITE_DELAY_S = 0.3
ACTOR = "website-sync"


@dataclass
class Page:
    url: str
    title: str
    blocks: list[ingest.Block]
    content: list[ingest.Block] = field(default_factory=list)  # without the furniture
    links: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ discovery and fetching


def _sitemap_urls(
    client: httpx.Client, robots_text: str, start: str, sleep, depth: int = 0
) -> list[str]:
    maps = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots_text) or [
        urljoin(start, "/sitemap.xml")
    ]
    out: list[str] = []
    for m in maps:
        out += _read_sitemap(client, m, sleep, depth)
    return list(dict.fromkeys(out))


def _read_sitemap(client: httpx.Client, url: str, sleep, depth: int) -> list[str]:
    r = _fetch(client, url, sleep)
    if r is None or r.status_code != 200:
        return []
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)
    if "<sitemapindex" in r.text and depth < 2:  # an index of sitemaps
        return [u for m in locs for u in _read_sitemap(client, m, sleep, depth + 1)]
    return locs


def discover(
    client: httpx.Client,
    start: str,
    include: list[str],
    exclude: list[str],
    max_pages: int,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[str], RobotFileParser]:
    host = urlparse(start).netloc
    robots = RobotFileParser()
    r = _fetch(client, urljoin(start, "/robots.txt"), sleep)
    text = r.text if r is not None and r.status_code == 200 else ""
    robots.parse(text.splitlines())
    urls = [
        u
        for u in _sitemap_urls(client, text, start, sleep)
        if urlparse(u).netloc == host
        and _allowed(u, include, exclude)
        and robots.can_fetch(USER_AGENT, u)
    ]
    return (urls or [start])[:max_pages], robots


def listing_urls(
    client: httpx.Client,
    listings: list[dict],
    host: str,
    include: list[str],
    exclude: list[str],
    robots: RobotFileParser,
    sleep: Callable[[float], None] = time.sleep,
    delay: float = POLITE_DELAY_S,
) -> list[str]:
    """Pages a site lists only through its own listing request, not in its sitemap.

    RUAS (19 Nov 2026): www.msruas.ac.in/programme fills its A-Z course list with
    POST programme/showProgramsDataAjax, and the sitemap still names old course addresses that
    return 404 (B.Tech. AI & ML, AI & Data Science, VLSI and 25 more were never read). A pack
    describes such a request:

        listings:
          - url: https://www.msruas.ac.in/programme/showProgramsDataAjax
            form: {fac_school: "", department: "", load: "", limit: 12}
            vary: {programid: [1, 2, 3, 4]}     # one listing per value
            page_field: page                     # 0, 1, 2 ... until no new link comes back

    The links found (same site, allowed by include / exclude and robots.txt) are returned."""
    found: list[str] = []
    for spec in listings or []:
        url = spec.get("url")
        if (
            not url
            or urlparse(url).netloc != host
            or not robots.can_fetch(USER_AGENT, url)
        ):
            continue
        field, values = next(iter((spec.get("vary") or {"_": [None]}).items()))
        for value in values:
            for page in range(int(spec.get("max_pages", 30))):
                form = {k: str(v) for k, v in (spec.get("form") or {}).items()}
                if value is not None:
                    form[field] = str(value)
                if spec.get("page_field"):
                    form[spec["page_field"]] = str(page)
                try:
                    r = client.post(url, data=form, headers={"User-Agent": USER_AGENT})
                except httpx.HTTPError:
                    break
                sleep(delay)
                links = [
                    urljoin(url, h)
                    for h in re.findall(
                        r'href="([^"#?]+)"', r.text if r.status_code == 200 else ""
                    )
                ]
                new = [
                    u
                    for u in links
                    if urlparse(u).netloc == host
                    and _allowed(u, include, exclude)
                    and robots.can_fetch(USER_AGENT, u)
                    and u not in found
                ]
                if not new:
                    break
                found += new
                if not spec.get("page_field"):
                    break
    return found


def fetch_pages(
    client: httpx.Client,
    urls: list[str],
    sleep: Callable[[float], None] = time.sleep,
    delay: float = POLITE_DELAY_S,
) -> tuple[list[Page], int]:
    pages, failed = [], 0
    for url in urls:
        resp = _fetch(client, url, sleep)
        if (
            resp is None
            or resp.status_code != 200
            or "html" not in resp.headers.get("content-type", "html")
        ):
            failed += 1
            continue
        blocks, title, links = ingest.parse_html(resp.text)
        final = str(resp.url)
        pages.append(
            Page(final, title or url, blocks, links=[urljoin(final, h) for h in links])
        )
        sleep(delay)
    return pages, failed


def more_urls(
    pages: list[Page], include: list[str], exclude: list[str], host: str, robots
) -> list[str]:
    """Pages the crawl links to that the sitemap did not list (a renamed programme page is
    linked from the programme listings, while the sitemap still names its old address)."""
    have = {p.url.rstrip("/") for p in pages}
    out = []
    for p in pages:
        for u in p.links:
            u = u.split("#")[0]
            if (
                urlparse(u).netloc == host
                and u.rstrip("/") not in have
                and _allowed(u, include, exclude)
                and robots.can_fetch(USER_AGENT, u)
            ):
                have.add(u.rstrip("/"))
                out.append(u)
    return out


# Files a page links to (a programme's regulations, the fee notice, the prospectus) are indexed
# too. The include patterns choose pages, not files: a college keeps its PDFs under /uploads/ or
# /assets/, so a linked file is read when a page that was read links to it on the same site.
DOC_TYPES = {".pdf", ".docx", ".pptx", ".xlsx"}
MAX_DOCUMENTS = 150
MAX_DOCUMENT_MB = 40
DOC_RECHECK_DAYS = 7  # a file already indexed is downloaded again at most once a week


def _doc_name(url: str) -> str:
    return unquote(urlparse(url).path.rsplit("/", 1)[-1])


def linked_documents(
    pages: list[Page], host: str, robots, exclude: list[str], limit: int
) -> list[str]:
    """Same-site files of the types ingest reads that the read pages link to, in page order."""
    out: dict[str, None] = {}
    for p in pages:
        for u in p.links:
            u = u.split("#")[0].strip()
            suffix = (
                "." + _doc_name(u).rsplit(".", 1)[-1].lower()
                if "." in _doc_name(u)
                else ""
            )
            if (
                suffix in DOC_TYPES
                and urlparse(u).netloc == host
                and _allowed(u, [], exclude)
                and robots.can_fetch(USER_AGENT, u)
            ):
                out.setdefault(u, None)
    return list(out)[:limit]


def fetch_document(
    client: httpx.Client, url: str, max_bytes: int, sleep: Callable[[float], None]
) -> bytes | str:
    """The file's bytes, or why it was not read ('too large', 'not found')."""
    for attempt in range(3):
        try:
            with client.stream(
                "GET",
                url,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
                timeout=60,
            ) as r:
                if r.status_code >= 500:
                    raise httpx.HTTPError(str(r.status_code))
                if r.status_code != 200:
                    return "not found"
                if int(r.headers.get("content-length") or 0) > max_bytes:
                    return "too large"
                data = bytearray()
                for part in r.iter_bytes():
                    data += part
                    if len(data) > max_bytes:
                        return "too large"
                if "html" in r.headers.get("content-type", ""):
                    return "not a file"  # a soft 404 page in place of the file
                return bytes(data)
        except httpx.HTTPError:
            sleep(2**attempt)
    return "not found"


# ------------------------------------------------------------------ site furniture


def _key(b: ingest.Block) -> str:
    return f"{b.level}|{' '.join(b.text.split()).lower()}"


def strip_furniture(pages: list[Page], share: float = BOILERPLATE_SHARE) -> None:
    """Drop the runs of blocks that open and close most pages (menus, footers). Only the runs at
    the start and end are dropped: a sentence repeated inside many pages (an eligibility rule
    shared by many programmes) is content and stays."""
    if not pages:
        return
    seen = Counter(k for p in pages for k in {_key(b) for b in p.blocks})
    common = {k for k, n in seen.items() if n >= max(3, share * len(pages))}
    for p in pages:
        lo, hi = 0, len(p.blocks)
        while lo < hi and _key(p.blocks[lo]) in common:
            lo += 1
        while hi > lo and _key(p.blocks[hi - 1]) in common:
            hi -= 1
        p.content = p.blocks[lo:hi]


# ------------------------------------------------------------------ structured knowledge

_AMOUNT = re.compile(r"(?:rs\.?|inr|₹)\s*(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
# Inside a fee section, an amount may be written without "Rs" ("4,50,000"): Indian or Western
# digit grouping, or five or more digits, so a year (2026) is never read as a fee.
_BARE_AMOUNT = re.compile(r"(?<![\d,.])(\d{1,3}(?:,\d{2,3})+|\d{5,})(?![\d,])")
_YEAR = re.compile(r"(20\d\d)\s*[–\-/]\s*(\d\d)(?!\d)")
_DURATION = re.compile(
    r"(\d+(?:\.\d+)?)\s*(years?|yrs?|months?|semesters?)", re.IGNORECASE
)
_LEVELS = [  # the name's first word decides the level
    (re.compile(r"^(ph\.?\s?d|doctor)", re.I), "PhD"),
    (re.compile(r"^(pg\s*diploma|diploma|certificate|b\.?\s?voc)", re.I), "Diploma"),
    (re.compile(r"^(m\.?\s?[a-z]|master|mba|mca|llm|md\b|ms\b)", re.I), "PG"),
    (re.compile(r"^(b\.?\s?[a-z]|bachelor|mbbs|llb|bba|bca|bds)", re.I), "UG"),
]


def _sections(blocks: list[ingest.Block]) -> list[tuple[str, list[ingest.Block]]]:
    """(heading, blocks under it) for every heading below the page title."""
    out: list[tuple[str, list[ingest.Block]]] = []
    for b in blocks:
        if b.level >= 2:
            out.append((b.text.strip(), []))
        elif out:
            out[-1][1].append(b)
    return out


def _section(blocks, pattern: str) -> tuple[str, list[ingest.Block]] | None:
    """The first section whose heading matches: everything up to the next heading of the same
    level (or the page title). Headings of other levels inside it are sub-sections ("Indian
    Nationals", "Lateral Entry" under "Eligibility Criteria") and are kept as text; a sibling
    heading that also matches ("Fee Structure" then "Fee Structure 2026–27") continues it."""
    for i, b in enumerate(blocks):
        if b.level < 2 or not re.search(pattern, b.text, re.I):
            continue
        heads, body = [b.text.strip()], []
        for nb in blocks[i + 1 :]:
            if nb.level == 1:
                break
            if nb.level == b.level:
                if re.search(pattern, nb.text, re.I):
                    heads.append(nb.text.strip())
                    continue
                break
            body.append(ingest.Block(nb.text.strip() + ":", 0) if nb.level >= 2 else nb)
        return " / ".join(heads), body
    return None


def _label_value(blocks: list[ingest.Block], labels: tuple[str, ...]) -> str | None:
    """A value the page shows as a label line followed by its value ("Study" / "4 Years")."""
    for i, b in enumerate(blocks[:-1]):
        if b.level == 0 and b.text.strip().lower() in labels:
            nxt = blocks[i + 1]
            if nxt.level == 0 and nxt.text.strip():
                return nxt.text.strip()
    return None


def _months(text: str | None) -> int | None:
    m = _DURATION.search(text or "")
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2).lower()
    if unit.startswith(("year", "yr")):
        return round(n * 12)
    if unit.startswith("sem"):
        return round(n * 6)
    return round(n)


def _level(name: str) -> str | None:
    for pattern, level in _LEVELS:
        if pattern.search(name.strip()):
            return level
    return None


_ACRONYM = re.compile(r"\(([A-Z]{2,6})\)")


def _acronyms(text: str) -> list[tuple[str, str]]:
    """(long form, acronym) wherever the text spells one out: the capitalised words right before
    "(MDS)" whose initials are M, D, S ("Master of Dental Surgery(MDS)")."""
    out = []
    for m in _ACRONYM.finditer(text):
        acr, words, taken = m.group(1), text[: m.start()].split(), []
        caps = 0
        for w in reversed(words):
            taken.insert(0, w)
            if w[:1].isupper():
                caps += 1
                if caps == len(acr):
                    break
        initials = "".join(w[0] for w in taken if w[:1].isupper())
        if caps == len(acr) and initials.upper() == acr:
            out.append((" ".join(taken), acr))
    return out


_SMALL = {"and", "of", "in", "&", "for", "with", "the"}
_DEGREE = re.compile(
    r"^((?:[A-Za-z]{1,4}\.){1,3}(?:[A-Za-z]{1,6}\.?)?|[A-Z]{2,5})\s+(?:\(hons?\.?\)\s+)?(?:in\s+)?(.+)$",
    re.IGNORECASE,
)


def subject_aliases(name: str, level: str | None) -> list[str]:
    """How visitors name a programme by its subject. "B.Tech. in Computer Science and Engineering"
    is asked about as "CSE", "B.Tech CSE" and "Computer Science"; seen live, "how many seats in
    CSE" matched no programme and the intake (360) in the catalogue was never used. A bare acronym
    or subject is given to undergraduate programmes only, so "CSE" means the B.Tech while "M.Tech
    CSE" still names the M.Tech (the longest name wins in Module B)."""
    # "B. Tech. in ..." (a space inside the degree) is read as "B.Tech. in ...": seen 19 Nov 2026,
    # the degree was taken as "B." and the programme got the acronym "TAIML" instead of "AIML".
    name = re.sub(r"^([A-Z])\.\s+(?=[A-Z][a-z]+\.?\s)", r"\1.", name.strip())
    m = _DEGREE.match(name.strip())
    if not m:
        return []
    degree = m.group(1).strip()
    bracket = " ".join(re.findall(r"\(([^)]*)\)", m.group(2)))
    subject = re.sub(r"\s*\(.*?\)\s*", " ", m.group(2)).strip(" .")
    words = [w for w in re.split(r"[\s,/-]+", subject) if w]
    out: set[str] = set()
    short = re.sub(
        r"\s+(and\s+)?engineering$", "", subject, flags=re.IGNORECASE
    ).strip()
    initials = "".join(
        w[0] for w in words if w.lower() not in _SMALL and w[:1].isalpha()
    ).upper()
    plain_degree = degree.replace(".", "").replace(" ", "")  # "B.Tech." -> "BTech"
    tagged = {degree.rstrip("."), plain_degree, re.sub(r"\.\s?", " ", degree).strip()}
    if 2 <= len(initials) <= 5 and len(words) >= 2:
        out |= {f"{d} {initials}" for d in tagged}
        if level == "UG" and len(initials) >= 3:  # "MC" is too easily something else
            out.add(initials)
    if short and short.lower() != subject.lower() and len(short.split()) >= 1:
        out |= {f"{d} {short}" for d in tagged}
        if level == "UG" and len(short) >= 5:
            out.add(short)
    if level == "UG" and len(words) >= 2:
        out.add(subject)
    # How these subjects are said (19 Nov 2026: "B.Tech AI and Data Science" matched the M.Tech in
    # Data Science, "AIML" matched nothing, "VLSI chip design" matched nothing).
    for form in _spoken_forms(subject):
        out |= {f"{d} {form}" for d in tagged}
        if level == "UG":
            out.add(form)
    for short, everyday in _SPECIALISATIONS.items():
        if short.lower() in bracket.lower() or short.lower() in subject.lower():
            for form in (short, *everyday):
                out |= {f"{d} {form}" for d in tagged}
                if level == "UG":
                    out.add(form)
    return sorted(a for a in out if a.lower() != name.lower())


# Usual short forms of subject words, in the order they are combined ("AI and ML", "AI & DS").
_SHORT_FORMS = {
    "artificial intelligence": "AI",
    "machine learning": "ML",
    "data science": "DS",
}
# A specialisation named in brackets, and what visitors call it.
_SPECIALISATIONS = {"VLSI": ("VLSI Design", "Chip Design")}


def _spoken_forms(subject: str) -> list[str]:
    """'Artificial Intelligence and Machine Learning' -> AIML, AI&ML, AI & ML, AI and ML; 'Artificial
    Intelligence (AI) and Data Science' -> AI and Data Science, AI & DS, AIDS, AI&DS."""
    s = re.sub(r"\s*\(.*?\)\s*", " ", subject).strip()
    parts = [
        p.strip() for p in re.split(r"\s+(?:and|&)\s+", s, flags=re.I) if p.strip()
    ]
    if len(parts) != 2:
        return []
    shorts = [_SHORT_FORMS.get(p.lower()) for p in parts]
    if not shorts[0] and not shorts[1]:
        return []
    a = shorts[0] or parts[0]
    b = shorts[1] or parts[1]
    forms = {f"{a} and {b}", f"{a} & {b}", f"{a}&{b}"}
    if shorts[0] and shorts[1]:
        forms.add(f"{a}{b}")
    if shorts[0]:
        forms.add(f"{a} and {parts[1]}")  # "AI and Data Science"
        forms.add(f"{a} & {parts[1]}")
    if shorts[0] and len(b) <= 3:
        forms.add(f"{a}{b}")
    return sorted(forms)


def aliases(name: str, title: str = "") -> list[str]:
    """Ways visitors write a programme name: without dots, without "in", without "(Hons)", and
    with the acronym the page itself gives ("Master of Dental Surgery(MDS)" -> "MDS ...")."""
    variants = {name}
    for long, short in _acronyms(f"{title} . {name}"):
        if f"{long} ({short})" in name:  # "Master's of Business Administration (MBA)"
            variants.add(short)
        elif long in name:
            variants.add(" ".join(name.replace(long, short).split()))
    for v in list(variants):
        variants.add(" ".join(v.replace(".", " ").split()).replace(" ( ", " ("))
        variants.add(" ".join(re.sub(r"\.", "", v).split()))
    for v in list(variants):
        variants.add(re.sub(r"\s+in\s+", " ", v).strip())
    # "(Hons)", "(Hons.)" and "(Honours)": dropped, or said as a word. Speech-to-text writes
    # "Honours" as "honors" (seen live: "BBA (Honours)" had no aliases at all, so a spoken "BBA
    # honors" matched no programme while the typed name did).
    honours = re.compile(r"\s*\(\s*(hons?\.?|hono(u)?rs?)\s*\)\s*", re.IGNORECASE)
    for v in list(variants):
        if honours.search(v):
            bare = honours.sub(" ", v).strip()
            variants.add(bare)
            head, _, tail = honours.sub(" | ", v).partition(" | ")
            for word in ("Hons", "Honours", "Honors"):
                variants.add(" ".join(f"{head.strip()} {word} {tail.strip()}".split()))
    for v in list(variants):  # "B Tech" is also written "BTech"
        variants.add(re.sub(r"^([A-Za-z])\s+(?=[A-Z][a-z])", r"\1", v))
    variants.discard(name)
    return sorted(" ".join(v.split()) for v in variants if len(v) >= 3)  # "MBA"


_QUOTA = re.compile(
    r"quota|category|\bnri\b|management|government|private|international|foreign|indian",
    re.I,
)


def _amount(text: str) -> Decimal | None:
    m = _AMOUNT.search(text) or _BARE_AMOUNT.search(_YEAR.sub(" ", text))
    return Decimal(m.group(1).replace(",", "")) if m else None


def _period(text: str) -> str:
    if re.search(r"per\s+(year|annum)", text, re.I):
        return "per year"
    if re.search(r"per\s+semester", text, re.I):
        return "per semester"
    return "total"


def fee_rows(body: list[ingest.Block]) -> list[tuple[str, Decimal, str]]:
    """(category, amount, period) from a fee section. A table with one column per quota
    ("Government Quota", "Private Quota", "NRI/Other Quota") gives one fee per quota, read from
    the last cells after the header (the first cell of a row is the programme's name); a cell
    without an amount ("Click Here") is skipped. Otherwise the first amount is the general fee."""
    texts = [b.text.strip() for b in body if b.text.strip()]
    whole = "\n".join(texts)
    heads = [i for i, t in enumerate(texts) if _QUOTA.search(t) and _amount(t) is None]
    if len(heads) >= 2 and heads == list(range(heads[0], heads[0] + len(heads))):
        cells = [t for t in texts[heads[-1] + 1 :] if not t.endswith(":")]
        # the value cells of the first row: up to (and including) the first amount run
        row = cells[: len(heads) + 1]
        values = row[-len(heads) :] if len(row) > len(heads) else row
        out = []
        for head, cell in zip((texts[i] for i in heads), values):
            amount = _amount(cell)
            if amount is not None:
                name = re.sub(r"\s*quota\s*", " ", head, flags=re.I).strip().lower()
                out.append((name or "general", amount, _period(cell + " " + whole)))
        if out:
            return out
    amount = _amount(whole)
    return [("general", amount, _period(whole))] if amount is not None else []


def programme(page: Page, default_year: str) -> tuple[dict, list[dict]] | None:
    """The catalogue entry a programme page describes, or None if it does not describe one."""
    blocks = page.blocks
    titles = [b.text.strip() for b in blocks if b.level == 1 and b.text.strip()]
    elig = _section(blocks, r"eligib")
    fee = _section(blocks, r"\bfees?\b")
    if not titles or not (elig or fee):
        return None
    name = titles[0]
    level = _level(name)
    if level is None:
        return None
    slug = urlparse(page.url).path.rstrip("/").rsplit("/", 1)[-1]
    year = default_year
    fees: list[dict] = []
    if fee:
        head, body = fee
        text = "\n".join(b.text for b in body)
        y = _YEAR.search(head) or _YEAR.search(text)
        if y:
            year = f"{y.group(1)}-{y.group(2)}"
        for category, amount, per in fee_rows(body):
            fees.append(
                {
                    "program_code": slug.upper(),
                    "category": category,
                    "component": "Total fee",
                    "amount": amount,
                    "currency": "INR",
                    "period": per,
                    "valid_from": None,
                    "valid_to": None,
                }
            )
    intake = None
    sec = _section(blocks, r"intake")
    if sec:
        n = re.search(r"(\d+)\s*seats?", " ".join(b.text for b in sec[1]), re.I)
        intake = int(n.group(1)) if n else None
    eligibility = (
        " ".join(b.text.strip() for b in elig[1] if b.text.strip())[:2000]
        if elig
        else None
    )
    prog = {
        "code": slug.upper(),
        "name": name,
        "aliases": sorted(
            set(aliases(name, page.title)) | set(subject_aliases(name, level))
        ),
        "department": _label_value(blocks, ("faculty", "school"))
        or _label_value(blocks, ("department",)),
        "level": level,
        "duration_months": _months(
            _label_value(blocks, ("study", "duration", "programme duration"))
        ),
        "intake": intake,
        "eligibility": eligibility,
        "contact": None,
        "academic_year": year,
        "source_url": page.url,
    }
    return prog, fees


def faqs(page: Page) -> list[tuple[str, str]]:
    """(question, answer) for every heading on the page that asks a question."""
    out = []
    for head, body in _sections(page.content or page.blocks):
        answer = " ".join(b.text.strip() for b in body if b.text.strip())
        if head.endswith("?") and len(answer) >= 20:
            out.append((" ".join(head.split()), answer[:3000]))
    return out


# ------------------------------------------------------------------ building the KB


def _store_page(
    settings: Settings, embedder: Embedder, tenant: str, page: Page, source_id
) -> str:
    """Index one page as an approved document; 'new', 'updated', 'unchanged' or 'empty'. Tables
    are indexed row by row, each value next to its column heading."""
    content = ingest.table_rows(page.content)
    if not any(b.level == 0 for b in content):
        return "empty"
    data = ingest.full_text(content).encode()
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select id from kb.documents where url = %s and status not in ('rejected','expired')"
            " order by version desc limit 1",
            (page.url,),
        )
        live = cur.fetchone()
        row, duplicate = documents.create(
            cur,
            tenant,
            "page.html",
            data,
            {
                "title": page.title,
                "url": page.url,
                "source_id": source_id,
                "replaces": live["id"] if live else None,
            },
        )
    if duplicate:
        return "unchanged"
    done = documents.process(
        settings, embedder, tenant, str(row["id"]), "page.html", data, content
    )
    if done["status"] == "draft" and done["chunk_count"]:
        with tx(settings.database_url, tenant) as cur:
            documents.act(cur, str(row["id"]), "approve", ACTOR)
    return "updated" if live else "new"


def _recent_file(settings: Settings, tenant: str, url: str) -> bool:
    """A file from this address was indexed (or found unreadable) within DOC_RECHECK_DAYS."""
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select 1 from kb.documents where url = %s and status <> 'expired'"
            " and updated_at > now() - make_interval(days => %s) limit 1",
            (url, DOC_RECHECK_DAYS),
        )
        return cur.fetchone() is not None


def _store_file(
    settings: Settings,
    embedder: Embedder,
    tenant: str,
    url: str,
    data: bytes,
    source_id,
) -> str:
    """Index a linked file. A text file is approved like a page; one read by OCR stays a draft
    with its warning until staff check its numbers (F4.1); a scan without OCR is kept as
    needs_review. Returns 'new', 'updated', 'unchanged', 'ocr_draft', 'needs_review' or
    'rejected'."""
    name = _doc_name(url)
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select id from kb.documents where url = %s and status not in ('rejected','expired')"
            " order by version desc limit 1",
            (url,),
        )
        live = cur.fetchone()
        row, duplicate = documents.create(
            cur,
            tenant,
            name,
            data,
            {
                "title": name.rsplit(".", 1)[0].strip() or name,
                "url": url,
                "source_id": source_id,
                "replaces": live["id"] if live else None,
            },
        )
    if duplicate:
        return "unchanged"
    done = documents.process(settings, embedder, tenant, str(row["id"]), name, data)
    if done["status"] == "draft" and done["chunk_count"]:
        if done["status_reason"]:  # read by OCR: staff check it first
            return "ocr_draft"
        with tx(settings.database_url, tenant) as cur:
            documents.act(cur, str(row["id"]), "approve", ACTOR)
        return "updated" if live else "new"
    return done["status"]


def _store_curricula(
    settings: Settings, embedder: Embedder, tenant: str, pages: list[Page], source_id
) -> Counter:
    """One curriculum summary document per programme page with a credits table (curriculum.py),
    at the page's address + '#curriculum-summary', so it is versioned with its page."""
    out: Counter = Counter()
    for p in pages:
        name = next(
            (b.text.strip() for b in p.blocks if b.level == 1 and b.text.strip()), ""
        )
        months = _months(
            _label_value(p.blocks, ("study", "duration", "programme duration"))
        )
        blocks = curriculum.summary(name, p.url, p.content, months) if name else None
        if not blocks:
            continue
        doc = Page(
            f"{p.url}#curriculum-summary",
            f"{name}: curriculum, credits and subject share",
            blocks,
            content=blocks,
        )
        out[_store_page(settings, embedder, tenant, doc, source_id)] += 1
    return out


def _norm(q: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", q.lower()).strip()


def _store_faqs(
    settings: Settings,
    embedder: Embedder,
    tenant: str,
    pairs: list[tuple[str, str]],
    year: str,
    valid_to: date,
) -> dict:
    """An FAQ the site asks becomes a curated answer; one that matches an existing curated
    question updates it (a new version), so references to it keep working."""
    stats = {"new": 0, "updated": 0, "unchanged": 0}
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select id::text, question, answer from kb.curated_qa where status = 'approved'"
        )
        existing = {_norm(r["question"]): r for r in cur.fetchall()}
        for q, a in pairs:
            found = existing.get(_norm(q))
            if found and " ".join(found["answer"].split()) == " ".join(a.split()):
                stats["unchanged"] += 1
                continue
            if found:
                curated.update(cur, embedder, found["id"], {"answer": a}, ACTOR)
                curated.set_status(cur, found["id"], "approved", ACTOR)
                stats["updated"] += 1
                continue
            ref = "site:" + hashlib.sha1(_norm(q).encode()).hexdigest()[:12]
            curated.create(
                cur,
                embedder,
                tenant,
                {
                    "question": q,
                    "answer": a,
                    "category": "faq",
                    "academic_year": year,
                    "valid_to": valid_to,
                    "external_ref": ref,
                },
                ACTOR,
                status="approved",
            )
            stats["new"] += 1
    return stats


def _current_year(settings: Settings, tenant: str) -> str:
    with tx(settings.database_url, tenant) as cur:
        cur.execute("select max(academic_year) as y from kb.programs")
        y = (cur.fetchone() or {}).get("y")
    if y:
        return y
    t = date.today()
    start = t.year if t.month >= 6 else t.year - 1
    return f"{start}-{str(start + 1)[2:]}"


def build(
    settings: Settings,
    embedder: Embedder,
    tenant: str,
    start_url: str,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    max_pages: int = 300,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    delay: float = POLITE_DELAY_S,
    log: Callable[[str], None] = lambda s: None,
    max_documents: int = MAX_DOCUMENTS,
    max_document_mb: float = MAX_DOCUMENT_MB,
    listings: list[dict] | None = None,
) -> dict:
    client = client or httpx.Client()
    urls, robots = discover(
        client, start_url, include or [], exclude or [], max_pages, sleep
    )
    if listings:
        # Pages only the site's own listing names come first, so max_pages never cuts them.
        listed = listing_urls(
            client,
            listings,
            urlparse(start_url).netloc,
            include or [],
            exclude or [],
            robots,
            sleep,
            delay,
        )
        known = {u.rstrip("/") for u in listed}
        urls = (listed + [u for u in urls if u.rstrip("/") not in known])[:max_pages]
        log(f"{len(listed)} pages from the site's listings")
    log(f"{len(urls)} pages to read")
    pages, failed = fetch_pages(client, urls, sleep, delay)
    # One more round: pages the read pages link to that the sitemap missed (renamed pages).
    extra = more_urls(
        pages, include or [], exclude or [], urlparse(start_url).netloc, robots
    )[: max(0, max_pages - len(pages))]
    if extra:
        more, failed2 = fetch_pages(client, extra, sleep, delay)
        seen = {p.url.rstrip("/") for p in pages}
        pages += [p for p in more if p.url.rstrip("/") not in seen]
        failed += failed2
    log(
        f"{len(pages)} pages read, {failed} failed (sitemap entries that no longer exist)"
    )
    strip_furniture(pages)

    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select id from kb.sources where kind = 'crawl' and config->>'start_url' = %s",
            (start_url,),
        )
        src = cur.fetchone()
        if src is None:
            cur.execute(
                "insert into kb.sources (tenant_id, kind, name, config) values (%s,'crawl',%s,%s)"
                " returning id",
                (
                    tenant,
                    f"Website {urlparse(start_url).netloc}",
                    Jsonb(
                        {
                            "start_url": start_url,
                            "include": include or [],
                            "self_building": True,
                        }
                    ),
                ),
            )
            src = cur.fetchone()
    docs = Counter(_store_page(settings, embedder, tenant, p, src["id"]) for p in pages)
    log(f"documents: {dict(docs)}")
    summaries = _store_curricula(settings, embedder, tenant, pages, src["id"])
    log(f"curriculum summaries: {dict(summaries)}")

    files: Counter = Counter()
    for url in linked_documents(
        pages, urlparse(start_url).netloc, robots, exclude or [], max_documents
    ):
        if _recent_file(settings, tenant, url):
            files["checked recently"] += 1
            continue
        data = fetch_document(client, url, int(max_document_mb * 1024 * 1024), sleep)
        if isinstance(data, str):
            files[data] += 1
            continue
        files[_store_file(settings, embedder, tenant, url, data, src["id"])] += 1
        sleep(delay)
    log(f"linked files: {dict(files)}")

    year = _current_year(settings, tenant)
    programmes, fees = [], []
    for p in pages:
        found = programme(p, year)
        if found:
            programmes.append(found[0])
            fees += found[1]
    by_code = {p["code"]: p for p in programmes}  # one entry per page address
    programmes = list(by_code.values())
    fees = list({(f["program_code"], f["category"]): f for f in fees}.values())
    if programmes:
        # The catalogue year is the newest the site states. A page that still shows an older
        # fee table stays in the catalogue, and its fee says which year it is from.
        newest = max(p["academic_year"] for p in programmes)
        stale = {
            p["code"]: p["academic_year"]
            for p in programmes
            if p["academic_year"] != newest
        }
        for p in programmes:
            p["academic_year"] = newest
        for f in fees:
            if f["program_code"] in stale:
                f["period"] = (
                    f"{f['period']} ({stale[f['program_code']]} fee structure)"
                )
    report: dict = {
        "pages": len(pages),
        "failed": failed,
        "documents": dict(docs),
        "curriculum_summaries": dict(summaries),
        "linked_files": dict(files),
        "programmes_found": len(programmes),
        "catalogue": "unchanged",
    }
    if programmes:
        years = sorted({p["academic_year"] for p in programmes})
        with tx(settings.database_url, tenant) as cur:
            # Names visitors already use stay recognised: aliases and the old code of the
            # programme with the same name are carried over to its regenerated entry.
            cur.execute("select code, name, aliases from kb.programs")
            known = {_norm(r["name"]): r for r in cur.fetchall()}
            for p in programmes:
                old = known.get(_norm(p["name"]))
                if old:
                    extra = [old["code"], *(old["aliases"] or [])]
                    p["aliases"] = sorted(
                        {a for a in p["aliases"] + extra if a and a != p["name"]}
                    )
            cur.execute(
                "select count(*) as n from kb.programs where academic_year = any(%s)",
                (years,),
            )
            before = cur.fetchone()["n"]
            if len(programmes) >= MIN_SHARE * before:
                catalogue.import_catalogue(
                    cur,
                    tenant,
                    {"programs": programmes, "fees": fees, "dates": []},
                )
                report["catalogue"] = (
                    f"replaced {before} programmes with {len(programmes)} for {', '.join(years)}"
                )
            else:
                report["catalogue"] = (
                    f"kept: only {len(programmes)} programmes found against {before} today"
                )
    pairs = [qa for p in pages for qa in faqs(p)]
    pairs = list({_norm(q): (q, a) for q, a in pairs}.values())
    y0 = int(year[:4])
    report["faqs"] = _store_faqs(
        settings, embedder, tenant, pairs, year, date(y0 + 1, 7, 31)
    )
    search.invalidate(tenant)
    if report["catalogue"].startswith("replaced"):
        from . import catalogue_cases

        report["catalogue_cases"] = catalogue_cases.generate(settings, tenant)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tenant-id", required=True)
    ap.add_argument(
        "--pack", help="a Tenant Pack's pack.yaml: its `website:` gives the settings"
    )
    ap.add_argument("--start-url")
    ap.add_argument("--include", action="append", default=[])
    ap.add_argument("--exclude", action="append", default=[])
    ap.add_argument("--max-pages", type=int, default=300)
    ap.add_argument(
        "--max-documents",
        type=int,
        default=MAX_DOCUMENTS,
        help="linked files (PDF, DOCX, PPTX, XLSX) to index; 0 for none",
    )
    ap.add_argument("--max-document-mb", type=float, default=MAX_DOCUMENT_MB)
    a = ap.parse_args()
    if a.pack:
        import yaml

        site = (yaml.safe_load(open(a.pack, encoding="utf-8")) or {}).get("website")
        if not site:
            print(f"{a.pack} has no website: section; nothing to build")
            return 0
        a.start_url = a.start_url or site["start_url"]
        a.include = a.include or site.get("include", [])
        a.exclude = a.exclude or site.get("exclude", [])
        a.max_pages = int(site.get("max_pages", a.max_pages))
        files = site.get("documents") or {}  # documents: {max: 150, max_mb: 40}
        a.max_documents = int(files.get("max", a.max_documents))
        a.max_document_mb = float(files.get("max_mb", a.max_document_mb))
        a.listings = site.get("listings") or []
    if not a.start_url:
        sys.exit("Give --start-url or --pack")
    s = Settings()
    if not s.database_url:
        sys.exit("Set KB_DATABASE_URL (source local.env.sh)")
    t0 = time.monotonic()
    report = build(
        s,
        make_embedder(s),
        a.tenant_id,
        a.start_url,
        a.include,
        a.exclude,
        a.max_pages,
        log=lambda m: print(f"[{time.monotonic() - t0:5.0f}s] {m}", flush=True),
        max_documents=a.max_documents,
        max_document_mb=a.max_document_mb,
        listings=getattr(a, "listings", None),
    )
    for k, v in report.items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
