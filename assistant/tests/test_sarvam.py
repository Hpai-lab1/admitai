"""Sarvam AI speech (sarvam.py) and the choice of speech engine per language (speech.py). No
network: Sarvam's API is faked with httpx.MockTransport."""

import base64
import io
import json
import math
import struct
import wave

import httpx
import pytest

from assistant_api import voice
from assistant_api.sarvam import SarvamClient, SpeechError, clamp, filename_for
from assistant_api.speech import MultilingualSpeech, spoken_text, wav_to_ogg


def client(handler, **kw) -> SarvamClient:
    return SarvamClient(
        "sk-test",
        "https://sarvam.test",
        speakers={"kn": "chaitra_kn_conversation", "hi": "simran_hi_assistant"},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
        **kw,
    )


def tone(seconds=0.2, rate=22050) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(
            b"".join(
                struct.pack("<h", int(6000 * math.sin(2 * math.pi * 440 * i / rate)))
                for i in range(int(rate * seconds))
            )
        )
    return buf.getvalue()


# ------------------------------------------------------------------ speech-to-text


def test_transcription_detects_the_language_and_sends_the_key_only_in_the_header():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"], seen["key"] = req.url.path, req.headers["api-subscription-key"]
        seen["body"] = req.content
        return httpx.Response(
            200,
            json={
                "request_id": "r1",
                "transcript": "ಸಿಎಸ್‌ಇ ಶುಲ್ಕ ಎಷ್ಟು?",
                "language_code": "kn-IN",
                "language_probability": 0.97,
            },
        )

    heard = client(handler).transcribe(b"OggS-audio", "audio/ogg; codecs=opus")
    assert heard.text == "ಸಿಎಸ್‌ಇ ಶುಲ್ಕ ಎಷ್ಟು?" and heard.language == "kn"
    assert heard.probability == 0.97 and heard.request_id == "r1"
    assert seen["path"] == "/speech-to-text" and seen["key"] == "sk-test"
    body = seen["body"].decode("latin-1")
    assert 'name="model"' in body and "saaras:v4" in body
    assert "unknown" in body  # language detected, not forced
    assert 'filename="audio.ogg"' in body  # WhatsApp voice notes are OGG/Opus
    assert "sk-test" not in body
    assert "COMEDK" in body  # programme and exam names as key terms (saaras:v4)


def test_a_fixed_language_and_codemix_mode_are_sent_when_asked():
    seen = {}

    def handler(req):
        seen["body"] = req.content.decode("utf-8", "replace")
        return httpx.Response(200, json={"transcript": "B.Tech fees ಎಷ್ಟು"})

    heard = client(handler, stt_mode="codemix").transcribe(b"x", "audio/webm", "kn")
    assert "kn-IN" in seen["body"] and "codemix" in seen["body"]
    assert heard.language == "kn"  # the fixed language, when Sarvam reports none


def test_busy_and_server_errors_are_retried_then_succeed():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(
                429, json={"error": {"code": "rate_limit_exceeded_error"}}
            )
        if len(calls) == 2:
            return httpx.Response(
                503, json={"error": {"code": "internal_server_error"}}
            )
        return httpx.Response(
            200, json={"transcript": "hello", "language_code": "en-IN"}
        )

    assert client(handler).transcribe(b"x").text == "hello"
    assert len(calls) == 3


@pytest.mark.parametrize("status", [400, 401, 402, 403, 422])
def test_errors_that_a_retry_cannot_fix_are_not_retried(status):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(
            status, json={"error": {"code": "invalid_request_error", "message": "bad"}}
        )

    with pytest.raises(SpeechError) as err:
        client(handler).transcribe(b"x")
    assert err.value.status == status and len(calls) == 1
    assert "sk-test" not in str(err.value)


def test_a_timeout_is_retried_then_reported():
    calls = []

    def handler(req):
        calls.append(1)
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(SpeechError, match="unreachable"):
        client(handler, retries=1).transcribe(b"x")
    assert len(calls) == 2


def test_content_types_are_sent_without_parameters():
    """Sarvam refused "audio/ogg; codecs=opus" (live, 16 Nov 2026): WhatsApp voice notes and the
    browser's "audio/webm;codecs=opus" recordings must go as the bare type."""
    from assistant_api.sarvam import clean_mime

    seen = {}

    def handler(req):
        seen["body"] = req.content.decode("latin-1")
        return httpx.Response(200, json={"transcript": "ok", "language_code": "en-IN"})

    client(handler).transcribe(b"OggS", "audio/ogg; codecs=opus")
    assert "Content-Type: audio/ogg\r\n" in seen["body"]
    assert "codecs" not in seen["body"]
    assert clean_mime("audio/webm;codecs=opus") == "audio/webm"
    assert clean_mime("Audio/MPEG") == "audio/mpeg"
    assert clean_mime(None) == "audio/wav"
    assert clean_mime("audio/x-unknown") == "application/octet-stream"


def test_file_names_tell_the_format():
    assert filename_for("audio/ogg; codecs=opus") == "audio.ogg"
    assert filename_for("audio/webm;codecs=opus") == "audio.webm"
    assert filename_for("audio/mpeg") == "audio.mp3"
    assert filename_for(None) == "audio.wav"


# ------------------------------------------------------------------ text-to-speech


def test_speech_is_made_in_the_replys_language_with_its_own_voice():
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return httpx.Response(
            200,
            json={
                "request_id": "r2",
                "audios": [base64.b64encode(b"RIFFaudio").decode()],
            },
        )

    audio = client(handler).synthesize("ಶುಲ್ಕ ₹2,50,000.", "kn", codec="mp3")
    assert audio == b"RIFFaudio"
    assert seen["language_code"] == "kn-IN" and seen["model"] == "bulbul:v4-flash"
    assert seen["speaker"] == "chaitra_kn_conversation"
    assert seen["output_audio_codec"] == "mp3"


