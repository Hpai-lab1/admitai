"""Deterministic per-sentence checks (PRD F1.4a): no LLM, well under 20 ms per sentence.

A sentence is released only if
  1. every number, amount, date and percentage in it appears in a cited source (or the catalogue),
     **as the same fact**: a number is bound to the labels around it in its source (fee component,
     category, intake or duration, date event, programme), so "the hostel fee is 2,50,000" fails
     when 2,50,000 is the tuition fee, and a date must match on day and month, not only the day;
  2. it carries a citation marker [n] for a source in the context, if it states a fact, and the
     cited source shares the sentence's words (lexical support; NLI verification is Level 2), and
  3. it contains no banned promise phrase.

When the model forgets a marker, a sentence whose words clearly come from exactly one source is
attributed to it ("auto-cited"); this is a deterministic overlap rule and is logged as such.

Label binding only ever blocks when both sides name a label of the same kind and the labels
differ; a number with no label words around it (in the sentence or in its source) is checked for
presence only, as before. It narrows what passes, it never lets through what was blocked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for m in names
}

PROMISE_PATTERNS = [
    r"\bguarantee(d|s)?\b",
    r"\bassured (admission|seat|placement)",
    r"\byou will (definitely |surely |certainly )?(get|be given|receive) (an? )?(admission|seat)",
    r"\b(confirmed|sure|definite) (admission|seat)\b",
    r"\b100 ?% (placement|admission)",
    r"\byou are (selected|admitted)\b",
    r"\bseat is (yours|confirmed|reserved for you)",
    r"पक्का (एडमिशन|प्रवेश|सीट)",
    r"(एडमिशन|प्रवेश|सीट) (पक्का|गारंटी)",
    r"गारंटी",
    r"ಖಚಿತ (ಪ್ರವೇಶ|ಸೀಟು)",
    r"(ಪ್ರವೇಶ|ಸೀಟು) (ಖಚಿತ|ಗ್ಯಾರಂಟಿ)",
    r"ಗ್ಯಾರಂಟಿ",
]
_PROMISE = re.compile("|".join(PROMISE_PATTERNS), re.IGNORECASE)

# A verdict on the visitor's own eligibility ("As you are in class 11, you are not eligible") is
# never the assistant's to give, cited or not: it can turn a student away for good, and it is
# most often said to under-18s. The published criteria may be stated; a conditional ("you are
# eligible if you have 60% in PCM") is a criterion, not a verdict. Roadmap item 1.
_UNLESS = r"(?!\s*,?\s*(if|provided|when|once|as long as|only if|unless|until|before|after|without)\b)"
VERDICT_PATTERNS = [
    r"\byou(?:'re| are| would be| will be| may be| might be| wouldn't be| won't be)?\s*(not\s+)?"
    r"(eligible|ineligible|qualified|disqualified)\b" + _UNLESS,
    r"\byou (can ?not|can't|cannot|won't be able to|will not be able to|are not allowed to|"
    r"may not|are unable to) (apply|join|get (an )?admission|be admitted|take admission|enrol+)\b"
    + _UNLESS,
    r"\byou (do not|don't|did not|didn't|do|will|would) (not )?(qualify|meet the "
    r"(eligibility|criteria|requirements?))\b" + _UNLESS,
    r"\byou qualify\b" + _UNLESS,
    r"\b(as|since|because) you(?:'re| are| have| were)\b[^.]*\b(eligible|ineligible|qualify|"
    r"apply|join|admitted|admission)\b",
    r"\byou are (too young|underage|not old enough|over ?age)\b",
    r"आप (पात्र|योग्य|अयोग्य) (नहीं )?(हैं|है)",
    r"आप (आवेदन|प्रवेश|दाखिला) नहीं (कर|ले) (सकते|सकतीं|सकती)",
    r"ನೀವು (ಅರ್ಹರಲ್ಲ|ಅರ್ಹರು|ಅರ್ಹರಾಗಿದ್ದೀರಿ|ಅನರ್ಹರು)",
    r"ನೀವು (ಅರ್ಜಿ ಸಲ್ಲಿಸಲು|ಸೇರಲು|ಪ್ರವೇಶ ಪಡೆಯಲು) (ಸಾಧ್ಯವಿಲ್ಲ|ಆಗುವುದಿಲ್ಲ)",
]
_VERDICT = re.compile("|".join(VERDICT_PATTERNS), re.IGNORECASE)
ELIGIBILITY_VERDICT = "eligibility verdict"

# Not factual: questions back to the user, offers of help, greetings and hedges.
_NON_FACTUAL = re.compile(
    r"^(would you like|do you want|shall i|can i help|let me know|feel free|i can (ask|connect|arrange|help)"
    r"|please (ask|contact|let)|hello|hi\b|thank|you're welcome|i'm not (sure|certain)|i am not (sure|certain)"
    r"|i don't know|i do not know"
    r"|क्या आप|धन्यवाद|नमस्ते)",
    re.IGNORECASE,
)
_CITE = re.compile(r"\s*\[(\d{1,2})\]")
_ABBREV = re.compile(
    r"\b(B|M|Ph|e|i|Dr|Mr|Mrs|Ms|Prof|No|Rs|St|vs|etc|approx|Govt|Dept|Sr|Jr)\.$", re.I
)
_LOCAL_SCRIPT = re.compile(
    r"[\u0900-\u0DFF]"
)  # Indic scripts: sources are English, so no word test
_WORD = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
STOP = set(
    "the and for are with this that from you your can will have has was were our its not but all any "
    "per year years also which who what when where how there their they them into about after before "
    "than then more most such only other each been being does did may must shall should could would".split()
)

LAKH, CRORE = Decimal(100000), Decimal(10000000)
_NUMBER = re.compile(
    r"(?<![\w.])(?:₹|rs\.?|inr)?\s*(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(lakhs?|lacs?|crores?|cr\b|k\b|%)?",
    re.IGNORECASE,
)
_TIME = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_YEAR_RANGE = re.compile(r"\b(\d{4})\s*[-–/]\s*(\d{2,4})\b")


def split_sentences(buffer: str, final: bool = False) -> tuple[list[str], str]:
    """Split complete sentences off the front of a streaming buffer. Returns (sentences, rest).
    A boundary is . ! ? or । followed by whitespace, except after abbreviations like "B." in
    "B.Tech" (no whitespace) or "Rs." / "Dr.". Newlines also end a sentence (list items)."""
    sentences: list[str] = []
    start = 0
    for m in re.finditer(r"([.!?।])(\s+)|\n+", buffer):
        end = m.end()
        if m.group(1):
            head = buffer[start : m.start() + 1]
            if _ABBREV.search(head.rstrip()) or re.fullmatch(r"\s*\d{1,2}\.", head):
                continue  # "B." / "Rs." / a list marker such as "1."
            # keep a citation marker that follows the full stop with its sentence: "... 2027. [1]"
            tail = _CITE.match(buffer, end)
            if tail:
                end = tail.end()
        piece = buffer[start:end].strip()
        if piece:
            sentences.append(piece)
        start = end
    rest = buffer[start:]
    if final and rest.strip():
        sentences.append(rest.strip())
        rest = ""
    return sentences, rest


def _dec(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def _blank(text: str, pattern: re.Pattern) -> str:
    """Remove matches but keep every other character where it was (positions stay valid)."""
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


def number_spans(text: str) -> list[tuple[int, int, set[Decimal]]]:
    """Every number with its position in `text`: (start, end, values). Values are normalised as in
    numbers_in; an ISO date or a year range is one span with several values."""
    spans: list[tuple[int, int, set[Decimal]]] = []
    t = _blank(text, _CITE)
    for m in _TIME.finditer(t):
        spans.append(
            (m.start(), m.end(), {Decimal(int(m.group(1)) * 100 + int(m.group(2)))})
        )
    t = _blank(t, _TIME)
    for m in _ISO_DATE.finditer(t):
        spans.append((m.start(), m.end(), {Decimal(int(g)) for g in m.groups()}))
    t = _blank(t, _ISO_DATE)
    for m in _YEAR_RANGE.finditer(t):
        a, b = m.group(1), m.group(2)
        values = {Decimal(a), Decimal(b if len(b) == 4 else a[:2] + b), Decimal(b)}
        spans.append((m.start(), m.end(), values))
    t = _blank(t, _YEAR_RANGE)
    for m in _NUMBER.finditer(t):
        value = _dec(m.group(1))
        if value is None:
            continue
        unit = (m.group(2) or "").lower()
        if unit.startswith(("lakh", "lac")):
            value *= LAKH
        elif unit.startswith("cr"):
            value *= CRORE
        elif unit == "k":
            value *= 1000
        spans.append((m.start(), m.end(), {value.normalize()}))
    spans.sort(key=lambda s: s[0])
    return [(a, b, {v.normalize() for v in vs}) for a, b, vs in spans]


def numbers_in(text: str) -> set[Decimal]:
    """Every number a reader would take as a fact, normalised: '2,50,000', 'Rs 250000' and
    '2.5 lakh' are all 250000; '30 June 2027' yields 30 and 2027; '09:00' yields 9:00 as 900."""
    out: set[Decimal] = set()
    for _, _, values in number_spans(text):
        out |= values
    return out


# ------------------------------------------------------------------ dates (day and month together)

_MONTH_NAMES = "|".join(sorted(MONTHS, key=len, reverse=True))
_DAY_MONTH = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_NAMES})\.?(?:,?\s+(\d{{4}}))?\b",
    re.I,
)
_MONTH_DAY = re.compile(
    rf"\b({_MONTH_NAMES})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b", re.I
)
_NUMERIC_DATE = re.compile(
    r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b"
)  # Indian dd/mm/yyyy


def dates_in(text: str) -> set[tuple[int | None, int, int]]:
    """Calendar dates as (year or None, month, day): '30 June 2027', 'June 30, 2027', '30th June',
    '2027-06-30' and '30/06/2027' are all (2027, 6, 30)."""
    return {value for _, _, value in date_spans(text)}


def _date_supported(
    date: tuple[int | None, int, int], known: set[tuple[int | None, int, int]]
) -> bool:
    y, mo, d = date
    return any(
        (mo, d) == (km, kd) and (y is None or ky is None or y == ky)
        for ky, km, kd in known
    )


# ------------------------------------------------------------------ label binding

# Kinds of label that tell catalogue facts apart. Two labels of the same kind that differ mean two
# different facts: the tuition fee is not the hostel fee, the general-category fee is not the
# management-quota fee, the deadline is not the start of classes, the intake is not the duration.
LABELS: dict[str, dict[str, tuple[str, ...]]] = {
    "component": {
        "tuition": ("tuition", "tution", "ट्यूशन", "शिक्षण शुल्क"),
        "hostel": (
            "hostel",
            "hostels",
            "accommodation",
            "boarding",
            "room",
            "rooms",
            "हॉस्टल",
            "छात्रावास",
        ),
        "mess": ("mess", "food", "meals", "dining", "भोजन", "मेस"),
        "transport": ("transport", "bus", "buses"),
        "exam": ("exam fee", "examination fee", "exam fees", "examination fees"),
        "registration": ("registration", "enrolment", "enrollment"),
        "application": ("application fee", "application fees", "application form fee"),
        "deposit": ("deposit", "caution", "refundable"),
        "library": ("library",),
        "development": ("development",),
        "laboratory": ("lab fee", "lab fees", "laboratory fee", "laboratory fees"),
        "uniform": ("uniform",),
    },
    "category": {
        "general": ("general",),
        "management": (
            "management quota",
            "management category",
            "management seat",
            "management seats",
            "management)",
            "(management",
        ),
        "nri": ("nri", "non-resident", "non resident"),
        "sc": ("sc",),
        "st": ("st",),
        "obc": ("obc",),
        "ews": ("ews",),
        "government": (
            "government quota",
            "govt quota",
            "government seat",
            "government seats",
            "cet quota",
        ),
    },
    "kind": {
        "intake": ("intake", "seats", "seat", "capacity"),
        "duration": ("duration", "months", "month", "semesters"),
    },
    "event": {
        "deadline": (
            "deadline",
            "last date",
            "closes",
            "closing",
            "close",
            "due date",
            "apply by",
            "apply before",
        ),
        "start": (
            "begin",
            "begins",
            "start",
            "starts",
            "commence",
            "commences",
            "commencement",
            "classes",
            "orientation",
        ),
        "counselling": ("counselling", "counseling"),
        "entrance": ("entrance", "exam date", "test date"),
        "result": ("result", "results", "merit list"),
        "opening": ("opens", "open from", "applications open", "registration opens"),
    },
}


def _label_pattern(term: str) -> re.Pattern:
    body = re.escape(term)
    if term.isascii():
        lead = r"(?<![\w])" if term[0].isalnum() else ""
        tail = r"(?![\w])" if term[-1].isalnum() else ""
        return re.compile(lead + body + tail, re.I)
    return re.compile(body)  # Devanagari: vowel signs are not \w, so no word boundaries


_LABEL_PATTERNS = [
    (kind, name, _label_pattern(term))
    for kind, names in LABELS.items()
    for name, terms in names.items()
    for term in terms
]
KINDS = (*LABELS, "program")
Labels = dict[str, set[str]]


def labels_in(text: str, programs: list[dict] | None = None) -> Labels:
    """The label words in a piece of text, by kind, plus the programme codes it names."""
    out: Labels = {}
    for kind, name, pattern in _LABEL_PATTERNS:
        if pattern.search(text):
            out.setdefault(kind, set()).add(name)
    if programs:
        from .router import match_programs  # no cycle: router does not import checks

        codes = {p["code"] for p in match_programs(text, programs)}
        if codes:
            out["program"] = codes
    return out


def compatible(a: Labels, b: Labels) -> bool:
    """Two label sets can describe the same fact unless they name different labels of one kind."""
    return all(not (a.get(k) and b.get(k)) or bool(a[k] & b[k]) for k in KINDS)


# "24 months" or "18 seats" names what it counts, so it is not a fee and must not take a fee's labels.
# Plural "seats" and "semesters" only: "5000 seat deposit" and "50000 semester fee" are fees.
_UNIT = re.compile(r"[\s-]*(?:months?|semesters|seats)(?![\w])", re.I)


def _unit_end(text: str, span: tuple) -> int | None:
    """Where the unit after this number ends, or None if the number has no unit."""
    m = _UNIT.match(text, span[1])
    return m.end() if m else None


def _unit_labels(text: str, span: tuple) -> Labels:
    """A counted number's labels: its own unit only. The programme is added by the caller."""
    return labels_in(text[span[1] : _unit_end(text, span)])


