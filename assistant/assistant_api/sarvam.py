"""Sarvam AI speech (https://docs.sarvam.ai): Saaras speech-to-text and Bulbul text-to-speech for
English, Hindi and Kannada, including code-mixed speech. Used for push-to-talk and WhatsApp voice
notes (speech.py); Retell calls keep Retell's own speech stack (retell.py).

- Auth: the `api-subscription-key` header, from SARVAM_API_KEY. The key is never logged and never
  reaches the browser.
- Speech-to-text: POST /speech-to-text (multipart), real-time REST, at most 30 s of audio per
  request (longer audio gets 422 and falls back to the local model in speech.py). The language is
  detected (`language_code=unknown`) unless the caller fixes it.
- Text-to-speech: POST /text-to-speech (JSON), at most 2,500 characters for bulbul:v4-flash;
  the audio comes back base64-encoded (`audios`).
- Retries: 429 and 5xx and network errors are retried with exponential backoff (SARVAM_RETRIES,
  default 2); 400/401/402/403/422 are not, since sending the same request again cannot help.
- What is logged: path, status, milliseconds and Sarvam's request id. Never the audio, the text
  or the transcript (personal data).
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

log = logging.getLogger(__name__)

LANGUAGE_CODES = {"en": "en-IN", "hi": "hi-IN", "kn": "kn-IN"}
MAX_TTS_CHARS = 2500
RETRYABLE = {429, 500, 502, 503, 504}
# Programme and exam names that speech-to-text should not turn into ordinary words (keyterms is
# a saaras:v4 option: up to 50 terms of up to 64 characters).
KEYTERMS = [
    "B.Tech",
    "M.Tech",
    "MBA",
    "BBA",
    "B.Des",
    "CSE",
    "KCET",
    "COMEDK",
    "JEE",
    "PUC",
    "NEET",
    "hostel",
    "scholarship",
    "counselling",
    "lateral entry",
]
_EXTENSIONS = {
    "webm": "webm",
    "ogg": "ogg",
    "opus": "ogg",
    "wav": "wav",
    "wave": "wav",
    "x-wav": "wav",
    "mpeg": "mp3",
    "mp3": "mp3",
    "mp4": "m4a",
    "m4a": "m4a",
    "x-m4a": "m4a",
    "aac": "aac",
    "amr": "amr",
    "flac": "flac",
}


class SpeechError(Exception):
    """A provider could not hear or speak this request. `status` is the HTTP status (None for a
    network error or timeout)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class Heard:
    text: str
    language: str  # en | hi | kn, or the code Sarvam reported (e.g. "ta")
    probability: float | None
    request_id: str | None
    ms: int


def _short(code: str | None) -> str:
    return (code or "").split("-")[0].lower() or "en"


# The content types Sarvam's /speech-to-text accepts, from its own 400 reply (16 Nov 2026). A type
# with parameters is refused: WhatsApp's "audio/ogg; codecs=opus" and the browser's
# "audio/webm;codecs=opus" must be sent as "audio/ogg" and "audio/webm".
ACCEPTED_MIME = {
    "audio/mpeg", "audio/mp3", "audio/mpeg3", "audio/x-mpeg-3", "audio/x-mp3", "audio/wav",
    "audio/x-wav", "audio/wave", "audio/pcm_s16le", "audio/l16", "audio/raw",
    "application/octet-stream", "audio/aac", "audio/x-aac", "audio/aiff", "audio/x-aiff",
    "audio/ogg", "audio/opus", "audio/flac", "audio/x-flac", "audio/mp4", "audio/x-m4a",
    "audio/amr", "audio/x-ms-wma", "audio/webm", "video/webm",
}  # fmt: skip


def clean_mime(mime: str | None) -> str:
    """The bare media type Sarvam accepts ("audio/ogg; codecs=opus" -> "audio/ogg")."""
    base = (mime or "audio/wav").split(";")[0].strip().lower()
    return base if base in ACCEPTED_MIME else "application/octet-stream"


def filename_for(mime: str | None) -> str:
    """A file name whose extension tells Sarvam the format ("audio/ogg; codecs=opus" -> .ogg)."""
    sub = (mime or "").split(";")[0].strip().split("/")[-1].lower()
    return f"audio.{_EXTENSIONS.get(sub, 'wav')}"


