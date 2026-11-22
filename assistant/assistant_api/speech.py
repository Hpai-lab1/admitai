"""Which engine hears and which speaks, per language (PRD F2, F1.6): one interface for
push-to-talk (routes/voice.py) and WhatsApp voice notes (whatsapp.py).

- Speech-to-text: Sarvam Saaras (sarvam.py) when SARVAM_API_KEY is set and
  SPEECH_TO_TEXT_PROVIDER is auto or sarvam. It detects English, Hindi, Kannada and code-mixed
  speech itself. If Sarvam fails (down, quota, a recording over 30 s), the local Whisper model
  hears it instead, told the visitor's chosen language as before.
- Text-to-speech (TEXT_TO_SPEECH_PROVIDER): auto speaks English with the local Piper voice (no
  audio leaves the platform for English) and Hindi and Kannada with Sarvam Bulbul; sarvam uses
  Bulbul for every language; local is Piper only (Hindi and Kannada replies are then text, as
  before). An English reply falls back to Piper if Sarvam fails; a Hindi or Kannada one is shown
  as text, never read out by the English voice.
- Retell calls keep Retell's own speech stack (retell.py); this module is not on that path.

Audio is never stored here (F2.6 consent recordings are audio_store.py's job).
"""

from __future__ import annotations

import asyncio
import io
import logging
import re

from . import voice
from .checks import _CITE
from .sarvam import SarvamClient, SpeechError

log = logging.getLogger(__name__)

SUPPORTED = ("en", "hi", "kn")
_NOTE = re.compile(
    r"\([^)]*(?:अंग्रेज़ी|ಇಂಗ್ಲಿಷ್)[^)]*\)"
)  # "this answer is in English" notes
_MARKERS = re.compile(
    r"\s*\[\d+(?:\s*,\s*\d+)*\]"
)  # "[1]" and "[1, 2]" (as retell.speakable)


def spoken_text(text: str) -> str:
    """What Sarvam should say: no citation markers or "answer in English" notes. Numbers stay as
    digits ("₹2,50,000"): Bulbul reads them in the reply's own language."""
    t = _NOTE.sub("", _MARKERS.sub("", _CITE.sub("", text)))
    return re.sub(r"\s{2,}", " ", t).strip()


def wav_to_ogg(wav: bytes) -> bytes:
    """WAV -> OGG/Opus, the voice-note format WhatsApp plays (it does not play WAV)."""
    import av

    src = av.open(io.BytesIO(wav), format="wav")
    buf = io.BytesIO()
    dst = av.open(buf, mode="w", format="ogg")
    try:
        stream = dst.add_stream("libopus", rate=48000)
        stream.layout = "mono"
        resampler = av.AudioResampler(
            format=stream.format.name if stream.format else "s16",
            layout="mono",
            rate=48000,
            frame_size=960,  # Opus encodes 20 ms frames
        )
        for frame in src.decode(audio=0):
            frame.pts = None
            for f in resampler.resample(frame):
                for packet in stream.encode(f):
                    dst.mux(packet)
        for f in resampler.resample(None):
            for packet in stream.encode(f):
                dst.mux(packet)
        for packet in stream.encode(None):
            dst.mux(packet)
    finally:
        dst.close()
        src.close()
    return buf.getvalue()