def _windows(text: str, spans: list) -> list[tuple[str, str]]:
    """For each number, the text before it (back to the previous number) and after it (up to the
    next number). Label words in a sentence or a source line sit next to the number they label."""
    out = []
    for i, (start, end, _) in enumerate(spans):
        prev_end = spans[i - 1][1] if i else 0
        next_start = spans[i + 1][0] if i + 1 < len(spans) else len(text)
        out.append((text[prev_end:start], text[end:next_start]))
    return out


_CLAUSE = re.compile(r";|\n|\|")


def source_facts(
    text: str, program_code: str | None, programs: list[dict] | None
) -> list[tuple[set[Decimal], Labels]]:
    """Every number in a source with the labels of the fact it belongs to. A clause (between
    sentence ends, semicolons, line breaks or table cells) gives the labels next to the number;
    the programme comes from the clause, else its sentence, else the source itself (a catalogue
    entry names its programme once, on its first line)."""
    facts = []
    for sentence in split_sentences(text, final=True)[0]:
        sentence_programs = labels_in(sentence, programs).get("program")
        for clause in _CLAUSE.split(sentence):
            spans = number_spans(clause)
            if not spans:
                continue
            clause_programs = labels_in(clause, programs).get("program")
            prog = (
                clause_programs
                or sentence_programs
                or ({program_code} if program_code else None)
            )
            counted = [s for s in spans if _unit_end(clause, s)]
            for span in counted:
                labels = _unit_labels(clause, span)
                if prog:
                    labels["program"] = set(prog)
                facts.append((span[2], labels))
            # Counted numbers are not boundaries: "(UG, 48 months): tuition fee 250000" labels the
            # fee only, and the words on either side of "48 months" still reach their own number.
            plain = [s for s in spans if not _unit_end(clause, s)]
            for (_, _, values), (before, after) in zip(plain, _windows(clause, plain)):
                near_before, near_after = labels_in(before), labels_in(after)
                labels: Labels = {}
                for (
                    kind
                ) in LABELS:  # the label just before a number, else just after it
                    got = near_before.get(kind) or near_after.get(kind)
                    if got:
                        labels[kind] = set(got)
                if prog:
                    labels["program"] = set(prog)
                facts.append((values, labels))
    return facts


