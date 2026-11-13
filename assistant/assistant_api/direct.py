"""Catalogue questions answered straight from the catalogue, without the model.

"What is the fee for B.Tech CSE?" has one exact answer in Module A's catalogue, the authoritative
source the sentence checks compare against anyway. Asking the model to rewrite it took 9-12 s on
a laptop and now and then added something ("for M.Tech" on a fee every programme shares, or an
invented eligibility sentence). Here such a question is answered from the catalogue in well under
a second, with its citation, using the catalogue's values exactly as stored.

English and Kannada (9 Nov 2026). Kannada answers come from fixed templates filled with the
catalogue's numbers, because the 3B model's own Kannada was garbled and once gave the hostel fee
as the B.Tech fee (the number checks bind labels only in English: roadmap item 22). The Kannada
wording was written without a native speaker and needs review before real use.

Deliberately narrow: one question; a fee, the duration or an admission date; one programme, or a
fee every programme shares. Anything that needs judgement (scholarships, refunds, instalments,
what a fee includes, a category or quota, a comparison, two things at once) goes to the model.
"""

from __future__ import annotations

import re
from datetime import date

from .context import Source, _fee_name
from .display import duration, money, period

LANGUAGES = ("en", "hi", "kn")

# Kannada has no word boundaries for re's \b (its vowel signs are not \w), so its words are
# matched as plain substrings.
# ("ಎಷ್ಟು" is "how much" and "how many" alike, so it does not make a question a fee question.)
# Kannada as the speech model writes it in English letters is matched too ("shulka", "avadhi",
# "Arji Salislu Kone Yadinaam": the last date to apply). Stems, because spellings vary.
FEE_Q = re.compile(
    r"\b(fees?|cost|tuition|how much|charges?)\b|ಶುಲ್ಕ|ಫೀಸ್|shulk|फीस|शुल्क", re.I
)
DURATION_Q = re.compile(
    r"\b(how long|duration|how many (years|months))\b|ಅವಧಿ|ಎಷ್ಟು ವರ್ಷ|ಎಷ್ಟು ತಿಂಗಳು|avadh"
    r"|अवधि|कितने साल|कितने वर्ष|कितने महीने",
    re.I,
)
DATE_Q = re.compile(
    r"\b(last date|deadline|closing date|apply by|when (do|does|will|is|are)|start|open)\b"
    r"|ದಿನಾಂಕ|ಯಾವಾಗ|ಕೊನೆಯ|kone|dinank|dinaam|yaavaga|yavaga|तिथि|तारीख|कब|अंतिम|आखिरी",
    re.I,
)
NEEDS_JUDGEMENT = re.compile(
    r"\b(scholarships?|waivers?|concessions?|discounts?|refunds?|instal+ments?|emi|loans?|reduce|"
    r"cheaper|includ\w*|food|mess|laundry|why|compare|vs|versus|both|each|every|hidden|extra|"
    r"late|fines?|penalt\w*|nri|foreign|international|quota|categor\w*|obc|sc|st|ews|sports|"
    r"change|increase|next year|last year|per semester|semester|total|overall|whole)\b"
    r"|ವಿದ್ಯಾರ್ಥಿವೇತನ|ಸ್ಕಾಲರ್|ರಿಯಾಯಿತಿ|ಮರುಪಾವತಿ|ಕಂತು|ಸಾಲ|ಸೇರಿ|ಒಳಗೊಂಡ|ಊಟ|ವರ್ಗ|ಮೀಸಲಾತಿ|"
    r"ಒಟ್ಟು|ಸೆಮಿಸ್ಟರ್|ಹೆಚ್ಚಳ|ದಂಡ|ಏಕೆ|ಹೋಲಿಸ"
    r"|छात्रवृत्ति|स्कॉलरशिप|रियायत|छूट|वापसी|रिफंड|किस्त|लोन|ऋण|शामिल|खाना|भोजन|श्रेणी|कोटा|"
    r"आरक्षण|कुल|सेमेस्टर|क्यों|तुलना|जुर्माना",
    re.I,
)
# Two questions in one ("the fee and the duration", "B.Tech or M.Tech"): the model splits them.
# A follow-up may start with "and" ("and what about its hostel fee?").
_TWO_THINGS = re.compile(r"(?<!^)\b(and|or)\b|ಮತ್ತು|ಅಥವಾ| और | या ", re.I)