def clamp(text: str, limit: int = MAX_TTS_CHARS) -> str:
    """At most `limit` characters, cut at the end of a sentence when there is one."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(p) for p in (". ", "? ", "! ", "। ", "\n"))
    return cut[: end + 1] if end > limit // 2 else cut


@dataclass
class SarvamClient:
    api_key: str
    base_url: str = "https://api.sarvam.ai"
    stt_model: str = "saaras:v4"
    stt_mode: str = (
        "codemix"  # English words stay in English letters (config.sarvam_stt_mode)
    )
    tts_model: str = "bulbul:v4-flash"
    speakers: dict[str, str] = field(default_factory=dict)
    timeout_s: float = 15.0
    retries: int = 2
    client: httpx.Client | None = None
    sleep: Callable[[float], None] = time.sleep

    @classmethod
    def from_settings(cls, s) -> "SarvamClient | None":
        if not s.sarvam_api_key:
            return None
        speakers = {}
        for item in s.sarvam_speakers.split(","):
            if "=" in item:
                lang, name = item.split("=", 1)
                speakers[lang.strip()] = name.strip()
        return cls(
            s.sarvam_api_key,
            s.sarvam_base_url.rstrip("/"),
            s.sarvam_stt_model,
            s.sarvam_stt_mode,
            s.sarvam_tts_model,
            speakers,
            s.sarvam_timeout_s,
            max(0, s.sarvam_retries),
        )

    # ------------------------------------------------------------------ calls

    def transcribe(
        self, audio: bytes, mime: str | None = None, language: str | None = None
    ) -> Heard:
        """Speech -> text. `language` (en/hi/kn) fixes the language; None detects it, which is
        what lets one recording be Kannada, Hindi, English or a mix of them."""
        data = {
            "model": self.stt_model,
            "language_code": LANGUAGE_CODES.get(language or "", "unknown"),
        }
        if self.stt_mode and self.stt_mode != "transcribe":
            data["mode"] = self.stt_mode
        if self.stt_model.endswith("v4"):
            data["keyterms"] = json.dumps(KEYTERMS)
        files = {"file": (filename_for(mime), audio, clean_mime(mime))}
        t0 = time.monotonic()
        body = self._post("/speech-to-text", data=data, files=files)
        text = (body.get("transcript") or "").strip()
        return Heard(
            text,
            _short(body.get("language_code"))
            if body.get("language_code")
            else (language or "en"),
            body.get("language_probability"),
            body.get("request_id"),
            int((time.monotonic() - t0) * 1000),
        )

    def synthesize(
        self,
        text: str,
        language: str,
        codec: str = "wav",
        sample_rate: int = 22050,
    ) -> bytes:
        """Text -> audio (WAV by default; mp3 for WhatsApp, which does not play WAV)."""
        if language not in LANGUAGE_CODES:
            raise SpeechError(f"no Sarvam voice for language {language!r}")
        payload = {
            "text": clamp(text),
            "language_code": LANGUAGE_CODES[language],
            "model": self.tts_model,
            "output_audio_codec": codec,
            "speech_sample_rate": sample_rate,
        }
        speaker = self.speakers.get(language)
        if speaker:
            payload["speaker"] = speaker
        body = self._post("/text-to-speech", json=payload)
        audios = body.get("audios") or []
        if not audios:
            raise SpeechError("Sarvam returned no audio")
        return base64.b64decode("".join(audios))

    # ------------------------------------------------------------------ transport

    def _post(self, path: str, **kw) -> dict:
        own = self.client is None
        c = self.client or httpx.Client(
            timeout=httpx.Timeout(self.timeout_s, connect=min(5.0, self.timeout_s))
        )
        headers = {"api-subscription-key": self.api_key}
        try:
            for attempt in range(self.retries + 1):
                t0 = time.monotonic()
                try:
                    r = c.post(f"{self.base_url}{path}", headers=headers, **kw)
                except httpx.HTTPError as exc:  # timeout, connection refused, DNS
                    log.warning(
                        "sarvam %s failed (%s), attempt %d",
                        path,
                        type(exc).__name__,
                        attempt + 1,
                    )
                    if attempt < self.retries:
                        self.sleep(0.4 * 2**attempt)
                        continue
                    raise SpeechError(
                        f"Sarvam is unreachable: {type(exc).__name__}"
                    ) from None
                ms = int((time.monotonic() - t0) * 1000)
                rid = _request_id(r)
                log.info(
                    "sarvam %s %d %d ms request_id=%s", path, r.status_code, ms, rid
                )
                if r.status_code < 300:
                    return r.json()
                if r.status_code in RETRYABLE and attempt < self.retries:
                    self.sleep(_backoff(r, attempt))
                    continue
                raise SpeechError(
                    f"Sarvam refused ({r.status_code}): {_error_text(r)}", r.status_code
                )
        finally:
            if own:
                c.close()
        raise SpeechError("Sarvam: no attempt made")  # pragma: no cover (retries >= 0)


def _backoff(r: httpx.Response, attempt: int) -> float:
    """Retry-After when Sarvam sends a short one, else 0.4 s, 0.8 s, 1.6 s ..."""
    try:
        after = float(r.headers.get("Retry-After", ""))
        if 0 <= after <= 3:
            return after
    except ValueError:
        pass
    return 0.4 * 2**attempt


def _request_id(r: httpx.Response) -> str | None:
    try:
        body = r.json()
    except ValueError:
        return None
    if isinstance(body, dict):
        return body.get("request_id") or (body.get("error") or {}).get("request_id")
    return None


def _error_text(r: httpx.Response) -> str:
    """Sarvam's error code and message ({"error": {"message", "code"}}), short, no request data."""
    try:
        err = (r.json() or {}).get("error") or {}
        return re.sub(
            r"\s+", " ", f"{err.get('code', '')} {err.get('message', '')}"
        ).strip()[:200]
    except ValueError:
        return r.text[:200]


# Text translation (19 Nov 2026): the fallback when Gemini cannot translate an answer (quota,
# outage). Only the college's own checked English answer is sent, never the visitor's words.
TRANSLATE_CODES = {"hi": "hi-IN", "kn": "kn-IN"}


def translate(api_key: str, text: str, language: str, timeout: float = 15.0) -> str:
    """English -> Hindi or Kannada with sarvam-translate:v1 (kept numbers and markers in testing;
    mayura:v1 moved the citation markers). Raises SpeechError on any failure."""
    if language not in TRANSLATE_CODES:
        raise SpeechError(f"no translation into {language}")
    try:
        r = httpx.post(
            "https://api.sarvam.ai/translate",
            headers={"api-subscription-key": api_key},
            json={
                "input": text[:2000],
                "source_language_code": "en-IN",
                "target_language_code": TRANSLATE_CODES[language],
                "model": "sarvam-translate:v1",
            },
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise SpeechError(
            f"Sarvam translate unreachable: {type(exc).__name__}"
        ) from None
    if r.status_code != 200:
        raise SpeechError(f"Sarvam translate refused ({r.status_code})")
    return (r.json().get("translated_text") or "").strip()