class MultilingualSpeech:
    """The local models (voice.Speech) and, when configured, Sarvam, behind the interface the
    voice routes already use (transcribe, speak, slots, preload, speak_pcm)."""

    def __init__(
        self,
        local: voice.Speech | None,
        sarvam: SarvamClient | None = None,
        stt: str = "auto",
        tts: str = "auto",
    ):
        self.local = local
        self.sarvam = sarvam
        self.stt = stt
        self.tts = tts
        self.slots = local.slots if local else asyncio.Semaphore(voice.MAX_SESSIONS)

    @classmethod
    def from_settings(
        cls, settings, local: voice.Speech | None
    ) -> "MultilingualSpeech":
        return cls(
            local,
            SarvamClient.from_settings(settings),
            settings.speech_to_text_provider,
            settings.text_to_speech_provider,
        )

    # ------------------------------------------------------------------ hearing

    def hears_with(self) -> str:
        if self.sarvam and self.stt in ("auto", "sarvam"):
            return "sarvam"
        return "local"

    def transcribe(
        self,
        audio: bytes,
        vocabulary: str = "",
        language: str | None = None,
        mime: str | None = None,
    ) -> voice.Transcript:
        """`language` is the visitor's chosen language: the local model listens for it; Sarvam
        detects the spoken language itself (a visitor may pick Kannada replies and speak English)."""
        if self.hears_with() == "sarvam":
            try:
                heard = self.sarvam.transcribe(audio, mime, None)
                lang = "hi" if heard.language == "ur" else heard.language
                return voice.Transcript(
                    heard.text, lang, 0.0 if heard.text else -10.0, "sarvam"
                )
            except SpeechError as exc:
                log.warning(
                    "speech-to-text: Sarvam failed (%s); using the local model", exc
                )
                if self.local is None:
                    raise RuntimeError(str(exc)) from None
        if self.local is None:
            raise RuntimeError("no speech-to-text configured")
        return self.local.transcribe(audio, vocabulary, language)

    # ------------------------------------------------------------------ speaking

    def voice_for(self, language: str) -> str | None:
        """ "sarvam", "local" or None (no voice: the reply is shown as text only)."""
        if language not in SUPPORTED:
            return None
        sarvam = self.sarvam is not None and self.tts in ("auto", "sarvam")
        if self.tts == "sarvam" and sarvam:
            return "sarvam"
        if language == "en":
            return "local" if self.local else ("sarvam" if sarvam else None)
        return "sarvam" if sarvam else None

    def speaks(self, language: str) -> bool:
        return self.voice_for(language) is not None

    def speak(self, text: str, language: str = "en") -> bytes:
        """The reply as WAV in its own language's voice. LookupError: no voice for it."""
        audio, _ = self._speak(text, language, "wav")
        return audio

    def speak_message(self, text: str, language: str) -> tuple[bytes, str]:
        """(audio, MIME type) for a WhatsApp voice reply: MP3 from Sarvam, OGG/Opus from Piper."""
        return self._speak(text, language, "message")

    def _speak(self, text: str, language: str, kind: str) -> tuple[bytes, str]:
        provider = self.voice_for(language)
        if provider is None:
            raise LookupError(f"no voice for {language}")
        if provider == "sarvam":
            try:
                if kind == "message":
                    return (
                        self.sarvam.synthesize(spoken_text(text), language, "mp3"),
                        "audio/mpeg",
                    )
                return self.sarvam.synthesize(
                    spoken_text(text), language, "wav"
                ), "audio/wav"
            except SpeechError as exc:
                if language != "en" or self.local is None:
                    raise
                log.warning(
                    "text-to-speech: Sarvam failed (%s); using the local voice", exc
                )
        wav = self.local.speak(text)
        if kind == "message":
            return wav_to_ogg(wav), "audio/ogg"
        return wav, "audio/wav"

    # ------------------------------------------------------------------ the local models

    def preload(self) -> None:
        if self.local:
            self.local.preload()

    def speak_pcm(self, text: str) -> tuple[bytes, int]:
        return self.local.speak_pcm(text)


# Helpers that work with either kind of speech object (the routes' tests use plain fakes).


def can_speak(speech, language: str) -> bool:
    if isinstance(speech, MultilingualSpeech):
        return speech.speaks(language)
    return language in voice.SPOKEN_LANGUAGES


def speak_wav(speech, text: str, language: str) -> bytes:
    if isinstance(speech, MultilingualSpeech):
        return speech.speak(text, language)
    if language not in voice.SPOKEN_LANGUAGES:
        raise LookupError(f"no voice for {language}")
    return speech.speak(text)


def speak_message(speech, text: str, language: str) -> tuple[bytes, str]:
    """(audio, MIME type) for a WhatsApp voice reply, from either kind of speech object."""
    if isinstance(speech, MultilingualSpeech):
        return speech.speak_message(text, language)
    if language not in voice.SPOKEN_LANGUAGES:
        raise LookupError(f"no voice for {language}")
    return wav_to_ogg(speech.speak(text)), "audio/ogg"


def transcribe(
    speech, audio: bytes, vocabulary: str, language: str | None, mime: str | None
) -> voice.Transcript:
    if isinstance(speech, MultilingualSpeech):
        return speech.transcribe(audio, vocabulary, language, mime)
    return speech.transcribe(audio, vocabulary, language)


def provider_names(speech, language: str) -> dict:
    """For the timings: who heard and who spoke this turn."""
    if isinstance(speech, MultilingualSpeech):
        return {"stt": speech.hears_with(), "tts": speech.voice_for(language)}
    return {
        "stt": "local",
        "tts": "local" if language in voice.SPOKEN_LANGUAGES else None,
    }


def for_college(speech, allowed: set[str]):
    """The speech service as this college may use it: without Sarvam unless the college opted in
    (`model_policy.voice_external` lists "sarvam"); local Whisper and Piper are always allowed."""
    if "sarvam" in allowed or getattr(speech, "sarvam", None) is None:
        return speech
    import copy

    local_only = copy.copy(speech)
    local_only.sarvam = None
    return local_only
