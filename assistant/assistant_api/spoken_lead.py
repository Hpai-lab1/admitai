"""Spoken lead capture (PRD F2.9): on voice, a phone number is read back digit by digit and saved
only after the person says yes.

    visitor: "Can someone call me?"            -> ask for the mobile number
    visitor: "nine eight seven six five, four three two one zero"
    assistant: "I heard 9, 8, 7, 6, 5 - 4, 3, 2, 1, 0. Shall the admissions team call you on
                this number? Please say yes or no."
    visitor: "yes"                             -> contact consent and the lead, in Module C

The number is personal data: it is never written to the transcript, waits in the conversation
state only until confirmed (and for at most 10 minutes), and is removed once saved. An under-18
visitor is never asked for their own number (PRD F5.3).
"""

from __future__ import annotations

import re
import time

TTL_S = 600
MAX_TRIES = 2
CONSENT_TEXT_VERSION = "voice-2026-10-v1"

CALL_ME = re.compile(
    r"\b(call me|call back|callback|ring me|phone me|contact me|talk to (a |an )?(person|human|"
    r"someone|counsellor|counselor)|speak to (a |an )?(person|human|someone))\b"
    r"|मुझे कॉल|कॉल बैक|किसी से बात",
    re.I,
)
YES = re.compile(
    r"^\W*(yes|yeah|yep|correct|right|that'?s right|ok|okay|sure|haan|ha|हाँ|हां|जी|सही)\b",
    re.I,
)
NO = re.compile(r"^\W*(no|nope|wrong|not right|nahi|nahin|नहीं|गलत)\b", re.I)
STOP = re.compile(r"\b(cancel|stop|never ?mind|forget it|rehne do|रहने दो)\b", re.I)

WORDS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "to": "2", "too": "2",
    "three": "3", "four": "4", "for": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "niner": "9",
    "shunya": "0", "ek": "1", "do": "2", "teen": "3", "char": "4", "chaar": "4",
    "paanch": "5", "panch": "5", "chhe": "6", "chhah": "6", "saat": "7", "aath": "8", "nau": "9",
    "शून्य": "0", "एक": "1", "दो": "2", "तीन": "3", "चार": "4", "पांच": "5", "पाँच": "5",
    "छह": "6", "छः": "6", "सात": "7", "आठ": "8", "नौ": "9",
    "ಸೊನ್ನೆ": "0", "ಒಂದು": "1", "ಎರಡು": "2", "ಮೂರು": "3", "ನಾಲ್ಕು": "4", "ಐದು": "5",
    "ಆರು": "6", "ಏಳು": "7", "ಎಂಟು": "8", "ಒಂಬತ್ತು": "9",
}  # fmt: skip
REPEAT = {"double": 2, "triple": 3}
_DEVANAGARI = str.maketrans("०१२३४५६७८९೦೧೨೩೪೫೬೭೮೯", "01234567890123456789")

TEXT = {
    "en": {
        "ask": "Of course. Please say your 10-digit mobile number, one digit at a time.",
        "again": "Sorry, I didn't get a full 10-digit mobile number. Please say it again, slowly.",
        "confirm": "I heard {digits}. Shall the admissions team call you on this number? "
        "Please say yes or no.",
        "unclear": "Please say yes if {digits} is right, or no to say it again.",
        "saved": "Thank you. The admissions team will call you on that number.",
        "form": "Let's do it in writing instead: please type your number in the contact form "
        "in the chat.",
        "stopped": "No problem. You can keep asking me questions.",
        "minor": "As you are under 18, a parent or guardian needs to agree before we call. "
        "Please ask them to give their own number in the chat.",
        "error": "Sorry, I couldn't save that right now. Please use the contact form in the chat.",
    },
    "hi": {
        "ask": "ज़रूर। कृपया अपना 10 अंकों का मोबाइल नंबर एक-एक अंक करके बोलें।",
        "again": "माफ़ कीजिए, पूरा 10 अंकों का नंबर नहीं मिला। कृपया धीरे-धीरे फिर से बोलें।",
        "confirm": "मैंने सुना {digits}। क्या प्रवेश टीम आपको इसी नंबर पर कॉल करे? कृपया हाँ या नहीं बोलें।",
        "unclear": "अगर {digits} सही है तो हाँ बोलें, नहीं तो फिर से बोलने के लिए नहीं बोलें।",
        "saved": "धन्यवाद। प्रवेश टीम आपको इस नंबर पर कॉल करेगी।",
        "form": "कृपया चैट के संपर्क फ़ॉर्म में अपना नंबर लिख दें।",
        "stopped": "कोई बात नहीं। आप प्रश्न पूछते रहें।",
        "minor": "आपकी उम्र 18 से कम है, इसलिए कॉल से पहले माता-पिता या अभिभावक की सहमति ज़रूरी है। "
        "कृपया उनसे चैट में अपना नंबर देने को कहें।",
        "error": "माफ़ कीजिए, अभी यह सेव नहीं हो सका। कृपया चैट का संपर्क फ़ॉर्म इस्तेमाल करें।",
    },
}


def t(key: str, lang: str, **kw) -> str:
    return TEXT.get(lang, TEXT["en"])[key].format(**kw)