# Fee components as a Kannada question names them, and as a Kannada answer says them.
_COMPONENT_WORDS = {
    "hostel": ("hostel", "ಹಾಸ್ಟೆಲ್", "ವಸತಿ", "हॉस्टल", "छात्रावास"),
    "tuition": ("tuition", "ಬೋಧನ", "ಟ್ಯೂಷನ್", "ट्यूशन", "शिक्षण"),
    "application": ("application", "ಅರ್ಜಿ", "आवेदन"),
}
_KN_COMPONENT = {
    "hostel": "ಹಾಸ್ಟೆಲ್ ಶುಲ್ಕ",
    "tuition": "ಬೋಧನಾ ಶುಲ್ಕ",
    "application": "ಅರ್ಜಿ ಶುಲ್ಕ",
}
_KN_PERIOD = {
    "per year": "ಪ್ರತಿ ವರ್ಷಕ್ಕೆ",
    "per annum": "ಪ್ರತಿ ವರ್ಷಕ್ಕೆ",
    "one time": "ಒಂದೇ ಬಾರಿ",
    "once": "ಒಂದೇ ಬಾರಿ",
}
# Hindi (10 Nov 2026), written without a native speaker: have it reviewed before real use.
_HI_COMPONENT = {
    "hostel": "हॉस्टल फीस",
    "tuition": "ट्यूशन फीस",
    "application": "आवेदन शुल्क",
}
_HI_PERIOD = {
    "per year": "प्रति वर्ष",
    "per annum": "प्रति वर्ष",
    "one time": "एक बार",
    "once": "एक बार",
}
# Admission dates: which calendar entry a question asks about, and how each language says it.
_DATE_KINDS = [
    (
        "last",
        re.compile(r"last date|deadline|closing|apply by|ಕೊನೆಯ|kone|अंतिम|आखिरी", re.I),
        "last date",
    ),
    ("open", re.compile(r"\bopen|\bstart|ಆರಂಭ|ಪ್ರಾರಂಭ|ತೆರೆ|शुरू|खुल", re.I), "open"),
    ("counselling", re.compile(r"counsel|ಕೌನ್ಸೆಲಿಂಗ್|काउंसलिंग", re.I), "counselling"),
]
_ADMISSION = re.compile(
    r"applica|admission|ಅರ್ಜಿ|ಪ್ರವೇಶ|arji|pravesh|आवेदन|प्रवेश|एडमिशन", re.I
)
_DATE_TEXT = {
    "en": {
        "last": "The last date to apply is {d} [{n}].",
        "open": "Applications open on {d} [{n}].",
        "counselling": "The first counselling round is on {d} [{n}].",
    },
    "kn": {
        "last": "ಅರ್ಜಿ ಸಲ್ಲಿಸಲು ಕೊನೆಯ ದಿನಾಂಕ {d} [{n}].",
        "open": "ಅರ್ಜಿಗಳು {d} ರಿಂದ ತೆರೆಯುತ್ತವೆ [{n}].",
        "counselling": "ಮೊದಲ ಕೌನ್ಸೆಲಿಂಗ್ ಸುತ್ತು {d} ರಂದು ನಡೆಯುತ್ತದೆ [{n}].",
    },
    "hi": {
        "last": "आवेदन की अंतिम तिथि {d} है [{n}].",
        "open": "आवेदन {d} से शुरू होते हैं [{n}].",
        "counselling": "पहला काउंसलिंग राउंड {d} को है [{n}].",
    },
}


