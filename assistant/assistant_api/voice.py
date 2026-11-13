"""Voice (PRD F2): local speech-to-text (faster-whisper) and text-to-speech (Piper) around the same
engine as chat, with a voice answer style.

Level 1 on the lab servers streams over WebRTC (LiveKit); this prototype takes one recorded
utterance per request (push-to-talk), which keeps every other part identical: same agents,
knowledge, guardrails and sentence checks (F2.10 channel consistency). Audio is never stored; the
transcript is (F2.6: audio only with consent).
"""

from __future__ import annotations

import asyncio
import io
import re
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

from .checks import _CITE

MIN_CONFIDENCE = (
    -1.0
)  # Whisper average log-probability below this: "Sorry, I didn't catch that"
MAX_SESSIONS = 5  # PRD 10.2: voice is capped at 5 concurrent sessions in Level 1

BASE_VOCABULARY = (
    "Admissions enquiry. B.Tech, M.Tech, MBA, CSE, lakh, crore, rupees, hostel, scholarship, "
    "counselling, KCET, COMEDK, JEE, PUC, diploma, lateral entry."
)

MESSAGES = {
    "en": {
        "not_heard": "Sorry, I didn't catch that. Could you say it again?",
        "offer_chat": "Sorry, I still couldn't hear that clearly. You can type your question in the chat, or "
        "ask us to call you back.",
        "busy": "Voice is busy right now. Please type your question in the chat, or ask for a call back.",
    },
    "hi": {
        "not_heard": "माफ़ कीजिए, मैं सुन नहीं पाया। क्या आप फिर से बोलेंगे?",
        "offer_chat": "माफ़ कीजिए, अभी भी साफ़ सुनाई नहीं दिया। आप चैट में लिख सकते हैं या कॉल बैक माँग सकते हैं।",
        "busy": "अभी वॉइस व्यस्त है। कृपया चैट में लिखें या कॉल बैक माँगें।",
    },
    # Kannada written without a native speaker: have it reviewed before real use.
    "kn": {
        "not_heard": "ಕ್ಷಮಿಸಿ, ನನಗೆ ಸರಿಯಾಗಿ ಕೇಳಿಸಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಮತ್ತೆ ಹೇಳುವಿರಾ?",
        "offer_chat": "ಕ್ಷಮಿಸಿ, ಇನ್ನೂ ಸ್ಪಷ್ಟವಾಗಿ ಕೇಳಿಸಲಿಲ್ಲ. ನೀವು ಚಾಟ್‌ನಲ್ಲಿ ಟೈಪ್ ಮಾಡಬಹುದು ಅಥವಾ ಮರಳಿ ಕರೆ ಮಾಡಲು ಕೇಳಬಹುದು.",
        "busy": "ಈಗ ಧ್ವನಿ ಸೇವೆ ಕಾರ್ಯನಿರತವಾಗಿದೆ. ದಯವಿಟ್ಟು ಚಾಟ್‌ನಲ್ಲಿ ಟೈಪ್ ಮಾಡಿ ಅಥವಾ ಮರಳಿ ಕರೆ ಮಾಡಲು ಕೇಳಿ.",
    },
}

# Languages the speech model can say out loud (one voice per language; PIPER_VOICE is English, and
# reading Hindi or Kannada text with it sounds wrong).
# For the others the answer is shown as text, rather than read out wrongly in an English voice.
SPOKEN_LANGUAGES = ("en",)


def msg(key: str, lang: str) -> str:
    return MESSAGES.get(lang, MESSAGES["en"])[key]


# ------------------------------------------------------------------ speaking numbers (F2.3)

_ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = [
    "",
    "",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
]