def digits_from_speech(text: str) -> str | None:
    """A 10-digit Indian mobile number from what speech-to-text heard, or None. Handles digits,
    number words in English and Hindi, and "double five"; drops a leading +91, 91 or 0. Only an
    unbroken run of number words counts, so "for" or "to" in a sentence is not read as a digit."""
    text = text.lower().translate(_DEVANAGARI)
    runs, run, repeat = [], [], 1
    for tok in re.findall(r"\d+|[a-z]+|[ऀ-ॿ]+", text):
        if tok in REPEAT:
            repeat = REPEAT[tok]
            continue
        digit = tok if tok.isdigit() else WORDS.get(tok)
        if digit is None:
            if run:
                runs.append("".join(run))
            run, repeat = [], 1
            continue
        run.append(digit * repeat if len(digit) == 1 else digit)
        repeat = 1
    if run:
        runs.append("".join(run))
    for number in sorted(runs, key=len, reverse=True):
        for prefix in ("91", "0"):
            if len(number) > 10 and number.startswith(prefix):
                number = number[len(prefix) :]
        if re.fullmatch(r"[6-9]\d{9}", number):
            return number
    return None


def read_back(number: str) -> str:
    """Digit by digit, grouped 5 + 5 as Indian mobile numbers are usually said."""
    return ", ".join(number[:5]) + " - " + ", ".join(number[5:])


def step(
    state: dict, text: str, lang: str, now: float | None = None
) -> tuple[str | None, str | None]:
    """Advance the capture. Returns (reply, action); reply None with no action means "not a lead
    turn, answer the question". Action "save" means: record consent and the lead for
    state['voice_lead']['number']. `state` is changed in place."""
    now = time.time() if now is None else now
    lead = state.get("voice_lead")
    if lead and now - lead.get("at", 0) > TTL_S:
        state.pop("voice_lead", None)  # an abandoned number is not kept
        lead = None
    if not lead:
        if not CALL_ME.search(text):
            return None, None
        if state.get("is_minor"):
            return t("minor", lang), None
        state["voice_lead"] = {"step": "number", "tries": 0, "at": now}
        return t("ask", lang), None
    if STOP.search(text):
        state.pop("voice_lead", None)
        return t("stopped", lang), None
    if lead["step"] == "number":
        number = digits_from_speech(text)
        if number:
            state["voice_lead"] = {"step": "confirm", "number": number, "at": now}
            return t("confirm", lang, digits=read_back(number)), None
        if NO.match(text):
            state.pop("voice_lead", None)
            return t("stopped", lang), None
        lead["tries"] = lead.get("tries", 0) + 1
        if lead["tries"] >= MAX_TRIES:
            state.pop("voice_lead", None)
            return t("form", lang), None
        return t("again", lang), None
    # confirming a number
    if YES.match(text):
        return None, "save"
    if NO.match(text):
        state["voice_lead"] = {"step": "number", "tries": 0, "at": now}
        return t("again", lang), None
    corrected = digits_from_speech(text)  # said a corrected number straight away
    if corrected:
        state["voice_lead"] = {"step": "confirm", "number": corrected, "at": now}
        return t("confirm", lang, digits=read_back(corrected)), None
    return t("unclear", lang, digits=read_back(lead["number"])), None


# ------------------------------------------------------------------ spoken end-of-chat rating
# "to", "for", "do" and "oh" are numbers only when said on their own ("for" in "for sure" is not 4).
_AMBIGUOUS = {"to", "too", "for", "do", "o", "oh"}
RATING_TEXT = {
    "en": {
        "thanks": "Thank you for rating this chat {n} out of 5.",
        "unclear": "Sorry, I didn't catch that. Please say a number from 1 to 5.",
    },
    "kn": {
        "thanks": "ಈ ಚಾಟ್‌ಗೆ 5 ರಲ್ಲಿ {n} ರೇಟಿಂಗ್ ನೀಡಿದ್ದಕ್ಕೆ ಧನ್ಯವಾದಗಳು.",
        "unclear": "ಕ್ಷಮಿಸಿ, ಅರ್ಥವಾಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು 1 ರಿಂದ 5 ರವರೆಗಿನ ಒಂದು ಸಂಖ್ಯೆಯನ್ನು ಹೇಳಿ.",
    },
    "hi": {
        "thanks": "इस चैट को 5 में से {n} रेटिंग देने के लिए धन्यवाद।",
        "unclear": "माफ़ कीजिए, समझ नहीं आया। कृपया 1 से 5 तक कोई संख्या बोलिए।",
    },
}


def spoken_rating(text: str) -> int | None:
    """1-5 from a spoken rating ("four", "4 stars", "char", "चार", "४"), or None when unclear or
    when two different numbers were said. "out of five" is not the rating."""
    t = (text or "").lower().translate(_DEVANAGARI)
    t = re.sub(r"\b(out of|outta)\s+(five|5|paanch|panch|पांच|पाँच)\b", " ", t)
    t = re.sub(r"5\s*ರಲ್ಲಿ|ಐದರಲ್ಲಿ", " ", t)  # Kannada "out of 5"
    t = re.sub(r"(?<=\d)(?=[a-z])", " ", t)  # "4stars"
    tokens = re.findall(r"[0-9]+|[a-z]+|[ऀ-ॿ]+|[ಀ-೿]+", t)  # Latin, Devanagari, Kannada
    values = set()
    for w in tokens:
        if w.isdigit():
            values.add(int(w))
        elif w in WORDS and (w not in _AMBIGUOUS or len(tokens) == 1):
            values.add(int(WORDS[w]))
    if len(values) != 1:
        return None
    n = values.pop()
    return n if 1 <= n <= 5 else None
