"""Voice (PRD F2): number speaking, the voice turn with a fake speech engine, limits and errors."""

import asyncio
import uuid

import pytest

from assistant_api import voice
from conftest import FakeKnowledge, FakeLLM, needs_db, visitor_token


@pytest.mark.parametrize(
    "n,words",
    [
        (250000, "two lakh fifty thousand"),
        (90000, "ninety thousand"),
        (1200, "one thousand two hundred"),
        (12000000, "one crore twenty lakh"),
        (412500, "four lakh twelve thousand five hundred"),
    ],
)
def test_indian_number_words(n, words):
    assert voice.indian_words(n) == words


def test_speech_text_drops_markers_and_reads_amounts():
    said = voice.for_speech(
        "The tuition fee is 2,50,000 INR per year [1]. Apply by 30 June 2027 [2]."
    )
    assert (
        said
        == "The tuition fee is two lakh fifty thousand rupees per year. Apply by 30 June 2027."
    )
    assert voice.for_speech("Gates close at 21:30.") == "Gates close at 21:30."
    assert "अंग्रेज़ी" not in voice.for_speech("Yes [1]. (यह उत्तर अंग्रेज़ी में है।)")


class FakeSpeech(voice.Speech):
    def __init__(self, heard, confidence=-0.2, slots=5, tts_fails=False):
        super().__init__("/nowhere", "base", "none", max_sessions=slots)
        self.heard, self.confidence, self.tts_fails, self.spoken = (
            heard,
            confidence,
            tts_fails,
            [],
        )
        self.vocab = None

    def transcribe(self, audio, vocabulary="", language=None):
        self.vocab = vocabulary
        return voice.Transcript(self.heard, "en", self.confidence)

    def speak(self, text):
        if self.tts_fails:
            raise RuntimeError("tts down")
        self.spoken.append(text)
        return b"RIFFfakewav"


def vclient(settings, llm, speech):
    from fastapi.testclient import TestClient

    from assistant_api.engine import Deps
    from assistant_api.guard import RateLimiter
    from assistant_api.main import create_app

    return TestClient(
        create_app(
            settings,
            Deps(settings, FakeKnowledge(), llm, RateLimiter(100, 60)),
            speech=speech,
        )
    )


def token(client, tenant, session, conv="v1"):
    r = client.post(
        "/voice/token",
        json={"conversation_id": conv},
        headers={"Authorization": f"Bearer {visitor_token(tenant, session)}"},
    )
    assert r.status_code == 200
    return r.json()["token"]


def turn(client, tok, audio=b"webm-audio"):
    return client.post(
        "/voice/turn",
        data={"token": tok},
        files={"audio": ("q.webm", audio, "audio/webm")},
    )


@needs_db
def test_voice_turn_transcribes_answers_in_voice_style_and_speaks(settings, tenant):
    llm = FakeLLM("The tuition fee is 250000 INR per year [1].")
    speech = FakeSpeech("What is the CSE fee?")
    client = vclient(settings, llm, speech)
    session = str(uuid.uuid4())
    r = turn(client, token(client, tenant, session)).json()
    assert r["transcript"] == "What is the CSE fee?" and r["outcome"] == "answered"
    assert (
        r["answer"] == "The tuition fee is 250000 INR per year [1]."
    )  # captions keep the digits
    assert r["audio_wav_base64"] and set(r["timings"]) == {
        "stt_ms",
        "answer_ms",
        "tts_ms",
        "total_ms",
    }
    assert "spoken aloud" in llm.calls[0]["messages"][0]["content"]  # F2.3 voice style
    assert (
        "B.Tech Computer Science and Engineering" in speech.vocab
    )  # tenant vocabulary for STT
    # the transcript is kept like a chat message; the audio is not
    view = client.get(
        "/chat/v1",
        headers={"X-Service-Key": settings.service_key, "X-Tenant-Id": tenant},
    ).json()
    assert (
        view["channel"] == "web_voice"
        and view["messages"][0]["content"] == "What is the CSE fee?"
    )


@needs_db
def test_unclear_audio_asks_again_then_offers_chat(settings, tenant):
    llm = FakeLLM("should not be used")
    client = vclient(settings, llm, FakeSpeech("mumble", confidence=-2.5))
    tok = token(client, tenant, str(uuid.uuid4()))
    first, second = turn(client, tok).json(), turn(client, tok).json()
    assert (
        first["answer"].startswith("Sorry, I didn't catch that")
        and not first["offer_handoff"]
    )
    assert (
        "type your question in the chat" in second["answer"] and second["offer_handoff"]
    )
    assert llm.calls == []


