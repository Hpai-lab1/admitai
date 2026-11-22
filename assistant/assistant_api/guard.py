"""Input guardrail (PRD 7.2 step 2, 7.5: no LLM, < 50 ms): PII masking for logs, prompt-injection
and abuse detection, language detection, per-session rate limiting."""

from __future__ import annotations

import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

# The part before "@" is at most 64 characters (the limit for a real address), so a long word
# with no "@" costs 64 steps per letter instead of the whole word per letter (quadratic before;
# CodeQL py/polynomial-redos). Matches are unchanged for every address of a legal length.
_EMAIL = re.compile(r"[\w.+-]{1,64}@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
_AADHAAR = re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)")

# Secrets a visitor may type: the digits after OTP / PIN / password / verification code, and the
# word after "password is". Seen live (18 Nov 2026): "My OTP is 482913" reached the analytics page.
# The bare word "code" is left alone, so college codes ("COMEDK code E197") are not touched.
_SECRET_CODE = re.compile(
    r"\b(otp|one[- ]time (?:password|code)|pin|passcode|password|verification code|security code)"
    r"(\W{1,3}(?:is\W{1,2}|was\W{1,2})?)(\d{4,8})\b",
    re.IGNORECASE,
)
_PASSWORD = re.compile(
    r"\b(password|passcode)(\s+(?:is|was)\s+|\s*[:=]\s*)(\S{1,64})", re.IGNORECASE
)
# Another person's records: "the marks of Rahul", "results for Priya Sharma" (a capitalised name).
_RECORD_OF = re.compile(
    r"\b((?:marks|results?|grades?|scores?|attendance|records?|report card|details)\s+(?:of|for)\s+)"
    r"([A-Z][a-z]{1,20}(?:\s[A-Z][a-z]{1,20}){0,2})\b"
)
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


_INJECTION = re.compile(
    r"ignore (all |any |the )?(previous|above|prior) (instructions|rules|prompts?)"
    r"|disregard (your|the|all) (instructions|rules|system prompt)"
    r"|(reveal|show|print|repeat) (me )?(your|the) (system )?(prompt|instructions)"
    r"|you are now (a |an )?(?!student)"
    r"|act as (a |an )?(different|unrestricted|jailbroken|dan\b)"
    r"|developer mode|jailbreak|\bDAN\b"
    r"|pretend (you are|to be) (?!a student)",
    re.IGNORECASE,
)
_ABUSE = re.compile(
    r"\b(fuck|f\*ck|shit|bitch|bastard|idiot|stupid bot|madarchod|bhenchod|chutiya|harami)\b",
    re.IGNORECASE,
)
# Common Hindi words written in Latin letters (Hinglish), for language detection.
_HINGLISH = re.compile(
    r"\b(kya|hai|hain|kitni|kitna|kaise|kab|kahan|mujhe|mera|meri|aap|batao|bataiye|chahiye|"
    r"milega|milegi|nahi|haan|ki|ka|ke|ko|se|liye|kaun|fees? kitni)\b",
    re.IGNORECASE,
)
# Common Kannada words written in Latin letters ("B.Tech fees eshtu?", "hostel ide na?"). One
# word that is never English is enough; short words that also occur in English need a second one.
_KANGLISH_STRONG = re.compile(
    r"\b(eshtu|eshto|yeshtu|yaavaga|yavaga|yaava|hege|hegge|ideya|idya|illva|beku|bekagutte|"
    r"nanage|nange|namge|namage|nimma|nimge|nimage|gottilla|swalpa|tumba|yelli|sigutte|siguthe|"
    r"aagutte|agutte|aagatte|hogutte|bartini|saaku|houdu|howdu|magalu|shulka|dinanka|avadhi|"
    r"koneya|yavudu|yavudhu|sallisalu|salislu)\b",
    re.IGNORECASE,
)
_KANGLISH_WEAK = re.compile(
    r"\b(ide|illa|enu|yenu|kodi|maadi|madi|elli|heli|sari|guru|anna|akka|appa|amma|maga|na)\b",
    re.IGNORECASE,
)