def _source(sources: list[Source], ref: str) -> Source | None:
    return next(
        (
            s
            for s in sources
            if s.citation.get("source_type") == "catalogue"
            and s.citation.get("ref_id") == ref
        ),
        None,
    )


def _human_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%B')} {d.year}"  # as the catalogue source says it


def _named_components(q: str, fees: list[dict]) -> list[dict]:
    low = q.lower()
    out = []
    for f in fees:
        c = (f.get("component") or "").lower()
        words = _COMPONENT_WORDS.get(c, (c,))
        if c and any(w in low for w in words):
            out.append(f)
    return out


def _pick_rows(fees: list[dict]) -> list[tuple[dict, bool]] | None:
    """One row per component; with several categories the general one (and say others differ).
    None when there is no single general row: the model's call."""
    by_component: dict[str, list[dict]] = {}
    for f in fees:
        by_component.setdefault(f.get("component") or "", []).append(f)
    rows = []
    for options in by_component.values():
        if len(options) == 1:
            rows.append((options[0], False))
            continue
        general = [f for f in options if (f.get("category") or "").lower() == "general"]
        if len(general) != 1:
            return None
        rows.append((general[0], True))
    # Tuition first, as applicants expect; the rest in catalogue order.
    order = {"tuition": 0, "hostel": 1}
    rows.sort(key=lambda r: order.get((r[0].get("component") or "").lower(), 2))
    return rows


def _fee_text(rows: list[tuple[dict, bool]], language: str) -> str:
    if language == "hi":
        parts = []
        for f, several in rows:
            c = (f.get("component") or "").lower()
            name = _HI_COMPONENT.get(c, _fee_name(f["component"]))
            per = period(f.get("period"))
            per = _HI_PERIOD.get(per.lower(), per)
            parts.append(
                name
                + (" (सामान्य श्रेणी)" if several else "")
                + (f" {per}" if per else "")
                + f" {money(f['amount'], f['currency'])}"
            )
        if any(several for _, several in rows):
            parts[-1] += "; अन्य श्रेणियों के लिए फीस अलग है"
        return "; ".join(parts)
    if language == "kn":
        parts = []
        for f, several in rows:
            c = (f.get("component") or "").lower()
            name = _KN_COMPONENT.get(c, _fee_name(f["component"]))
            per = period(f.get("period"))
            per = _KN_PERIOD.get(per.lower(), per)
            parts.append(
                name
                + (" (ಸಾಮಾನ್ಯ ವರ್ಗ)" if several else "")
                + (f" {per}" if per else "")
                + f" {money(f['amount'], f['currency'])}"
            )
        if any(several for _, several in rows):
            parts[-1] += "; ಇತರ ವರ್ಗಗಳಿಗೆ ಶುಲ್ಕ ಬೇರೆ ಇರುತ್ತದೆ"
        return "; ".join(parts)
    parts = [
        f"the {_fee_name(f['component']).lower()}"
        + (" (general category)" if several else "")
        + f" is {money(f['amount'], f['currency'])}"
        + (f" {period(f.get('period'))}" if period(f.get("period")) else "")
        for f, several in rows
    ]
    if any(several for _, several in rows):
        parts[-1] += "; other categories differ"
    return "; ".join(parts)


def _dates_answer(
    q: str, language: str, entries: list[dict], src: Source
) -> str | None:
    kinds = [k for k, rx, _ in _DATE_KINDS if rx.search(q)]
    if len(kinds) != 1:
        return None  # which date is meant is not clear: the model's call
    kind = kinds[0]
    if kind == "open" and not _ADMISSION.search(q):
        return None  # "open" about something else
    word = next(w for k, _, w in _DATE_KINDS if k == kind)
    hits = [e for e in entries if word in (e.get("label") or "").lower()]
    if len(hits) != 1:
        return None
    return _DATE_TEXT[language][kind].format(d=_human_date(hits[0]["date"]), n=src.n)


