"""Input guardrail (PRD 7.2 step 2, 7.5: no LLM, < 50 ms): PII masking for logs, prompt-injection
and abuse detection, language detection, per-session rate limiting."""

from __future__ import annotations

import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
_AADHAAR = re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)")

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
    """For logs and traces only; the visitor's own message is shown back to them unmasked."""
    text = _EMAIL.sub("[email]", text)
    text = _AADHAAR.sub("[id-number]", text)
    return _PHONE.sub("[phone]", text)


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