def sentence_labels(
    text: str, programs: list[dict] | None
) -> list[tuple[set[Decimal], Labels]]:
    """Each number in a sentence with the labels that may describe it, by kind: the words before
    it and after it, each up to the neighbouring number. Where both sides name a label of one kind
    ("2,50,000 for tuition and 90,000 for the hostel") either may be the number's own."""
    spans = number_spans(text)
    out = []
    for i, span in enumerate(spans):
        if _unit_end(text, span):
            labels = _unit_labels(text, span)
            start = spans[i - 1][1] if i else 0
            near = labels_in(text[start : span[0]], programs).get("program")
            if near:
                labels["program"] = near
            out.append((span[2], labels))
    plain = [s for s in spans if not _unit_end(text, s)]
    for (_, _, values), (before, after) in zip(plain, _windows(text, plain)):
        merged: Labels = {}
        for side in (labels_in(before, programs), labels_in(after, programs)):
            for kind, names in side.items():
                merged.setdefault(kind, set()).update(names)
        out.append((values, merged))
    return out


# Dates are bound to their own label as well ("Written test and interview: 5 June 2027"), with an
# open vocabulary: whatever words the source puts before the date, stemmed.
_DATE_GENERIC = {
    "date",
    "dates",
    "day",
    "days",
    "held",
    "will",
    "scheduled",
    "the",
    "on",
    "is",
    "are",
    "admission",
    "admissions",
    "college",
    "university",
    "programme",
    "program",
}


