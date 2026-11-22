"""The language of a turn (PRD F1.6): what the visitor wrote or said in (the input language) is kept
apart from what the reply is in (the response language), and the visitor's preferences are kept
in the conversation, so a switch in the middle of a conversation loses nothing said before.

The response language, in order (`decide`):
1. Asked for in the conversation ("reply in Kannada", "हिंदी में बताइए", "English please"): that
   language from then on, whatever the next question is written in, until asked again.
2. A question in Hindi or Kannada (script, or written in English letters, alone or mixed with
   English words): the same language.
3. A clear English question ("What is the fee for B.Tech?"): English, whatever the widget is set
   to, as Hindi and Kannada questions get their own language (decided 17 Nov 2026: with हिं picked,
   English questions were answered in Hindi, which visitors read as a fault).
4. A short English message with nothing to tell the language by ("yes", "2", "B.Tech fees?"):
   the language of the previous reply, so a Kannada conversation stays in Kannada.
5. The language picked in the widget (EN / हिं / ಕ): the reply language for such short messages
   when nothing came before.
6. Otherwise English.

No model call: script ranges, word lists and regular expressions only (< 1 ms).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .guard import detect_language as _romanised

SUPPORTED = ("en", "hi", "kn")
NAMES = {"en": "English", "hi": "Hindi", "kn": "Kannada"}
OUTPUT_MODES = ("text", "voice", "both")

_KN_SCRIPT = re.compile(r"[ಀ-೿]")
_HI_SCRIPT = re.compile(r"[ऀ-ॿ]")
_LATIN_WORD = re.compile(r"[A-Za-z]{2,}")


@dataclass(frozen=True)
class Detected:
    language: str  # en | hi | kn
    script: str  # latin | kannada | devanagari | mixed | none
    code_mixed: (
        bool  # Hindi or Kannada together with English words, or in English letters
    )
    strong: bool  # enough to tell the language by (False: "yes", "2", "B.Tech fees?")
    share: float = (
        1.0  # of the words, the part written in the detected language's own script
    )


def _script_share(text: str, kn: bool) -> float:
    words = [
        w for w in text.split() if re.search(r"[A-Za-z\u0900-\u097f\u0c80-\u0cff]", w)
    ]
    own = _KN_SCRIPT if kn else _HI_SCRIPT
    return sum(1 for w in words if own.search(w)) / len(words) if words else 0.0


def detect(text: str) -> Detected:
    """The language a message is written (or was heard) in."""
    text = text or ""
    kn = len(_KN_SCRIPT.findall(text))
    hi = len(_HI_SCRIPT.findall(text))
    latin = _LATIN_WORD.findall(text)
    if kn or hi:
        lang = "kn" if kn >= hi else "hi"
        script = "mixed" if kn and hi else ("kannada" if lang == "kn" else "devanagari")
        return Detected(
            lang, script, bool(latin), True, _script_share(text, lang == "kn")
        )
    romanised = _romanised(
        text
    )  # Hinglish / Kannada in English letters (guard.py word lists)
    if romanised != "en":
        return Detected(romanised, "latin", True, True)
    return Detected("en", "latin" if latin else "none", False, len(latin) >= 3)


# ------------------------------------------------------------------ asked-for preferences

_LANG = {
    "kn": r"(?<![A-Za-z])(?:kannada|kanada|kannad)(?![A-Za-z])|ಕನ್ನಡ(?:ದಲ್ಲಿ|ದಲ್ಲೇ)?|कन्नड़|कन्नड",
    "hi": r"(?<![A-Za-z])hindi(?![A-Za-z])|हिंदी|हिन्दी|ಹಿಂದಿ(?:ಯಲ್ಲಿ|ಯಲ್ಲೇ)?",
    "en": r"(?<![A-Za-z])(?:english|angrezi|angreji)(?![A-Za-z])|इंग्लिश|अंग्रेज़ी|अंग्रेजी"
    r"|ಇಂಗ್ಲಿಷ್‌?(?:ನಲ್ಲಿ|ನಲ್ಲೇ)?|ಇಂಗ್ಲೀಷ್‌?(?:ನಲ್ಲಿ|ನಲ್ಲೇ)?",
}
_ANY_LANG = "|".join(f"(?P<{k}>{v})" for k, v in _LANG.items())
# "Reply in Kannada", "can you explain the syllabus in Hindi", "switch to English".
_VERB = (
    r"(?<![A-Za-z])(?:reply|respond|answer|speak|talk|say|tell|explain|write|continue|switch|"
    r"change|translate|give|send)(?:s|ing|ed)?(?![A-Za-z])"
)
_ASKED_EN = re.compile(
    rf"(?P<verb>{_VERB})(?P<gap>[^.?!\n]{{0,40}}?)(?<![A-Za-z])(?P<span>(?:in|to|into)\s+(?:{_ANY_LANG})"
    r"(?:\s+(?:please|pls|only|language))*)",
    re.IGNORECASE,
)
# "Hindi mein batao", "हिंदी में बताइए", "Kannada alli heli", "ಕನ್ನಡದಲ್ಲಿ ಹೇಳಿ".
_ASKED_INDIC = re.compile(
    rf"(?P<span>(?:{_ANY_LANG})\s*(?:alli|nalli|dalli|me|mein|main|men|में)?\s*"
    r"(?:bolo|boliye|bolie|batao|bataiye|bataye|bataen|samjhao|samjhaiye|jawab\s+do|baat\s+karo|"
    r"heli|helu|heliri|maatadi|matadi|maathadi|uttarisi|"
    r"बोलो|बोलिए|बोलें|बताओ|बताइए|बताएँ|बताएं|समझाओ|समझाइए|जवाब\s+दो|जवाब\s+दें|बात\s+करो|बात\s+करें|"
    r"ಹೇಳಿ|ಹೇಳು|ಮಾತನಾಡಿ|ಮಾತಾಡಿ|ಉತ್ತರಿಸಿ|ತಿಳಿಸಿ))",
    re.IGNORECASE,
)
_ASKED_ALONE = re.compile(rf"(?P<span>{_ANY_LANG})", re.IGNORECASE)

# Output: text only, voice, or both (the widget plays the audio; WhatsApp sends a voice note).
_OUTPUT = (
    (
        "text",
        re.compile(
            r"(?<![A-Za-z])(?:text\s+only|only\s+text|just\s+text|no\s+(?:voice|audio)|"
            r"stop\s+(?:the\s+)?(?:voice|audio)|without\s+(?:voice|audio)|"
            r"(?:reply|replies|answer|answers)\s+in\s+text)(?![A-Za-z])",
            re.IGNORECASE,
        ),
    ),
    (
        "both",
        re.compile(
            r"(?<![A-Za-z])(?:(?:both\s+)?(?:text|written)\s+and\s+(?:voice|audio)|"
            r"(?:voice|audio)\s+and\s+text)(?![A-Za-z])",
            re.IGNORECASE,
        ),
    ),
    (
        "voice",
        re.compile(
            r"(?<![A-Za-z])(?:(?:reply|respond|answer|send|give)(?:\s+me)?(?:\s+(?:it|the\s+answers?|"
            r"answers|replies))?\s+(?:with|in|as|by)(?:\s+a)?\s+(?:voice|audio)(?:\s+(?:note|message))?s?|"
            r"(?:voice|audio)\s+(?:reply|replies|note|notes|message|messages|only|please)|"
            r"send\s+(?:a\s+)?(?:voice|audio)(?:\s+(?:note|message))?s?|"
            r"speak\s+(?:the|your)\s+(?:answers?|replies|reply)|"
            r"awaaz\s+(?:me|mein)\s+(?:bhejo|bolo)|voice\s+(?:me|mein|alli)\s+(?:bhejo|kalisi|heli)|"
            r"आवाज़\s+में\s+(?:भेजो|भेजें|बोलो|बताओ))(?![A-Za-z])",
            re.IGNORECASE,
        ),
    ),
)

# Words that, left alone after the request is taken out, are not a question ("can you please
# reply" / "ok, from now on"): such a message only sets the preference.
_FILLER = set(
    (
        "please pls plz kindly can could would will you u me i we want need prefer like the a an "
        "answers answer reply replies respond speak talk say tell explain write continue switch "
        "change translate give send from now on onwards ok okay and just only language rest of "
        "conversation all further it that this again also us lets let's do go ahead sir madam mam "
        "hi hello thanks thank in to into mein me main mujhe se aage ab nanage dayavittu swalpa "
        "inmunde ini ee yes sure voice audio text note notes message messages both with as by "
        "ಈಗ ಇನ್ನು ಮುಂದೆ ದಯವಿಟ್ಟು ನನಗೆ ಸರಿ कृपया अब से आगे मुझे मैं ठीक है"
    ).split()
)


@dataclass(frozen=True)
class Preferences:
    language: str | None  # asked for: en | hi | kn
    output: str | None  # asked for: text | voice | both
    question: str  # the message without the request ("" when nothing else was said)
    only: bool  # the message was only the request (no question to answer)


def _lang_of(m: re.Match) -> str:
    return next(k for k in _LANG if m.group(k))


def _content_words(text: str) -> list[str]:
    words = re.findall(r"[^\s.,!?;:()\"'“”‘’\-]+", text.lower())
    return [w for w in words if w not in _FILLER and len(w) > 1]


def preferences(text: str) -> Preferences:
    """What the visitor asked about the reply itself: its language, and text or voice."""
    rest, lang, output = text or "", None, None
    m = _ASKED_EN.search(rest) or _ASKED_INDIC.search(rest)
    if m:
        lang = _lang_of(m)
        start = m.start("span")
        # "Answer (me) in Hindi": the verb goes too; "explain the syllabus in Hindi" keeps it.
        if "verb" in m.re.groupindex and not _content_words(m.group("gap")):
            start = m.start("verb")
        rest = rest[:start] + " " + rest[m.end("span") :]
    else:
        alone = _ASKED_ALONE.search(rest)
        # Just the language's name ("Kannada please", "ಕನ್ನಡ", "in Hindi"): asked for, since there
        # is nothing else; "Is Kannada compulsory?" is a question about Kannada and is left alone.
        if alone:
            without = rest[: alone.start()] + " " + rest[alone.end() :]
            if not _content_words(without):
                lang, rest = _lang_of(alone), without
    for mode, pattern in _OUTPUT:
        o = pattern.search(rest)
        if o:
            output = mode
            rest = rest[: o.start()] + " " + rest[o.end() :]
            break
    if lang is None and output is None:
        return Preferences(None, None, text, False)
    question = re.sub(r"\s+([?.!,])", r"\1", re.sub(r"\s{2,}", " ", rest)).strip(
        " ,;:-–"
    )
    only = not _content_words(question)
    return Preferences(lang, output, "" if only else question, only)


# ------------------------------------------------------------------ the reply's language


VOICE_SWITCH_SHARE = (
    0.6  # of a spoken sentence's words, in one script, to switch language
)


def decide(
    detected: Detected,
    chosen: str | None,
    state: dict,
    asked: str | None = None,
    english_reply: str | None = None,
    spoken: bool = False,
) -> tuple[str, str]:
    """(response language, why). `english_reply`: the language a channel was told to answer
    English in (voice: "English speech, Hindi replies"); Hindi and Kannada still answer in their
    own language. Updates the preferences kept in the conversation `state`:
    `reply_language` {"lang", "by": "request" | "picker"} and `picked_language` (the widget's
    last value, so only a new pick replaces a preference asked for in the conversation)."""
    pref = state.get("reply_language") or None
    # The widget sends its current pick with every message: a change of it is a new choice; the
    # first value seen is a choice only while nothing was asked for in the conversation.
    previous = state.get("picked_language")
    if chosen in SUPPORTED:
        state["picked_language"] = chosen
    if asked in SUPPORTED:
        pref = {"lang": asked, "by": "request"}
        state["reply_language"] = pref
        return asked, "requested"
    if (
        chosen in SUPPORTED
        and chosen != previous
        and (previous is not None or not pref)
    ):
        # The visitor picked a language in the widget just now: the newest choice wins.
        pref = {"lang": chosen, "by": "picker"}
        state["reply_language"] = pref
    # Speech-to-text (19 Nov 2026) writes English or mixed speech partly in Kannada or Hindi
    # letters ("ಕನ್ನಡದಲ್ಲಿ. Okay, thank you. Kannada word", "कंप्यूटर साइंस ಮತ್ತು ಮೆಕಾನಿಕಲ್"): on a
    # call, only a sentence mostly in one Indian script changes the language; a mixed one keeps
    # the language the call is in.
    if (
        spoken
        and detected.language in ("hi", "kn")
        and (detected.script == "mixed" or detected.share < VOICE_SWITCH_SHARE)
    ):
        # The language the visitor chose (widget tab or "reply in Hindi") comes first: seen
        # 19 Nov 2026, Hindi tab, Hindi speech written in Kannada letters, answered in English
        # because the previous reply was English.
        keep = (pref or {}).get("lang") or chosen or state.get("last_language")
        if keep in SUPPORTED:
            return keep, "voice_keeps_language"
    # A question written in Hindi or Kannada script is answered in that language, even after an
    # earlier "reply in Kannada" (19 Nov 2026: a Hindi question got a Kannada answer because of a
    # request made earlier in the conversation). Hinglish / Kanglish in English letters is less
    # certain, so a request still decides those.
    if detected.language in ("hi", "kn") and detected.script in (
        "devanagari",
        "kannada",
    ):
        return detected.language, "same_as_input"
    if pref and pref.get("by") == "request":
        return pref["lang"], "requested"
    if detected.language in ("hi", "kn"):
        return detected.language, "same_as_input"
    if detected.language == "en" and english_reply in SUPPORTED:
        return english_reply, "preference"
    if detected.language == "en" and detected.strong:
        return "en", "same_as_input"
    last = state.get("last_language")
    if not detected.strong and last in SUPPORTED and last != "en":
        return last, "continuing"
    if pref and pref.get("lang") in SUPPORTED:
        return pref["lang"], "preference"
    return "en", "default"


def output_mode(state: dict, default: str = "text") -> str:
    mode = state.get("output_mode")
    return mode if mode in OUTPUT_MODES else default


def preview(text: str, chosen: str | None, state: dict) -> str:
    """The reply language a turn is likely to get, without changing `state`: for the fixed
    messages a voice channel says before the engine has run ("one moment", "not heard")."""
    prefs = preferences(text)
    lang, _ = decide(
        detect(prefs.question or text), chosen, dict(state), prefs.language, spoken=True
    )
    return lang
