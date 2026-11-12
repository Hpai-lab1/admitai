"""Personal data never enters the learning loop (PRD 11.4: feedback, eval data and proposals are
kept PII-free). Signals arrive with visitors' own words, so contact details, ID numbers and
self-introductions are masked before anything is stored, clustered or shown in a proposal.

Regex masking is a floor, not a guarantee: a name written without an introduction ("Priya here")
gets through. Erasure on request (erase_conversations) covers what masking misses."""

from __future__ import annotations

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
_AADHAAR = re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)")
# "my name is Priya Sharma", "I am Rahul", "this is Anil" (capitalised words after an introduction)
_INTRO = re.compile(
    r"((?:\bmy name is|\bmy name's|\bi am|\bi'm|^this is|\bmera naam)\s+)"
    r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}",
    re.IGNORECASE,
)
# Words after "I am" that are not names ("I am interested", "I'm a parent").
_NOT_NAMES = {
    "a",
    "an",
    "the",
    "interested",
    "looking",
    "planning",
    "from",
    "not",
    "in",
    "applying",
    "confused",
    "worried",
    "student",
    "parent",
    "currently",
    "studying",
}


def _intro(m: re.Match) -> str:
    lead = m.group(1)
    words = m.group(0)[len(lead) :].split(" ")
    explicit = lead.lower().startswith(("my name", "mera naam"))
    if not explicit and (words[0].lower() in _NOT_NAMES or not words[0][0].isupper()):
        return m.group(0)
    n = 1  # the name: the first word, then any further capitalised words
    while n < len(words) and words[n][:1].isupper():
        n += 1
    return " ".join([lead + "[name]", *words[n:]]).rstrip()


def mask_pii(text: str | None) -> str | None:
    if not text:
        return text
    text = _EMAIL.sub("[email]", text)
    text = _AADHAAR.sub("[id-number]", text)
    text = _PHONE.sub("[phone]", text)
    return _INTRO.sub(_intro, text)