def test_long_text_is_cut_at_a_sentence_for_the_2500_character_limit():
    text = "ಇದು ಒಂದು ವಾಕ್ಯ. " * 300
    cut = clamp(text)
    assert len(cut) <= 2500 and cut.endswith(".")


def test_no_audio_or_an_unknown_language_is_an_error():
    with pytest.raises(SpeechError):
        client(lambda r: httpx.Response(200, json={"audios": []})).synthesize("x", "hi")
    with pytest.raises(SpeechError):
        client(lambda r: httpx.Response(200, json={})).synthesize("x", "ta")


def test_what_is_spoken_has_no_citation_markers_or_english_only_notes():
    assert (
        spoken_text("ಶುಲ್ಕ ₹2,50,000 [1]. ಹಾಸ್ಟೆಲ್ ಇದೆ [1, 2].")
        == "ಶುಲ್ಕ ₹2,50,000. ಹಾಸ್ಟೆಲ್ ಇದೆ."
    )
    assert spoken_text("Fee 90000 [2]. (यह उत्तर अंग्रेज़ी में है)") == "Fee 90000."


# ------------------------------------------------------------------ which engine, per language


class FakeLocal(voice.Speech):
    def __init__(self, heard="local words", fails=False):
        super().__init__("/nowhere", "base", "none")
        self.heard, self.fails, self.calls = heard, fails, []

    def transcribe(self, audio, vocabulary="", language=None):
        self.calls.append(("stt", language))
        return voice.Transcript(self.heard, language or "en", -0.2)

    def speak(self, text):
        self.calls.append(("tts", text))
        if self.fails:
            raise RuntimeError("no piper")
        return tone()


class FakeSarvam:
    def __init__(self, heard=("ಹಾಸ್ಟೆಲ್ ಇದೆಯೆ?", "kn"), fail_stt=False, fail_tts=False):
        self.heard, self.fail_stt, self.fail_tts, self.calls = (
            heard,
            fail_stt,
            fail_tts,
            [],
        )

    def transcribe(self, audio, mime=None, language=None):
        from assistant_api.sarvam import Heard

        self.calls.append(("stt", mime, language))
        if self.fail_stt:
            raise SpeechError("Sarvam refused (422): too long", 422)
        return Heard(self.heard[0], self.heard[1], 0.9, "r", 5)

    def synthesize(self, text, language, codec="wav", sample_rate=22050):
        self.calls.append(("tts", language, codec, text))
        if self.fail_tts:
            raise SpeechError("Sarvam is unreachable: ConnectTimeout")
        return b"SARVAM-" + codec.encode()


def test_with_sarvam_every_recording_is_heard_by_it_with_the_language_detected():
    local, sarvam = FakeLocal(), FakeSarvam()
    speech = MultilingualSpeech(local, sarvam)
    heard = speech.transcribe(b"x", "", "en", "audio/webm")
    assert (heard.text, heard.language, heard.provider) == (
        "ಹಾಸ್ಟೆಲ್ ಇದೆಯೆ?",
        "kn",
        "sarvam",
    )
    # The visitor picked English replies; that is not what they speak, so it is not forced.
    assert sarvam.calls == [("stt", "audio/webm", None)] and local.calls == []


def test_when_sarvam_cannot_hear_it_the_local_model_does():
    local = FakeLocal("What is the fee?")
    speech = MultilingualSpeech(local, FakeSarvam(fail_stt=True))
    heard = speech.transcribe(b"x", "", "en")
    assert heard.text == "What is the fee?" and heard.provider == "local"
    assert local.calls == [("stt", "en")]


def test_without_sarvam_everything_stays_local_and_indian_languages_are_text_only():
    speech = MultilingualSpeech(FakeLocal(), None)
    assert speech.hears_with() == "local"
    assert speech.voice_for("en") == "local"
    assert speech.voice_for("kn") is None and speech.voice_for("hi") is None
    with pytest.raises(LookupError):
        speech.speak("ಶುಲ್ಕ", "kn")


def test_auto_speaks_english_locally_and_hindi_and_kannada_with_sarvam():
    local, sarvam = FakeLocal(), FakeSarvam()
    speech = MultilingualSpeech(local, sarvam, tts="auto")
    assert speech.speak("The fee is 250000.", "en").startswith(b"RIFF")
    assert speech.speak("शुल्क ₹2,50,000 है [1].", "hi") == b"SARVAM-wav"
    assert sarvam.calls[-1] == ("tts", "hi", "wav", "शुल्क ₹2,50,000 है.")
    # WhatsApp: MP3 from Sarvam, OGG/Opus from the local voice (WhatsApp does not play WAV).
    assert speech.speak_message("ಶುಲ್ಕ", "kn") == (b"SARVAM-mp3", "audio/mpeg")
    audio, mime = speech.speak_message("The fee is 250000.", "en")
    assert mime == "audio/ogg" and audio.startswith(b"OggS")


def test_an_english_voice_falls_back_to_piper_but_kannada_is_never_read_in_english():
    local = FakeLocal()
    speech = MultilingualSpeech(local, FakeSarvam(fail_tts=True), tts="sarvam")
    assert speech.speak("The fee is 250000.", "en").startswith(b"RIFF")
    with pytest.raises(SpeechError):
        speech.speak("ಶುಲ್ಕ", "kn")
    assert [c for c in local.calls if c[0] == "tts"] == [("tts", "The fee is 250000.")]


def test_piper_audio_becomes_a_whatsapp_voice_note():
    ogg = wav_to_ogg(tone(0.5))
    assert ogg.startswith(b"OggS") and len(ogg) > 100