def mask_pii(text: str) -> str:
    """For logs, traces, analytics and anything sent to an outside model; the visitor's own message is
    shown back to them unmasked. The same rules as Module A's `pii.mask_pii` (keep them in step)."""
    text = _EMAIL.sub("[email]", text)
    text = _SECRET_CODE.sub(lambda m: m.group(1) + m.group(2) + "[code]", text)
    text = _PASSWORD.sub(lambda m: m.group(1) + m.group(2) + "[secret]", text)
    text = _RECORD_OF.sub(lambda m: m.group(1) + "[name]", text)
    text = _AADHAAR.sub("[id-number]", text)
    text = _PHONE.sub("[phone]", text)
    return _INTRO.sub(_intro, text)


LANGUAGES = ("en", "hi", "kn")


def detect_language(text: str, requested: str | None = None) -> str:
    """The language the visitor chose in the widget (EN, हिं, ಕ) always wins: every reply is in it
    (requested 10 Nov 2026). Without a choice (WhatsApp, API callers): 'hi' for Devanagari or clear
    Hinglish, 'kn' for Kannada script or clear Kannada in Latin letters, else 'en'."""
    if requested in LANGUAGES:
        return requested
    if re.search(r"[ऀ-ॿ]", text):
        return "hi"
    if re.search(r"[ಀ-೿]", text):
        return "kn"
    if len(_HINGLISH.findall(text)) >= 2:
        return "hi"
    weak = len(_KANGLISH_WEAK.findall(text))
    if _KANGLISH_STRONG.search(text) or weak >= 2:
        return "kn"
    return "en"


@dataclass
class GuardResult:
    ok: bool
    reason: str | None  # injection | abuse | empty | too_long
    language: str
    masked: str


def check_input(text: str, requested_language: str | None = None) -> GuardResult:
    stripped = text.strip()
    language = detect_language(stripped, requested_language)
    masked = mask_pii(stripped)
    if not stripped:
        return GuardResult(False, "empty", language, masked)
    if len(stripped) > 2000:
        return GuardResult(False, "too_long", language, masked)
    if _INJECTION.search(stripped):
        return GuardResult(False, "injection", language, masked)
    if _ABUSE.search(stripped):
        return GuardResult(False, "abuse", language, masked)
    return GuardResult(True, None, language, masked)


class RateLimiter:
    """Sliding window per key (session or IP): at most `limit` messages per `window` seconds."""

    def __init__(self, limit: int = 8, window: float = 60.0):
        self.limit, self.window = limit, window
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def retry_after(self, key: str, now: float | None = None) -> int:
        """Whole seconds until `key` may send again (for the Retry-After header)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits.get(key)
            if not q or len(q) < self.limit:
                return 0
            return max(1, int(q[0] + self.window - now + 0.999))


# Questions the assistant must never answer, so they are never shown to staff as trends or gaps
# (injection, account secrets, another person's records, guarantees). The same rules as Module A's
# `by_design.not_answerable_by_design`; keep them in step.
_BY_DESIGN = re.compile(
    r"\bignore (?:all |your |the )?(?:previous |above |prior )?(?:instructions|rules)\b"
    r"|\b(?:system|hidden) prompt\b|\bjailbreak\b|\bpretend (?:you are|to be)\b"
    r"|\b(?:password|passcode|otp|one[- ]time (?:password|code)|login (?:id|details)|log ?in to)\b"
    r"|\bportal (?:password|login|account)\b|\[code\]|\[secret\]"
    r"|\b(?:marks|results?|grades?|scores?|attendance|records?|report card)\s+(?:of|for)\s+"
    r"(?:\[name\]|[A-Z][a-z]{1,20})"
    r"|\bguarantee(?:d)?\b.{0,40}\b(?:seat|admission|placement|scholarship|job)\b"
    r"|\b(?:seat|admission|placement|scholarship|job)\b.{0,40}\bguarantee(?:d)?\b",
    re.IGNORECASE,
)
_BY_DESIGN_RECORD = re.compile(
    r"\b(?:marks|results?|grades?|scores?|attendance|records?|report card)\s+(?:of|for)\s+[A-Z][a-z]"
)


def not_answerable_by_design(question: str | None) -> bool:
    """True for a question no curated answer should ever be written for."""
    if not question:
        return False
    q = question[:2000]
    m = _BY_DESIGN.search(q)
    if not m:
        return False
    # "marks of" needs a capitalised name (case-sensitive), not "marks for admission".
    hit = m.group(0).lower()
    if hit.startswith(
        ("marks", "result", "grade", "score", "attendance", "record", "report")
    ):
        return "[name]" in m.group(0) or bool(_BY_DESIGN_RECORD.search(q))
    return True
