"""Supervisor/Router (PRD F3.1, F3.1a, 7.2 step 3): rules first; the small model only when the
rules are inconclusive.

Output: one {intent, role, confidence} per sub-question, plus language, sensitive flag and the
handoff decision. Roles are "admission" (process, fees, dates, documents, hostel, scholarships,
transport) and "faculty" (syllabus, curriculum, labs, electives, outcomes, careers, higher studies).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

INTENTS: dict[str, tuple[str, list[str]]] = {
    # intent: (role, keyword patterns)
    "fees": (
        "admission",
        [
            r"\bfees?\b",
            r"\bcost",
            r"tuition",
            r"how much",
            r"\bcharges?\b",
            r"\bpay\b",
            r"फीस",
            r"शुल्क",
            r"kitni",
            r"kitna",
            r"\binstal+ments?\b",
            r"\bcash\b",
            r"ಶುಲ್ಕ",
            r"ಫೀಸ್",
            r"ಎಷ್ಟು",
            r"shulk",
        ],
    ),
    "dates": (
        "admission",
        [
            r"last date",
            r"deadline",
            r"\bwhen\b",
            r"\bdates?\b",
            r"\bopen",
            r"\bclose",
            r"\bstart",
            r"कब",
            r"तारीख",
            r"\bkab\b",
            r"ಕೊನೆಯ ದಿನಾಂಕ",
            r"ದಿನಾಂಕ",
            r"ಯಾವಾಗ",
            r"kone",
            r"dinank",
            r"dinaam",
            r"तिथि",
            r"अंतिम",
        ],
    ),
    "eligibility": (
        "admission",
        [
            r"eligib",
            r"criteria",
            r"\bqualif",
            r"percentage",
            r"marks",
            r"can i (apply|join)",
            r"who can",
            r"lateral",
            r"diploma",
            r"योग्यता",
            r"पात्र",
            r"ಅರ್ಹತೆ",
            r"ಅರ್ಹ",
            r"ಅಂಕ",
        ],
    ),
    "process": (
        "admission",
        [
            r"\bapply\b",
            r"application",
            r"admission process",
            r"\bcounsel",
            r"procedure",
            r"how (do|to|can) i (get|join|take)",
            r"\bcode\b",
            r"entrance",
            r"\bexam\b",
            r"\brank\b",
            r"\bseat",
            r"आवेदन",
            r"एडमिशन",
            r"प्रवेश",
            r"ಅರ್ಜಿ",
            r"ಪ್ರವೇಶ ಪ್ರಕ್ರಿಯೆ",
            r"ಕೌನ್ಸೆಲಿಂಗ್",
            r"\\barji",
        ],
    ),
    "documents": (
        "admission",
        [r"documents?", r"certificates?", r"marks ?cards?", r"\btc\b", r"दस्तावेज"],
    ),
    "hostel": (
        "admission",
        [
            r"hostel",
            r"accommodation",
            r"\bmess\b",
            r"\bfood\b",
            r"\broom",
            r"छात्रावास",
            r"हॉस्टल",
            r"ಹಾಸ್ಟೆಲ್",
            r"ವಸತಿ ನಿಲಯ",
            r"ವಸತಿ",
        ],
    ),
    "scholarship": (
        "admission",
        [
            r"scholarship",
            r"concession",
            r"waiver",
            r"financial aid",
            r"\bloan",
            r"छात्रवृत्ति",
            r"ವಿದ್ಯಾರ್ಥಿವೇತನ",
            r"ಸ್ಕಾಲರ್‌ಶಿಪ್",
        ],
    ),
    "transport": ("admission", [r"\bbus\b", r"transport", r"commute"]),
    "contact": (
        "admission",
        [
            r"\bcontact\b",
            r"phone number",
            r"e-?mail",
            r"\baddress\b",
            r"office hours",
            r"where is",
            r"location",
            r"ಬಸ್",
            r"ಸಾರಿಗೆ",
        ],
    ),
    "programs": (
        "admission",
        [
            r"programmes?",
            r"programs?",
            r"courses?",
            r"branches",
            r"\bintake\b",
            r"duration",
            r"how long",
        ],
    ),
    "curriculum": (
        "faculty",
        [
            r"syllabus",
            r"curriculum",
            r"subjects?",
            r"semesters?",
            r"credits?",
            r"\bteach",
            r"pedagogy",
            r"पाठ्यक्रम",
            r"सिलेबस",
        ],
    ),
    "labs": (
        "faculty",
        [r"\blabs?\b", r"laborator", r"workshop", r"equipment", r"\bgpus?\b"],
    ),
    "electives": (
        "faculty",
        [
            r"electives?",
            r"specialis",
            r"specializ",
            r"\bminor\b",
            r"\bai\b",
            r"machine learning",
            r"data science",
        ],
    ),
    "careers": (
        "faculty",
        [
            r"career",
            r"\bjobs?\b",
            r"placement",
            r"recruit",
            r"salary",
            r"package",
            r"higher stud",
            r"\bm\.?s\b abroad",
            r"\bgate\b",
            r"\bgre\b",
            r"internship",
            r"projects?",
            r"नौकरी",
            r"प्लेसमेंट",
        ],
    ),
}
_COMPILED = {
    k: (role, [re.compile(p, re.I) for p in pats])
    for k, (role, pats) in INTENTS.items()
}

EXPLICIT_HUMAN = re.compile(
    r"\b(talk|speak|chat) (to|with) (a |an )?(human|person|someone|real person|officer|counsell?or|staff|faculty)"
    r"|\b(human|real person|agent) please\b|\bcall me\b|\bcall ?back\b|\bconnect me\b"
    r"|किसी (इंसान|व्यक्ति) से बात|मुझे कॉल|insaan se baat|call karo",
    re.IGNORECASE,
)
_SPLIT = re.compile(
    r"\?\s+|\s+(?:and also|also|and|plus|aur|और)\s+(?=(?:what|how|is|are|can|do|does|which|when|"
    r"where|will|tell|kya|क्या)\b)",
    re.IGNORECASE,
)


@dataclass
class Route:
    intent: str
    role: str  # admission | faculty
    confidence: float
    text: str
    department: str | None = None


@dataclass
class RouteDecision:
    routes: list[Route]
    language: str
    sensitive: str | None = None  # matched sensitive topic
    explicit_human: bool = False
    decided_by: str = "rules"  # rules | model | default
    programs: list[dict] = field(default_factory=list)

    @property
    def handoff(self) -> bool:
        return self.explicit_human or self.sensitive is not None

    @property
    def roles(self) -> list[str]:
        return sorted({r.role for r in self.routes})

    def as_dict(self) -> dict:
        return {
            "routes": [r.__dict__ for r in self.routes],
            "language": self.language,
            "sensitive": self.sensitive,
            "explicit_human": self.explicit_human,
            "decided_by": self.decided_by,
            "programs": [p["code"] for p in self.programs],
        }


# Short aliases that are also everyday words ("ME" for Mechanical, "IT"): in lower case they are
# the word ("tell me the fee"), so they count only when written as the alias is written.
_WORD_ALIASES = {
    "me",
    "it",
    "is",
    "am",
    "an",
    "as",
    "at",
    "be",
    "by",
    "do",
    "go",
    "he",
    "if",
    "in",
    "no",
    "of",
    "on",
    "or",
    "so",
    "to",
    "up",
    "us",
    "we",
    "my",
    "hi",
    "ok",
    "art",
    "arts",
    "law",
    "can",
}


# Compiled name patterns per catalogue. Compiling them on every call was the slowest step of a turn
# with a large catalogue (111 programmes, ~700 names; Python's own regex cache holds 512, so
# every call recompiled almost all of them: 40-50 ms a call, 100-200 calls a turn, 4-7 s).
_MATCHERS: dict[tuple, list[tuple[int, str, bool, re.Pattern]]] = {}
_MATCHERS_MAX = 16


def _matchers(programs: list[dict]) -> list[tuple[int, str, bool, re.Pattern]]:
    key = tuple((p["code"], p["name"], tuple(p.get("aliases", []))) for p in programs)
    found = _MATCHERS.get(key)
    if found is None:
        found = []
        for i, p in enumerate(programs):
            for name in [p["code"], p["name"], *p.get("aliases", [])]:
                raw = name.strip()
                n = raw.lower()
                if len(n) < 2:
                    continue
                exact = n in _WORD_ALIASES  # matched as written, not in lower case
                needle = raw if exact else n
                found.append(
                    (
                        i,
                        needle,
                        exact,
                        re.compile(rf"(?<![\w.]){re.escape(needle)}(?![\w])"),
                    )
                )
        if len(_MATCHERS) >= _MATCHERS_MAX:
            _MATCHERS.pop(next(iter(_MATCHERS)))
        _MATCHERS[key] = found
    return found


def match_programs(text: str, programs: list[dict]) -> list[dict]:
    """Programs named in the text, by code, name or alias (e.g. 'CSE', 'MBA', 'Mechanical'). The
    longest name wins: "M.Tech Computer Science and Engineering" is not also B.Tech's alias
    "Computer Science"."""
    low = text.lower()
    hits: list[tuple[int, int, int]] = []
    for i, needle, exact, pattern in _matchers(programs):
        hay = text if exact else low
        if needle not in hay:  # no regex for names that do not occur at all
            continue
        hits.extend((m.start(), m.end(), i) for m in pattern.finditer(hay))
    kept = {
        i
        for start, end, i in hits
        if not any(
            other != i and s <= start and end <= e and e - s > end - start
            for s, e, other in hits
        )
    }
    return [p for i, p in enumerate(programs) if i in kept]


def _classify(text: str) -> list[tuple[str, str, int]]:
    hits = []
    for intent, (role, pats) in _COMPILED.items():
        n = sum(1 for p in pats if p.search(text))
        if n:
            hits.append((intent, role, n))
    hits.sort(key=lambda h: -h[2])
    return hits


def _route_part(text: str, programs: list[dict]) -> Route | None:
    hits = _classify(text)
    if not hits:
        return None
    intent, role, n = hits[0]
    roles = {h[1] for h in hits}
    # Faculty intents win over generic admission words ("which subjects are in CSE")
    if len(roles) > 1:
        fac = [h for h in hits if h[1] == "faculty"]
        adm = [h for h in hits if h[1] == "admission"]
        if fac[0][2] >= adm[0][2]:
            intent, role, n = fac[0]
    confidence = min(0.95, 0.6 + 0.15 * n) if len(roles) == 1 else 0.6
    progs = match_programs(text, programs)
    dept = progs[0].get("department") if progs and role == "faculty" else None
    return Route(intent, role, confidence, text.strip(), dept)


def route(
    text: str,
    language: str,
    sensitive_topics: list[str],
    programs: list[dict],
    previous_programs: list[dict] | None = None,
) -> RouteDecision:
    progs = match_programs(text, programs) or list(previous_programs or [])
    low = text.lower()
    sensitive = next(
        (t for t in sensitive_topics if re.search(rf"\b{re.escape(t.lower())}", low)),
        None,
    )
    explicit = bool(EXPLICIT_HUMAN.search(text))
    parts = [p for p in _SPLIT.split(text) if p and len(p.split()) >= 2] or [text]
    routes = [r for r in (_route_part(p, programs) for p in parts) if r]
    # Merge sub-questions that landed on the same role and intent.
    merged: dict[tuple[str, str], Route] = {}
    for r in routes:
        key = (r.intent, r.role)
        if key in merged:
            merged[key].text += " " + r.text
        else:
            merged[key] = r
    routes = list(merged.values())
    decided = "rules"
    if not routes:
        decided = "default"
        routes = [Route("general", "admission", 0.4, text.strip())]
    if progs:
        for r in routes:
            if r.role == "faculty" and r.department is None:
                r.department = progs[0].get("department")
    return RouteDecision(routes, language, sensitive, explicit, decided, progs)


ROUTER_PROMPT = """Classify the admission enquiry. Reply with one word only:
ADMISSION - fees, dates, eligibility, documents, application process, hostel, scholarships, transport, contact
FACULTY - syllabus, curriculum, subjects, labs, electives, teaching, careers, placements, higher studies
OTHER - anything unrelated to studying at the college

Enquiry: {q}
Answer:"""


def parse_model_route(reply: str) -> str | None:
    word = (reply or "").strip().split()
    w = word[0].upper().strip(".:") if word else ""
    return {"ADMISSION": "admission", "FACULTY": "faculty", "OTHER": "other"}.get(w)