def _below_thousand(n: int) -> str:
    words = []
    if n >= 100:
        words.append(f"{_ONES[n // 100]} hundred")
        n %= 100
    if n >= 20:
        words.append(_TENS[n // 10] + (f" {_ONES[n % 10]}" if n % 10 else ""))
    elif n or not words:
        words.append(_ONES[n])
    return " ".join(words)


def indian_words(n: int) -> str:
    """250000 -> 'two lakh fifty thousand'; 12000000 -> 'one crore twenty lakh'."""
    if n == 0:
        return "zero"
    parts = []
    for size, name in ((10_000_000, "crore"), (100_000, "lakh"), (1000, "thousand")):
        if n >= size:
            parts.append(
                f"{indian_words(n // size) if n // size >= 1000 else _below_thousand(n // size)} {name}"
            )
            n %= size
    if n:
        parts.append(_below_thousand(n))
    return " ".join(parts)


def for_speech(text: str) -> str:
    """Caption text -> what the voice says: no citation markers, amounts and big numbers in words,
    years read as years, times as times."""
    t = _CITE.sub("", text)
    t = re.sub(
        r"\(.*?अंग्रेज़ी.*?\)", "", t
    )  # the "answer is in English" note is shown, not spoken
    t = re.sub(r"(?:₹|Rs\.?|INR)\s*([\d,]+)", lambda m: f"{m.group(1)} rupees", t)
    t = re.sub(r"([\d,]+)\s*INR", lambda m: f"{m.group(1)} rupees", t)

    def num(m: re.Match) -> str:
        raw = m.group(0).replace(",", "")
        n = int(raw)
        if 1900 <= n <= 2100 and "," not in m.group(0):
            return raw  # a year
        return indian_words(n) if n >= 1000 else raw

    t = re.sub(
        r"(?<![\d:.])\d{1,3}(?:,\d{2,3})+(?![\d:])|(?<![\d:.])\d{4,9}(?![\d:.])", num, t
    )
    return re.sub(r"\s{2,}", " ", t).strip()


# ------------------------------------------------------------------ engines (loaded once, lazily)


@dataclass
class Transcript:
    text: str
    language: str
    confidence: float  # average log-probability of the segments


class Speech:
    """faster-whisper + Piper, loaded on first use. Missing models raise RuntimeError, which the
    API turns into "use chat instead" rather than a crash."""

    def __init__(
        self,
        model_dir: str,
        whisper_size: str,
        piper_voice: str,
        max_sessions: int = MAX_SESSIONS,
        whisper_size_indic: str | None = None,
    ):
        self.model_dir = Path(model_dir)
        self.whisper_size = whisper_size
        # Hindi and Kannada need a larger model than English (benchmarks/stt-2026-11-13.json:
        # on the CPU, `base` wrote Hindi in Urdu or Latin script; `small` was right but ~2.5x
        # slower, too slow for every English question). None: one model for every language.
        self.whisper_size_indic = whisper_size_indic or whisper_size
        self.piper_voice = piper_voice
        self.slots = asyncio.Semaphore(max_sessions)
        self._lock = threading.Lock()
        self._stt: dict[str, object] = {}
        self._tts = None

    def _whisper(self, size: str | None = None):
        size = size or self.whisper_size
        with self._lock:
            if size not in self._stt:
                from faster_whisper import WhisperModel

                self._stt[size] = WhisperModel(
                    size,
                    device="cpu",
                    compute_type="int8",
                    download_root=str(self.model_dir / "whisper"),
                )
            return self._stt[size]

    def preload(self) -> None:
        """Load every model a turn may need (both Whisper sizes and the voice)."""
        for size in dict.fromkeys((self.whisper_size, self.whisper_size_indic)):
            self._whisper(size)
        self._piper()

    def _piper(self):
        with self._lock:
            if self._tts is None:
                from piper import PiperVoice

                path = self.model_dir / "piper" / f"{self.piper_voice}.onnx"
                if not path.exists():
                    raise RuntimeError(f"Piper voice not found: {path}")
                self._tts = PiperVoice.load(str(path))
            return self._tts

    def transcribe(
        self, audio: bytes, vocabulary: str = "", language: str | None = None
    ) -> Transcript:
        """`language` ("kn", "hi"): the visitor chose it, so the model listens for it instead of
        guessing, which small models do badly for Kannada. The English vocabulary prompt would only
        pull such speech towards English, so it is left out then."""
        size = (
            self.whisper_size_indic
            if language not in (None, "en")
            else self.whisper_size
        )
        text, detected, confidence = self._run(size, audio, vocabulary, language)
        if language is None and detected != "en" and self.whisper_size_indic != size:
            # Spoken in another language with no language chosen: the English model guessed it
            # (well) but writes it badly, so the larger model hears it again in that language.
            text, detected, confidence = self._run(
                self.whisper_size_indic, audio, vocabulary, detected
            )
        return Transcript(text, detected, confidence)

    def _run(
        self, size: str, audio: bytes, vocabulary: str, language: str | None
    ) -> tuple[str, str, float]:
        segments, info = self._whisper(size).transcribe(
            io.BytesIO(audio),
            beam_size=1,
            vad_filter=True,
            language=language,
            # A question is a sentence or two. Speech in another language than the one chosen
            # made the model loop (14.8 s, then a mishearing anyway): cap it, and do not feed
            # its own earlier text back in, which is what drives such loops.
            max_new_tokens=80,
            condition_on_previous_text=False,
            initial_prompt=(
                f"{BASE_VOCABULARY} {vocabulary}"[:800]
                if language in (None, "en")
                else None
            ),
        )
        segs = list(segments)
        text = " ".join(s.text.strip() for s in segs).strip()
        confidence = sum(s.avg_logprob for s in segs) / len(segs) if segs else -10.0
        return text, info.language or "en", confidence

    def speak(self, text: str) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            self._piper().synthesize_wav(for_speech(text), w)
        return buf.getvalue()