def _label_stems(text: str) -> set[str]:
    words = {w for w in content_words(text) if w not in _DATE_GENERIC}
    return {(w[:-1] if len(w) > 3 and w.endswith("s") else w)[:4] for w in words}


def date_spans(text: str) -> list[tuple[int, int, tuple[int | None, int, int]]]:
    t = _blank(text, _CITE)
    out = []
    for m in _ISO_DATE.finditer(t):
        y, mo, d = (int(g) for g in m.groups())
        if 1 <= mo <= 12 and 1 <= d <= 31:
            out.append((m.start(), m.end(), (y, mo, d)))
    for m in _DAY_MONTH.finditer(t):
        d, mo = int(m.group(1)), MONTHS[m.group(2).lower()]
        if 1 <= d <= 31:
            out.append(
                (m.start(), m.end(), (int(m.group(3)) if m.group(3) else None, mo, d))
            )
    for m in _MONTH_DAY.finditer(t):
        mo, d = MONTHS[m.group(1).lower()], int(m.group(2))
        if 1 <= d <= 31:
            out.append(
                (m.start(), m.end(), (int(m.group(3)) if m.group(3) else None, mo, d))
            )
    for m in _NUMERIC_DATE.finditer(t):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            out.append((m.start(), m.end(), (y, mo, d)))
    out.sort(key=lambda s: s[0])
    return out


