"""'Did you mean ...?': for a programme name that was misheard or misspelt, and (related_questions)
for any question the assistant could not answer, from the college's own FAQ questions.

Speech-to-text and typing both mangle programme names ("Nero Space Engineering" for "Aerospace
Engineering", "mechatronix"). The router matches names exactly (router.match_programs), so such a
question finds no programme and the answer goes wrong or nowhere. When no programme matched, the
question's words are compared with each programme's distinctive words (the name without "B. Tech.
in" and the like); a close match becomes a suggested question, with the programme's real name in
place of the misheard words. The visitor confirms with "yes" (engine.py), on every channel.

Deterministic, no model call: 5-70 ms for RUAS's 81 programmes on a laptop, and only run when the
router matched no programme.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import NamedTuple

# Close enough to suggest: "nero space engineering" ~ "aerospace engineering" scores 0.93,
# "civil engineering" ~ "structural engineering" 0.64.
THRESHOLD = 0.8
EXACT = 0.999  # the distinctive words themselves are right
# Programmes this close to the best one are offered together ("X or Y?").
TIE = 0.04
MAX_SUGGESTIONS = 2

_WORD = re.compile(r"[A-Za-zऀ-෿]+|\d+")

# Degree words and joiners: dropped from a name to find its distinctive part, and absorbed into
# the misheard span so "B.Tech, Nero Space Engineering" becomes one programme name, not two.
DEGREE_WORDS = {
    "b",
    "m",
    "tech",
    "btech",
    "mtech",
    "be",
    "me",
    "sc",
    "bsc",
    "msc",
    "ba",
    "ma",
    "bba",
    "mba",
    "bca",
    "mca",
    "bcom",
    "com",
    "mcom",
    "pharm",
    "bpharm",
    "mpharm",
    "pharmd",
    "d",
    "des",
    "bdes",
    "mdes",
    "voc",
    "bvoc",
    "phd",
    "ph",
    "hons",
    "honours",
    "bachelor",
    "bachelors",
    "master",
    "masters",
    "of",
    "in",
    "and",
    "with",
    "a",
    "the",
    "course",
    "programme",
    "program",
    "degree",
    "diploma",
    "pg",
    "ug",
    "bds",
    "mds",
    "mbbs",
    "md",
    "ms",
    "llb",
    "llm",
    "arch",
    "barch",
    "march",
}

# A window that starts or ends on one of these is not a misheard name ("for electronics ...").
FILLER = {
    "for",
    "about",
    "is",
    "are",
    "the",
    "a",
    "an",
    "what",
    "whats",
    "me",
    "tell",
    "of",
    "to",
    "fee",
    "fees",
    "eligibility",
    "does",
    "do",
    "have",
    "has",
    "there",
    "any",
    "your",
    "you",
    "course",
    "i",
    "my",
    "want",
    "how",
    "which",
    "when",
    "and",
    "or",
    "with",
    "at",
    "on",
}
# Degree marks the visitor said ("B.Tech" -> b, tech): among equally close names, the programme
# with the same degree wins (B. Tech. over M. Tech. in Aerospace Engineering).
DEGREE_MARKS = {
    "b",
    "m",
    "tech",
    "btech",
    "mtech",
    "sc",
    "bsc",
    "msc",
    "ba",
    "ma",
    "bba",
    "mba",
    "bca",
    "mca",
    "bcom",
    "mcom",
    "voc",
    "bvoc",
    "pharm",
    "bpharm",
    "mpharm",
    "des",
    "phd",
    "diploma",
    "hons",
    "be",
    "me",
    "bds",
    "mds",
    "arch",
}

AFFIRMATIVE = re.compile(
    r"^\s*(yes|yeah|yea|yep|yup|ya|yah|sure|correct|right|exactly|that'?s right|that one|"
    # (?=...) not \b: a word boundary fails after a Devanagari or Kannada vowel sign ("हाँ").
    r"ok(ay)?|haan ji|haan?|ha|ji|houdu|hudu|sari|ಹೌದು|हाँ|हां|हा)(?=[\s.!,]|$)"
    # One run of spaces and punctuation, then an optional word with its own run: two runs side by
    # side could split the same spaces in many ways (slow on "ya" + many tabs).
    r"[\s.!,]*(?:(please|it is|i did)[\s.!]*)?$",
    re.IGNORECASE,
)
NEGATIVE = re.compile(
    r"^\s*(no|nope|nah|not that|wrong|nahi|nahin|illa|ಇಲ್ಲ|नहीं)(?=[\s.!,]|$)[\s.!,]*$",
    re.IGNORECASE,
)


def _norm(words: list[str]) -> str:
    return " ".join(w.lower() for w in words)


def _core(name: str) -> list[str]:
    """The distinctive words of a programme name: "B. Tech. in Aerospace Engineering" ->
    ["aerospace", "engineering"]."""
    return [
        w.lower()
        for w in _WORD.findall(name)
        if w.lower() not in DEGREE_WORDS and not w.isdigit()
    ]


def _degree(name: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(name)} & DEGREE_MARKS


# Subject words many names share, which a question may leave out ("B.Tech in Aerospace" for
# "B. Tech. in Aerospace Engineering"; seen live on voice, 17 Nov 2026, no answer given).
GENERIC_SUBJECT = {
    "engineering",
    "technology",
    "science",
    "sciences",
    "studies",
    "applied",
}
# Degrees written as one word ("BTech") are the same degree as "B. Tech.".
_JOINED = {
    "btech": ("b", "tech"),
    "mtech": ("m", "tech"),
    "bsc": ("b", "sc"),
    "msc": ("m", "sc"),
    "bcom": ("b", "com"),
    "mcom": ("m", "com"),
    "bvoc": ("b", "voc"),
    "bpharm": ("b", "pharm"),
    "mpharm": ("m", "pharm"),
    "bdes": ("b", "des"),
    "mdes": ("m", "des"),
    "barch": ("b", "arch"),
}


def _marks(words) -> set[str]:
    out: set[str] = set()
    for w in words:
        w = w.lower()
        if w in _JOINED:
            out.update(_JOINED[w])
        elif w in DEGREE_MARKS or w == "com":
            out.add(w)
    return out


def named_by_subject(text: str, programs: list[dict]) -> dict | None:
    """The one programme a question names by its degree and its distinctive subject words, with
    the generic ones left out: "fee for B.Tech in Aerospace" -> B. Tech. in Aerospace Engineering
    (not M.Tech.). None unless a degree is said ("B.Tech", "MBA") and exactly one programme fits
    best, so a question about a subject ("Is the course taught in English?") is never taken for
    a programme (B.A. in English). When several fit, the most specific wins ("B.Tech computer
    science AI ML" -> the AI & ML programme, not plain CSE)."""
    words = [w.lower() for w in _WORD.findall(text)]
    said = _marks(words)
    if not said:
        return None
    present = set(words)
    fits: list[tuple[int, int, dict]] = []
    for p in programs:
        degree = len(said & _marks(_WORD.findall(p["name"])))
        if not degree:
            continue
        core = _core(p["name"])
        subject = {w for w in core if w not in GENERIC_SUBJECT}
        # Or its initials, as people say them: "CSE" for Computer Science and Engineering,
        # "ECE", "EEE", "ISE" (three letters at least: "ME" or "CE" would be guesses).
        initials = "".join(w[0] for w in core)
        if (subject and subject <= present) or (
            len(initials) >= 3 and initials in present
        ):
            fits.append((degree, len(subject), p))
    if not fits:
        return None
    fits.sort(key=lambda f: (-f[0], -f[1]))
    best = [f for f in fits if f[:2] == fits[0][:2]]
    return best[0][2] if len(best) == 1 else None


class Offer(NamedTuple):
    program: dict
    question: str
    # The name's distinctive words were said exactly; only its writing differed ("B.Des Fashion
    # Design" for "B.Des. Fashion Design"). A single exact offer is used without asking.
    exact: bool


def did_you_mean(text: str, programs: list[dict]) -> list[Offer]:
    """Up to two offers (programme, suggested question), best first; [] when nothing is close."""
    tokens = list(_WORD.finditer(text))
    if len(tokens) < 2 or not programs:
        return []
    words = [t.group().lower() for t in tokens]
    scored: list[tuple[float, int, int, dict]] = []
    for p in programs:
        best: tuple[float, int, int] | None = None
        for name in [p["name"], *p.get("aliases", [])]:
            core = _core(name)
            target = " ".join(core)
            if not core or len(target) < 6:
                continue
            for k in {max(1, len(core) - 1), len(core), len(core) + 1}:
                for i in range(0, len(words) - k + 1):
                    window = words[i : i + k]
                    if all(w in DEGREE_WORDS for w in window):
                        continue
                    if window[0] in FILLER or window[-1] in FILLER:
                        continue
                    shown = _norm(window)
                    # A fragment of the name ("technology" in "biotechnology") is not a mishearing.
                    if len(shown.replace(" ", "")) < 5 or (
                        shown in target and shown != target
                    ):
                        continue
                    ratio = SequenceMatcher(None, shown, target).ratio()
                    if best is None or ratio > best[0]:
                        best = (ratio, i, i + k)
        if best and best[0] >= THRESHOLD:
            scored.append((best[0], best[1], best[2], p))
    if not scored:
        return []
    scored.sort(key=lambda x: -x[0])
    top = scored[0][0]
    said = {w for w in words if w in DEGREE_MARKS}
    if said:
        close = [x for x in scored if x[0] >= top - TIE]
        same = max(len(said & _degree(x[3]["name"])) for x in close)
        if same:
            scored = [x for x in close if len(said & _degree(x[3]["name"])) == same]
            top = scored[0][0]
    out: list[Offer] = []
    seen: set[str] = set()
    for ratio, i, j, p in scored:
        if ratio < top - TIE or len(out) >= MAX_SUGGESTIONS or p["name"] in seen:
            continue
        seen.add(p["name"])
        # Absorb degree words just before the misheard span ("B.Tech, Nero Space ...").
        while i > 0 and words[i - 1] in DEGREE_WORDS - {"the", "a", "of"}:
            i -= 1
        start, end = tokens[i].start(), tokens[j - 1].end()
        out.append(Offer(p, text[:start] + p["name"] + text[end:], ratio >= EXACT))
    return out


def phrase(suggestions: list[str]) -> str:
    """One suggestion quoted; several numbered, so "2" (or "the second one") picks one."""
    if len(suggestions) == 1:
        return f"“{suggestions[0].strip()}”"
    return " ".join(f"{i}. “{s.strip()}”" for i, s in enumerate(suggestions, 1))


# ------------------------------------------------------------------ related questions
# When the assistant has no answer (a typo such as "comdk" for "COMEDK", a vague or misheard
# question), the college's own FAQ questions closest to it are offered instead (engine.py). They
# come from Module A's search (the agreed contract): a curated answer reads "Q: ...? A: ...", and a
# page section or title may itself be a question.

RELATED_FLOOR = 0.55  # search score from which a FAQ question may be offered
RELATED_COVER = (
    0.5  # ... if it shares at least half the visitor's key words (typos allowed)
)
MAX_RELATED = 3

_QA = re.compile(r"^\s*Q:\s*(.+?\?)\s*(?:A:|$)", re.S)

# Words that say nothing about the topic.
STOP = {
    "what",
    "whats",
    "is",
    "are",
    "was",
    "the",
    "a",
    "an",
    "of",
    "for",
    "in",
    "on",
    "at",
    "to",
    "and",
    "or",
    "how",
    "do",
    "does",
    "did",
    "can",
    "could",
    "i",
    "my",
    "me",
    "you",
    "your",
    "there",
    "which",
    "when",
    "where",
    "who",
    "whom",
    "why",
    "will",
    "would",
    "be",
    "it",
    "its",
    "this",
    "that",
    "these",
    "those",
    "about",
    "tell",
    "please",
    "any",
    "get",
    "know",
    "want",
    "need",
    "have",
    "has",
    "with",
    "from",
    "by",
    "much",
    "many",
    "give",
    "details",
    "info",
    "information",
    "college",
    "university",
    "campus",
    "programme",
    "program",
    "course",
}


# Common in admission questions but not a topic by themselves: "is ragging allowed" must not
# match "Is fee payment allowed in instalments?" on "allowed" alone.
WEAK = {
    "allowed",
    "available",
    "required",
    "needed",
    "need",
    "eligible",
    "possible",
    "students",
    "student",
    "candidates",
    "apply",
    "admission",
    "admissions",
    "year",
    "first",
    "list",
    "offered",
    "provide",
    "provided",
    "option",
    "options",
    "process",
    "procedure",
    "best",
}


def _key_words(text: str, generic: set[str]) -> list[str]:
    words = [
        w.lower()
        for w in _WORD.findall(text)
        if len(w) > 2 and not w.isdigit() and w.lower() not in STOP | generic
    ]
    topic = [w for w in words if w not in WEAK]
    return topic or words


def _covered(word: str, words: list[str]) -> bool:
    """The word, or a close spelling of it ("comdk" ~ "comedk", "hostle" ~ "hostel")."""
    return any(
        word == w or (len(word) >= 4 and SequenceMatcher(None, word, w).ratio() >= 0.8)
        for w in words
    )


def question_of(item: dict) -> str | None:
    """The question a search hit answers, if it states one."""
    m = _QA.match(item.get("content") or "")
    if m:
        return " ".join(m.group(1).split())
    c = item.get("citation") or {}
    for t in (c.get("title"), (c.get("heading_path") or "").split(">")[-1]):
        t = (t or "").strip()
        if t.endswith("?") and 3 <= len(t.split()) <= 25:
            return t
    return None


def related_questions(
    text: str, items: list[dict], generic: set[str] | None = None
) -> list[str]:
    """Up to three of the college's own questions close to what was asked. Best first: the one
    sharing most of the visitor's key words, then the higher search score, so "yes" picks the
    full match ("COMEDK code") before a related one ("KCET code")."""
    generic = generic or set()
    asked = _key_words(text, generic)
    if not asked:
        return []
    found: list[tuple[float, float, str]] = []
    seen = {_norm(_WORD.findall(text))}
    for item in items:
        score = item.get("vector_score") or 0.0
        q = question_of(item) if score >= RELATED_FLOOR else None
        if not q:
            continue
        key = _norm(_WORD.findall(q))
        if key in seen:
            continue
        words = _key_words(q, generic)
        cover = sum(1 for w in asked if _covered(w, words)) / len(asked)
        if cover >= RELATED_COVER:
            seen.add(key)
            found.append((cover, score, q))
    found.sort(key=lambda x: (-x[0], -x[1]))
    return [q for _, _, q in found[:MAX_RELATED]]


def corrected(original: str, fixed: str | None) -> str | None:
    """A model's spelling fix of the question, if it is one: different words, but still close to
    what was typed (a rewrite or an answer is not a correction). None otherwise."""
    lines = (fixed or "").strip().splitlines()
    fixed = lines[0].strip().strip("\"'“”").strip() if lines else ""
    if not fixed or len(fixed) > 2 * len(original) + 20:
        return None

    def words(s: str) -> str:  # "whats" and "what's" are the same words
        return _norm(_WORD.findall(s.replace("'", "").replace("’", "")))

    a, b = words(original), words(fixed)
    if not b or a == b:
        return None
    return fixed if SequenceMatcher(None, a, b).ratio() >= 0.6 else None


def strip_focus(text: str, programs: list[dict]) -> str:
    """The widget's programme pages send "<Programme name>: question"; the question alone."""
    head, sep, tail = text.partition(": ")
    if sep and tail.strip():
        names = {p["name"].lower() for p in programs}
        if head.strip().lower() in names:
            return tail.strip()
    return text


_ORDINAL = {
    "1": 0,
    "one": 0,
    "first": 0,
    "2": 1,
    "two": 1,
    "second": 1,
    "3": 2,
    "three": 2,
    "third": 2,
}
_PICK = re.compile(
    r"^\s*(?:the\s+|option\s+|number\s+|no\.?\s*)?(1|2|3|one|two|three|first|second|third)"
    r"(?:\s+one)?[\s.!)]*$",
    re.IGNORECASE,
)


def choice(text: str, offered: int) -> int | None:
    """Which suggestion the reply picks: "yes" the first, "2" / "the second one" that one."""
    if AFFIRMATIVE.match(text):
        return 0
    m = _PICK.match(text)
    if m:
        i = _ORDINAL[m.group(1).lower()]
        return i if i < offered else None
    return None