def _plain(q: str, programs: list[dict]) -> str:
    """The question without the programme's own name and "general category": "Computer Science
    and Engineering" is one programme, not two questions, and the general category is what a
    plain fee answer quotes (it went to the model, 3-5 s, instead of the catalogue)."""
    out = q
    for p in programs:
        for n in sorted(
            {p.get("name") or "", p.get("code") or "", *(p.get("aliases") or [])},
            key=len,
            reverse=True,
        ):
            if len(n) >= 3:
                out = re.sub(re.escape(n), " ", out, flags=re.I)
    return re.sub(r"\(?\bgeneral category\b\)?", " ", out, flags=re.I)


def answer(
    text: str,
    language: str,
    routes: list,
    programs: list[dict],
    sources: list[Source],
    catalogue: dict | None = None,
) -> str | None:
    """The answer with its source marker ("... [1]."), or None to leave it to the model."""
    q = (text or "").strip()
    if language not in LANGUAGES or len(routes) != 1 or len(programs) > 1:
        return None
    if NEEDS_JUDGEMENT.search(_plain(q, programs)) or _TWO_THINGS.search(
        _plain(q, programs).lstrip().lower()
    ):
        return None
    catalogue = catalogue or {}
    is_fee, is_duration = bool(FEE_Q.search(q)), bool(DURATION_Q.search(q))

    # An admission date (applications open, last date, counselling), from the calendar. Only
    # for a question routed to admission dates: "When does the hostel open?" is not one.
    if (
        DATE_Q.search(q)
        and not is_fee
        and not is_duration
        and not programs
        and routes[0].intent in ("dates", "process")
    ):
        src = _source(sources, "calendar")
        return (
            _dates_answer(q, language, catalogue.get("general_dates") or [], src)
            if src
            else None
        )

    # A fee every programme shares ("What is the hostel fee?"), with no programme named.
    if not programs:
        items = catalogue.get("items") or []
        src = _source(sources, "fees")
        if not is_fee or not items or src is None:
            return None
        shared = []
        for f in _named_components(q, [x for p in items for x in p.get("fees") or []]):
            same = [
                x
                for p in items
                for x in p.get("fees") or []
                if x.get("component") == f.get("component")
                and x.get("category") == f.get("category")
            ]
            if len(same) == len(items) and len({x["amount"] for x in same}) == 1:
                shared.append(f)
        # The same fee appears once per programme: keep one of each.
        one_each = {
            (f.get("component"), f.get("category"), f["amount"]): f for f in shared
        }
        rows = _pick_rows(list(one_each.values()))
        if not rows or len(rows) != 1:
            return None
        if language == "kn":
            return f"ಎಲ್ಲ ಕೋರ್ಸ್‌ಗಳಿಗೂ {_fee_text(rows, 'kn')} [{src.n}]."
        if language == "hi":
            return f"सभी कोर्सों के लिए {_fee_text(rows, 'hi')} [{src.n}]."
        return f"For every programme, {_fee_text(rows, 'en')} [{src.n}]."

    p = programs[0]
    src = _source(sources, p.get("code", ""))
    if src is None:
        return None
    name, year = p["name"], p.get("academic_year")
    where = f"{name} ({year})" if year else name

    if is_duration and not is_fee:
        months = p.get("duration_months")
        if not months:
            return None
        if language == "kn":
            return f"{where} ಕೋರ್ಸ್‌ನ ಅವಧಿ {months} ತಿಂಗಳು [{src.n}]."
        if language == "hi":
            return f"{where} की अवधि {months} महीने है [{src.n}]."
        return f"{where} lasts {duration(months)} [{src.n}]."

    if not is_fee:
        return None
    fees = p.get("fees") or []
    if not fees:
        return None
    rows = _pick_rows(_named_components(q, fees) or fees)
    if not rows:
        return None
    if language in ("kn", "hi"):
        return f"{where}: {_fee_text(rows, language)} [{src.n}]."
    return f"For {where}, {_fee_text(rows, 'en')} [{src.n}]."