def _date_windows(text: str) -> list[tuple[tuple[int | None, int, int], set[str]]]:
    """Each date with the stems of its label: the words before it (back to the previous date),
    else the words after it (up to the next date)."""
    spans = date_spans(text)
    out = []
    for (_, _, value), (before, after) in zip(spans, _windows(text, spans)):
        out.append((value, _label_stems(before) or _label_stems(after)))
    return out


def source_date_labels(text: str) -> list[tuple[tuple[int | None, int, int], set[str]]]:
    out = []
    for sentence in split_sentences(text, final=True)[0]:
        for clause in _CLAUSE.split(sentence):
            out.extend(_date_windows(clause))
    return out


def strip_list_marker(sentence: str) -> str:
    return re.sub(r"^\s*(\d{1,2}[.)]|[-*•])\s+", "", sentence)


def content_words(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text) if w.lower() not in STOP}


@dataclass
class Source:
    """One numbered entry in the answer context. `programs` is the college's programme list (code,
    name, aliases), used to tell which programme a number in this source belongs to."""

    n: int
    text: str
    citation: dict
    programs: list[dict] = field(default_factory=list)
    numbers: set[Decimal] = field(default_factory=set)
    words: set[str] = field(default_factory=set)
    facts: list[tuple[set[Decimal], Labels]] = field(default_factory=list)
    dates: set[tuple[int | None, int, int]] = field(default_factory=set)
    date_labels: list[tuple[tuple[int | None, int, int], set[str]]] = field(
        default_factory=list
    )

    def __post_init__(self) -> None:
        self.numbers = numbers_in(self.text)
        self.words = content_words(self.text)
        self.dates = dates_in(self.text)
        self.date_labels = source_date_labels(self.text)
        codes = {p["code"] for p in self.programs}
        ref = self.citation.get("ref_id")
        own = (
            ref
            if self.citation.get("source_type") == "catalogue" and ref in codes
            else None
        )
        self.facts = source_facts(self.text, own, self.programs)