@needs_db
def test_voice_limits_and_errors(settings, tenant):
    speech = FakeSpeech("Is there a hostel?", slots=1, tts_fails=True)
    client = vclient(
        settings, FakeLLM("Hostels are separate for men and women [1]."), speech
    )
    tok = token(client, tenant, str(uuid.uuid4()))
    r = turn(client, tok).json()
    assert (
        r["answer"].startswith("Hostels are separate") and r["audio_wav_base64"] is None
    )  # TTS failed: text
    assert turn(client, "not-a-token").status_code == 401
    assert turn(client, tok, audio=b"").status_code == 422
    staff_like = visitor_token(
        tenant, typ="visitor"
    )  # a chat token is not a voice token
    assert turn(client, staff_like).status_code == 401

    async def busy():
        async with speech.slots:
            return turn(client, tok)

    assert asyncio.run(busy()).status_code == 503


class Scripted(FakeSpeech):
    """Hears the given phrases one turn after another."""

    def __init__(self, *phrases):
        super().__init__(phrases[0])
        self.phrases = list(phrases)

    def transcribe(self, audio, vocab="", language=None):
        self.heard = self.phrases.pop(0)
        return super().transcribe(audio, vocab)


class FakeExperience:
    def __init__(self):
        self.calls = []

    def consent(self, tenant, session, purpose, version, channel):
        self.calls.append(("consent", purpose, channel))

    def lead(self, tenant, session, body):
        self.calls.append(("lead", body["phone"]))
        return "lead-1"


def lead_client(settings, *phrases):
    from fastapi.testclient import TestClient

    from assistant_api.engine import Deps
    from assistant_api.guard import RateLimiter
    from assistant_api.main import create_app

    exp = FakeExperience()
    app = create_app(
        settings,
        Deps(settings, FakeKnowledge(), FakeLLM("unused"), RateLimiter(100, 60)),
        exp,
        speech=Scripted(*phrases),
    )
    return TestClient(app), exp


@needs_db
def test_spoken_number_is_read_back_and_saved_only_after_yes(settings, tenant):
    client, exp = lead_client(
        settings,
        "Can someone call me back?",
        "nine eight seven six five, four three two one one",  # misheard last digit
        "no",
        "nine eight seven six five four three two one zero",
        "yes",
    )
    tok = token(client, tenant, str(uuid.uuid4()))

    def say():
        return turn(client, tok).json()

    assert "mobile number" in say()["answer"]
    assert "9, 8, 7, 6, 5 - 4, 3, 2, 1, 1" in say()["answer"]
    assert "say it again" in say()["answer"] and exp.calls == []  # "no": nothing saved
    assert "9, 8, 7, 6, 5 - 4, 3, 2, 1, 0" in say()["answer"] and exp.calls == []
    assert say()["outcome"] == "lead_saved"
    assert exp.calls == [("consent", "contact", "web_voice"), ("lead", "+919876543210")]
    # The number never reaches the transcript, and is not kept after saving.
    view = client.get(
        "/chat/v1",
        headers={"X-Service-Key": settings.service_key, "X-Tenant-Id": tenant},
    ).json()
    assert "98765" not in str(view["messages"]) and not view["state"].get("voice_lead")


@needs_db
def test_minors_are_not_asked_for_their_number_and_a_stalled_capture_gives_way(
    settings, tenant
):
    from assistant_api import store

    client, exp = lead_client(settings, "Please call me", "um", "er")
    tok = token(client, tenant, str(uuid.uuid4()))
    store.open_conversation(settings.database_url, tenant, "v1", None, "web_voice")
    store.update_state(settings.database_url, tenant, "v1", {"is_minor": True})
    assert "under 18" in turn(client, tok).json()["answer"]

    client, exp = lead_client(settings, "call me back", "um", "er")
    tok = token(client, tenant, str(uuid.uuid4()), conv="v2")
    answers = [turn(client, tok).json()["answer"] for _ in range(3)]
    assert (
        "contact form" in answers[2] and exp.calls == []
    )  # after two tries: type it instead


def test_spoken_rating_reads_one_clear_number_from_1_to_5():
    from assistant_api.spoken_lead import spoken_rating

    for said, n in [
        ("Four", 4),
        ("I'd give it 4 stars", 4),
        ("5", 5),
        ("five out of five", 5),
        ("char", 4),
        ("चार", 4),
        ("४", 4),
        ("two", 2),
        ("to", 2),  # alone, "to" is the number
        ("4stars", 4),
    ]:
        assert spoken_rating(said) == n, said
    for said in ["", "for sure", "maybe three or four", "seven", "zero", "it was good"]:
        assert spoken_rating(said) is None, said