@dataclass
class CheckResult:
    ok: bool
    text: str  # sentence with markers kept
    cited: list[int]
    reason: str | None = None
    auto_cited: bool = False
    unsupported: list[str] = field(default_factory=list)


# "... is not provided in the given sources", "no specific amount is mentioned": the model saying
# it does not know, in its own words. A statement about the sources, not about the college.
REFUSAL = re.compile(
    r"\b(not|n't|never)\s+(be\s+)?(explicitly\s+|specifically\s+|directly\s+|clearly\s+)?"
    r"(provided|specified|covered|mentioned|stated|listed|included|given|available|found|described|detailed)"
    r"\b[^.]*\b(sources?|information|documents?|context|faqs?)\b"
    r"|\bno (specific |such |relevant )?(information|details?|amount|mention|source)\b[^.]*\b(mentioned|provided|"
    r"available|given|found|sources?)\b"
    r"|\b(sources?|information) (provided |given |available )?(do(es)? not|don't|doesn't) (mention|say|cover|contain|"
    r"include|specify|state)",
    re.IGNORECASE,
)


def is_refusal(sentence: str) -> bool:
    return bool(REFUSAL.search(_CITE.sub("", sentence)))


def is_factual(sentence: str) -> bool:
    plain = _CITE.sub("", strip_list_marker(sentence)).strip()
    if plain.endswith("?") or _NON_FACTUAL.match(plain) or is_refusal(plain):
        return False
    return len(plain.split()) >= 4 or bool(numbers_in(plain))


def check_sentence(sentence: str, sources: list[Source]) -> CheckResult:
    by_n = {s.n: s for s in sources}
    if _PROMISE.search(sentence):
        return CheckResult(False, sentence, [], "promise")
    if _VERDICT.search(_CITE.sub(" ", sentence)):
        return CheckResult(False, sentence, [], ELIGIBILITY_VERDICT)
    cited = [int(n) for n in _CITE.findall(sentence)]
    bad_refs = [n for n in cited if n not in by_n]
    if bad_refs:
        return CheckResult(
            False, sentence, cited, f"citation [{bad_refs[0]}] has no source"
        )
    body = strip_list_marker(_CITE.sub(" ", sentence))
    nums = numbers_in(body)
    auto = False
    if is_factual(sentence) and not cited:
        best = _attribute(body, nums, sources)
        if best is None and nums and _LOCAL_SCRIPT.search(body):
            # Indic-script sentence about English sources: attribute by its numbers alone.
            best = next((s.n for s in sources if nums <= s.numbers), None)
        if best is None:
            return CheckResult(False, sentence, [], "no citation")
        cited, auto = [best], True
    elif cited and is_factual(sentence) and not _LOCAL_SCRIPT.search(body):
        # The cited sources must actually support the words of the sentence; if another source
        # clearly does, the sentence is re-attributed to it (logged as auto-cited).
        words = content_words(body)
        support = set().union(*(by_n[n].words for n in cited))
        if len(words) >= 3 and len(words & support) / len(words) < 0.3:
            best = _attribute(body, nums, sources)
            if best is None:
                return CheckResult(False, sentence, cited, "citation does not support")
            sentence = _CITE.sub("", sentence).rstrip()
            cited, auto = [best], True
    # Numbers and dates must come from the cited sources; catalogue sources always count
    # (authoritative). Each number must also be the same fact there (label binding).
    allowed_sources = [
        s
        for s in sources
        if s.n in cited or s.citation.get("source_type") == "catalogue"
    ]
    if nums:
        allowed: set[Decimal] = set()
        for s in allowed_sources:
            allowed |= s.numbers
        missing = sorted(str(n) for n in nums - allowed)
        if missing:
            return CheckResult(
                False, sentence, cited, "number not in sources", auto, missing
            )
        programs = next((s.programs for s in sources if s.programs), None)
        facts = [f for s in allowed_sources for f in s.facts]
        for values, labels in sentence_labels(body, programs):
            if not labels:
                continue
            ok = any(
                values & fact_values and compatible(labels, fact_labels)
                for fact_values, fact_labels in facts
            )
            if not ok:
                return CheckResult(
                    False,
                    sentence,
                    cited,
                    "number does not match its label",
                    auto,
                    sorted(str(v) for v in values),
                )
    dates = dates_in(body)
    if dates:
        known = (
            set().union(*(s.dates for s in allowed_sources))
            if allowed_sources
            else set()
        )
        wrong = [d for d in dates if not _date_supported(d, known)]
        if wrong:
            y, mo, d = sorted(wrong, key=lambda x: (x[0] or 0, x[1], x[2]))[0]
            return CheckResult(
                False,
                sentence,
                cited,
                "date not in sources",
                auto,
                [f"{y or '?'}-{mo:02d}-{d:02d}"],
            )
        # The date must also be the event the sentence names: when the words next to it belong to
        # another dated event in the sources and not to this date's own label, it is a swap.
        labelled = [x for s in allowed_sources for x in s.date_labels]
        for value, words in _date_windows(body):
            if not words:
                continue
            same = [w for v, w in labelled if _date_supported(value, {v})]
            other = [w for v, w in labelled if not _date_supported(value, {v})]
            if not any(words & w for w in same) and any(words & w for w in other):
                y, mo, d = value
                return CheckResult(
                    False,
                    sentence,
                    cited,
                    "date does not match its label",
                    auto,
                    [f"{y or '?'}-{mo:02d}-{d:02d}"],
                )
    text = sentence
    if auto:
        text = sentence.rstrip() + f" [{cited[0]}]"
    return CheckResult(True, text, cited, None, auto)


def _attribute(body: str, nums: set[Decimal], sources: list[Source]) -> int | None:
    """Deterministic attribution: the one source that contains all the sentence's numbers and
    most of its content words (>= 60%, or >= 40% when the sentence's numbers all match that
    source exactly, which is strong evidence on its own); ties go to the lowest-numbered source."""
    words = content_words(body)
    if not words:
        return None
    scored = []
    for s in sources:
        if nums and not nums <= s.numbers:
            continue
        scored.append((len(words & s.words) / len(words), s.n))
    scored.sort(key=lambda x: (-x[0], x[1]))
    if not scored or scored[0][0] < (0.4 if nums else 0.6):
        return None
    return scored[0][1]


GENERIC = set(
    "university college campus student students program programs programme programmes course courses "
    "admission admissions tell about want know please like give details information need required "
    "there what which when where does have offer offers offered provide provides available get "
    "give take make help any some much many also can "
    # In admissions nearly every source mentions fees and paying; they say nothing about the topic.
    "fee fees cost costs charge charges amount pay paid paying payment payments".split()
)


def _stems(words: set[str]) -> set[str]:
    """Crude stems: drop a plural 's', keep 4 letters ('fees' ~ 'fee', 'apply' ~ 'application')."""
    return {(w[:-1] if len(w) > 3 and w.endswith("s") else w)[:4] for w in words}


def relevant(
    question: str, source_texts: list[str], extra_generic: set[str] = frozenset()
) -> bool:
    """Answer-level relevance (deterministic): the sources the answer cites must share at least one
    topic word with the question (4-letter stems, so 'apply' meets 'application'). Stops a small
    model from answering an off-topic question with an unrelated but well-supported sentence.
    Questions in Indic scripts are not tested (their sources are English)."""
    if _LOCAL_SCRIPT.search(question):
        return True
    topic = content_words(question) - GENERIC - extra_generic
    if not topic:
        return True
    support = (
        set().union(*(content_words(t) for t in source_texts))
        if source_texts
        else set()
    )
    return bool(_stems(topic) & _stems(support))
