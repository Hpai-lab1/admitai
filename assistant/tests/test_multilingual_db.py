"""English, Hindi and Kannada end to end (language.py, engine._translated, speech.py, Retell,
WhatsApp): input language apart from response language, natural Hindi and Kannada answers for a
college that opted in to Gemini, switching in the middle of a conversation without losing what
was said, and voice in and out. Fake Module A, model gateway, Sarvam and Retell."""

import json
import time
import uuid
from dataclasses import replace

import psycopg
from fastapi.testclient import TestClient

from assistant_api import retell, store
from assistant_api.engine import Deps
from assistant_api.guard import RateLimiter
from assistant_api.main import create_app
from assistant_api.prompts import QUESTION_PROMPT, TRANSLATE_PROMPT, message
from assistant_api.speech import MultilingualSpeech
from assistant_api.whatsapp import OutboxSender
from conftest import TEST_ADMIN_URL, TEST_URL, FakeKnowledge, FakeLLM, needs_db
from test_engine_db import ask, done_of, text_of
from test_sarvam import FakeLocal, FakeSarvam
from test_voice import token
from test_whatsapp_db import PNID, SECRET, FakeExperience, payload, post, text

pytestmark = needs_db

TUITION = "The B.Tech CSE tuition fee is ₹2,50,000 per year [1]."
HOSTEL_FEE = "The B.Tech CSE hostel fee is ₹90,000 per year [1]."
HOSTELS = "Separate hostels are available for men and women [1]."
KN_TUITION = "ಬಿ.ಟೆಕ್ ಸಿಎಸ್‌ಇ ಬೋಧನಾ ಶುಲ್ಕ ವರ್ಷಕ್ಕೆ ₹2,50,000."
KN_HOSTEL_FEE = "ಬಿ.ಟೆಕ್ ಸಿಎಸ್‌ಇ ಹಾಸ್ಟೆಲ್ ಶುಲ್ಕ ವರ್ಷಕ್ಕೆ ₹90,000."
HI_TUITION = "बी.टेक सीएसई की ट्यूशन फीस ₹2,50,000 प्रति वर्ष है।"


class TranslatingLLM(FakeLLM):
    """FakeLLM whose translation calls (TRANSLATE_PROMPT) return scripted translations."""

    def __init__(self, *replies, translations=(), questions=()):
        super().__init__(*replies)
        self.translations = list(translations)
        self.questions = list(
            questions
        )  # English for Hindi/Kannada questions, in order
        self.translated: list[dict] = []
        self.understood: list[dict] = []

    async def complete(self, model, messages, max_tokens, timeout):
        system = messages[0]["content"] if messages else ""
        if system == QUESTION_PROMPT:
            self.understood.append({"model": model, "text": messages[-1]["content"]})
            out = self.questions.pop(0) if self.questions else messages[-1]["content"]
            if isinstance(out, Exception):
                raise out
            return out
        for lang, prompt in TRANSLATE_PROMPT.items():
            if system == prompt:
                self.translated.append(
                    {"model": model, "language": lang, "text": messages[-1]["content"]}
                )
                out = self.translations.pop(0) if self.translations else ""
                if isinstance(out, Exception):
                    raise out
                return out
        return await super().complete(model, messages, max_tokens, timeout)


def opted_in(
    settings, tenant, llm, speech=None, wa_sender=None, experience=None, **s_kw
):
    """A college that opted in to Gemini (model_policy.external: gemini) and to the outside voice
    services (model_policy.voice_external)."""
    s = replace(settings, gemini_api_key="test-key", **s_kw)
    app = create_app(
        s,
        Deps(s, FakeKnowledge(), llm, RateLimiter(100, 60)),
        experience,
        wa_sender,
        speech,
    )
    client = TestClient(app)
    r = client.post(
        "/tenant-pack",
        json={
            "college": {"name": "Demo College"},
            "model_policy": {
                "external": "gemini",
                "voice_external": ["retell", "sarvam", "vapi"],
            },
        },
        headers={"X-Service-Key": "key-from-c", "X-Tenant-Id": tenant},
    )
    assert r.status_code == 200, r.text
    return client


def answers(llm):
    return [c for c in llm.calls if "messages" in c]


def languages_in_db(tenant, conv):
    with psycopg.connect(TEST_URL) as conn:
        conn.execute("select set_config('app.tenant_id', %s, false)", (tenant,))
        return [
            tuple(r)
            for r in conn.execute(
                "select sender, language from chat.messages where conversation_id = %s"
                " order by created_at",
                (conv,),
            )
        ]


# ------------------------------------------------------------------ chat: the four directions


def test_english_question_with_kannada_picked_is_answered_in_english(settings, tenant):
    # decided 17 Nov 2026: the reply follows the typed language, not the widget's pick
    llm = TranslatingLLM(TUITION)
    client = opted_in(settings, tenant, llm)
    _, events = ask(
        client,
        tenant,
        "What is the tuition fee for B.Tech CSE?",
        conv="m1",
        language="kn",
    )
    assert text_of(events) == TUITION and llm.translated == []
    done = done_of(events)
    assert (done["language"], done["language_reason"]) == ("en", "same_as_input")


def test_english_question_asking_for_kannada_is_answered_in_kannada(settings, tenant):
    llm = TranslatingLLM(TUITION, translations=[KN_TUITION])
    client = opted_in(settings, tenant, llm)
    _, events = ask(
        client,
        tenant,
        "What is the tuition fee for B.Tech CSE? Answer in Kannada",
        conv="m1k",
        language="en",
    )
    assert text_of(events) == KN_TUITION + " [1]"  # the number kept, the citation kept
    done = done_of(events)
    assert (done["language"], done["input_language"], done["language_reason"]) == (
        "kn",
        "en",
        "requested",
    )
    assert done["outcome"] == "answered" and done["sources"]
    # The English answer was checked, then translated by Gemini in one call, without markers.
    [t] = llm.translated
    assert t["model"].startswith("gemini/") and t["language"] == "kn"
    assert t["text"] == "The B.Tech CSE tuition fee is ₹2,50,000 per year."
    assert languages_in_db(tenant, "m1k") == [("user", "en"), ("assistant", "kn")]


def test_english_question_asking_for_hindi_is_answered_in_hindi(settings, tenant):
    llm = TranslatingLLM(TUITION, translations=[HI_TUITION])
    client = opted_in(settings, tenant, llm)
    _, events = ask(
        client,
        tenant,
        "What is the tuition fee for B.Tech CSE? Answer in Hindi",
        conv="m2",
        language="hi",
    )
    assert text_of(events) == HI_TUITION + " [1]"
    assert done_of(events)["language"] == "hi" and llm.translated[0]["language"] == "hi"


def test_a_kannada_question_is_answered_in_kannada(settings, tenant):
    kn = "ಪುರುಷರು ಮತ್ತು ಮಹಿಳೆಯರಿಗೆ ಪ್ರತ್ಯೇಕ ಹಾಸ್ಟೆಲ್‌ಗಳಿವೆ."
    llm = TranslatingLLM(HOSTELS, translations=[kn])
    client = opted_in(settings, tenant, llm)
    # The widget still shows EN: a question in Kannada is answered in Kannada all the same.
    _, events = ask(client, tenant, "ಹಾಸ್ಟೆಲ್ ಇದೆಯೆ?", conv="m3", language="en")
    assert text_of(events) == kn + " [1]"
    done = done_of(events)
    assert (done["language"], done["input_language"], done["language_reason"]) == (
        "kn",
        "kn",
        "same_as_input",
    )
    assert languages_in_db(tenant, "m3") == [("user", "kn"), ("assistant", "kn")]


def test_a_hindi_question_is_answered_in_hindi(settings, tenant):
    hi = "पुरुषों और महिलाओं के लिए अलग-अलग हॉस्टल हैं।"
    llm = TranslatingLLM(HOSTELS, translations=[hi])
    client = opted_in(settings, tenant, llm)
    _, events = ask(client, tenant, "क्या हॉस्टल है?", conv="m4")
    assert text_of(events) == hi + " [1]"
    assert done_of(events)["language"] == "hi"


def test_a_kannada_question_is_understood_in_english_and_answered_in_kannada(
    settings, tenant
):
    """Searched, routed and checked as English (the sources are English); the visitor's own
    words stay in the transcript. Seen live: a Kannada query missed the page an English one found."""
    llm = TranslatingLLM(
        TUITION,
        questions=["What is the tuition fee for CSE?"],
        translations=[KN_TUITION],
    )
    client = opted_in(settings, tenant, llm)
    _, events = ask(client, tenant, "ಸಿಎಸ್‌ಇ ಬೋಧನಾ ಶುಲ್ಕ ಎಷ್ಟು?", conv="m6")
    assert text_of(events) == KN_TUITION + " [1]"
    [u] = llm.understood
    assert u["model"].startswith("gemini/") and u["text"] == "ಸಿಎಸ್‌ಇ ಬೋಧನಾ ಶುಲ್ಕ ಎಷ್ಟು?"
    asked = answers(llm)[-1]["messages"][-1]["content"]
    assert "Visitor's question: What is the tuition fee for CSE?" in asked
    # The English question found the programme (CSE) and its catalogue fee: source [1].
    assert done_of(events)["sources"][0]["source_type"] == "catalogue"
    with psycopg.connect(TEST_URL) as conn:
        conn.execute("select set_config('app.tenant_id', %s, false)", (tenant,))
        said = conn.execute(
            "select content from chat.messages where conversation_id = 'm6' and sender = 'user'"
        ).fetchone()[0]
    assert said == "ಸಿಎಸ್‌ಇ ಬೋಧನಾ ಶುಲ್ಕ ಎಷ್ಟು?"


def test_when_the_question_cannot_be_put_into_english_it_is_used_as_written(
    settings, tenant
):
    from assistant_api.clients import UpstreamError

    kn = "ಪುರುಷರು ಮತ್ತು ಮಹಿಳೆಯರಿಗೆ ಪ್ರತ್ಯೇಕ ಹಾಸ್ಟೆಲ್‌ಗಳಿವೆ."
    llm = TranslatingLLM(
        HOSTELS, questions=[UpstreamError("Gemini: 429")], translations=[kn]
    )
    client = opted_in(settings, tenant, llm)
    _, events = ask(client, tenant, "ಹಾಸ್ಟೆಲ್ ಇದೆಯೆ?", conv="m7")
    assert text_of(events) == kn + " [1]"  # still answered, from the Kannada question


def test_code_mixed_kannada_english_is_answered_in_kannada(settings, tenant):
    llm = TranslatingLLM(TUITION, translations=[KN_TUITION])
    client = opted_in(settings, tenant, llm)
    _, events = ask(client, tenant, "B.Tech CSE fees eshtu?", conv="m5", language="en")
    done = done_of(events)
    assert done["language"] == "kn" and done["code_mixed"] is True
    assert text_of(events) == KN_TUITION + " [1]"


def test_a_translation_that_changes_a_number_is_never_shown(settings, tenant):
    llm = TranslatingLLM(TUITION, translations=["ಬಿ.ಟೆಕ್ ಸಿಎಸ್‌ಇ ಶುಲ್ಕ ವರ್ಷಕ್ಕೆ ₹2,05,000."])
    client = opted_in(settings, tenant, llm)
    _, events = ask(
        client, tenant, "What is the tuition fee for B.Tech CSE? Answer in Kannada"
    )
    shown = text_of(events)
    assert "2,05,000" not in shown and message("not_translated", "kn") in shown
    done = done_of(events)
    assert done["outcome"] == "partial" and done["offer_handoff"] is True


def test_a_rejected_translation_is_tried_once_more(settings, tenant):
    # Seen live: Gemini once dropped the fact itself (the COMEDK code) from a translation.
    llm = TranslatingLLM(TUITION, translations=["ಬಿ.ಟೆಕ್ ಸಿಎಸ್‌ಇ ಶುಲ್ಕ.", KN_TUITION])
    client = opted_in(settings, tenant, llm)
    _, events = ask(
        client, tenant, "What is the tuition fee for B.Tech CSE? Answer in Kannada"
    )
    assert text_of(events) == KN_TUITION + " [1]" and len(llm.translated) == 2
    assert done_of(events)["outcome"] == "answered"


def test_an_english_reply_costs_no_translation_call(settings, tenant):
    llm = TranslatingLLM(TUITION)
    client = opted_in(settings, tenant, llm)
    _, events = ask(
        client, tenant, "What is the tuition fee for B.Tech CSE?", language="en"
    )
    assert text_of(events) == TUITION and llm.translated == []


def test_a_college_without_gemini_keeps_its_local_only_behaviour(make_client, tenant):
    """Level 1 (no external model): no translation; Kannada gets catalogue templates or the
    admissions team, as before."""
    llm = TranslatingLLM(HOSTELS)
    client, _ = make_client(llm)
    _, events = ask(client, tenant, "Hostel?", language="kn")  # short: the pick decides
    assert text_of(events) == message("language_limited", "kn")
    assert answers(llm) == [] and llm.translated == []


# ------------------------------------------------------------------ switching mid-conversation


def test_switching_language_mid_conversation_keeps_the_context(settings, tenant):
    llm = TranslatingLLM(
        TUITION,  # 1: English
        TUITION,  # 2: "Kannada please" asks the same question again, in Kannada
        HOSTEL_FEE,  # 3: "its hostel fee" is still about B.Tech CSE, still in Kannada
        HOSTEL_FEE,  # 4: "English please"
        translations=[KN_TUITION, KN_HOSTEL_FEE],
    )
    client = opted_in(settings, tenant, llm)
    session = str(uuid.uuid4())

    def turn(q):
        _, events = ask(client, tenant, q, conv="sw", session=session, language="en")
        return text_of(events), done_of(events)

    said, done = turn("What is the tuition fee for B.Tech CSE?")
    assert said == TUITION and done["language"] == "en"

    said, done = turn("Kannada please")
    assert said == KN_TUITION + " [1]"
    assert (done["language"], done["language_reason"]) == ("kn", "requested")
    assert "tuition fee for B.Tech CSE" in answers(llm)[-1]["messages"][-1]["content"]

    # An English follow-up: Kannada still (asked for), and "its" is still B.Tech CSE.
    said, done = turn("and what about its hostel fee?")
    assert said == KN_HOSTEL_FEE + " [1]" and done["language"] == "kn"
    prompt = answers(llm)[-1]["messages"][-1]["content"]
    assert "B.Tech Computer Science and Engineering" in prompt
    assert "90,000" in prompt or "90000" in prompt  # the programme's own hostel fee

    said, done = turn("English please")
    assert said == HOSTEL_FEE and done["language"] == "en"
    assert len(llm.translated) == 2  # English replies were never translated
    assert languages_in_db(tenant, "sw") == [
        ("user", "en"),
        ("assistant", "en"),
        ("user", "en"),
        ("assistant", "kn"),
        ("user", "en"),
        ("assistant", "kn"),
        ("user", "en"),
        ("assistant", "en"),
    ]


def test_asking_only_for_a_language_confirms_it_without_a_model_call(settings, tenant):
    llm = TranslatingLLM()
    client = opted_in(settings, tenant, llm)
    _, events = ask(client, tenant, "हिंदी में बताइए", conv="p1")
    assert text_of(events) == message("language_set", "hi")
    done = done_of(events)
    assert done["outcome"] == "clarify" and done["language"] == "hi"
    assert answers(llm) == []


def test_text_or_voice_is_remembered_for_the_conversation(settings, tenant):
    llm = TranslatingLLM(TUITION)
    client = opted_in(settings, tenant, llm)
    session = str(uuid.uuid4())
    _, events = ask(client, tenant, "text only please", conv="o1", session=session)
    assert text_of(events) == message("output_text", "en")
    _, events = ask(
        client,
        tenant,
        "What is the tuition fee for B.Tech CSE?",
        conv="o1",
        session=session,
    )
    assert done_of(events)["output_mode"] == "text"


# ------------------------------------------------------------------ voice: push-to-talk


def voice_turn(client, tok, **data):
    return client.post(
        "/voice/turn",
        data={"token": tok, **data},
        files={"audio": ("q.webm", b"webm-audio", "audio/webm")},
    ).json()


def test_kannada_speech_is_answered_in_kannada_with_kannada_audio(settings, tenant):
    sarvam = FakeSarvam(heard=("CSE ಶುಲ್ಕ ಎಷ್ಟು?", "kn"))
    speech = MultilingualSpeech(FakeLocal(), sarvam)
    client = opted_in(
        settings, tenant, TranslatingLLM(), speech=speech, direct_catalogue_answers=True
    )
    tok = token(client, tenant, str(uuid.uuid4()), conv="v-kn")
    # English picked in the widget; Sarvam heard Kannada (not a mishearing: it detects).
    r = voice_turn(client, tok, language="en")
    assert r["transcript"] == "CSE ಶುಲ್ಕ ಎಷ್ಟು?" and r["language"] == "kn"
    assert "2,50,000" in r["answer"] and "ಬೋಧನಾ" in r["answer"] or "ಶುಲ್ಕ" in r["answer"]
    assert r["audio_wav_base64"] and r["providers"] == {
        "stt": "sarvam",
        "tts": "sarvam",
    }
    tts = [c for c in sarvam.calls if c[0] == "tts"]
    assert tts and tts[-1][1] == "kn" and "[1]" not in tts[-1][3]


def test_english_speech_with_hindi_replies_is_answered_and_spoken_in_hindi(
    settings, tenant
):
    sarvam = FakeSarvam(heard=("What is the tuition fee for B.Tech CSE?", "en"))
    llm = TranslatingLLM(TUITION, translations=[HI_TUITION])
    client = opted_in(
        settings, tenant, llm, speech=MultilingualSpeech(FakeLocal(), sarvam)
    )
    tok = token(client, tenant, str(uuid.uuid4()), conv="v-hi")
    r = voice_turn(client, tok, language="en", response_language="hi")
    assert r["language"] == "hi" and r["answer"] == HI_TUITION + " [1]"
    assert [c[1] for c in sarvam.calls if c[0] == "tts"] == ["hi"]


def test_text_output_makes_no_audio(settings, tenant):
    sarvam = FakeSarvam(heard=("CSE ಶುಲ್ಕ ಎಷ್ಟು?", "kn"))
    client = opted_in(
        settings,
        tenant,
        TranslatingLLM(),
        speech=MultilingualSpeech(FakeLocal(), sarvam),
        direct_catalogue_answers=True,
    )
    tok = token(client, tenant, str(uuid.uuid4()), conv="v-txt")
    r = voice_turn(client, tok, output="text")
    assert r["answer"] and r["audio_wav_base64"] is None
    assert not [c for c in sarvam.calls if c[0] == "tts"]


def test_speak_reads_each_language_with_its_own_voice(settings, tenant, make_client):
    from conftest import visitor_token

    sarvam = FakeSarvam()
    client = opted_in(
        settings,
        tenant,
        TranslatingLLM(),
        speech=MultilingualSpeech(FakeLocal(), sarvam),
    )
    auth = {"Authorization": f"Bearer {visitor_token(tenant)}"}
    r = client.post(
        "/voice/speak",
        json={"text": "ಶುಲ್ಕ ₹2,50,000 [1].", "language": "kn"},
        headers=auth,
    )
    assert r.status_code == 200 and r.content == b"SARVAM-wav"
    assert client.post(
        "/voice/speak", json={"text": "Fee."}, headers=auth
    ).content.startswith(b"RIFF")
    # Without Sarvam there is no Kannada voice: a clear 422, never English-voiced Kannada.
    local_only = opted_in(
        settings, tenant, TranslatingLLM(), speech=MultilingualSpeech(FakeLocal(), None)
    )
    r = local_only.post(
        "/voice/speak", json={"text": "ಶುಲ್ಕ", "language": "kn"}, headers=auth
    )
    assert r.status_code == 422


# ------------------------------------------------------------------ voice: a Retell call


def test_a_retell_call_switches_between_kannada_and_english(settings, tenant):
    client = opted_in(settings, tenant, TranslatingLLM(), direct_catalogue_answers=True)
    call_id = "call-" + uuid.uuid4().hex[:8]
    retell.remember(
        call_id,
        retell.RetellCall(
            tenant, "rc-" + call_id, str(uuid.uuid4()), "en", time.time()
        ),
    )
    with client.websocket_connect(f"/voice/retell/llm/{call_id}") as ws:
        assert ws.receive_json()["response_type"] == "config"
        assert (
            "Hello" in ws.receive_json()["content"]
        )  # greeting in the picked language

        def say(rid, words):
            ws.send_text(
                json.dumps(
                    {
                        "interaction_type": "response_required",
                        "response_id": rid,
                        "transcript": [{"role": "user", "content": words}],
                    }
                )
            )
            out = ""
            while True:
                m = ws.receive_json()
                if m.get("response_id") != rid:
                    continue
                out += m.get("content", "")
                if m.get("content_complete"):
                    return out.strip()

        kannada = say(1, "CSE ಶುಲ್ಕ ಎಷ್ಟು?")
        assert "2,50,000" in kannada and "[1]" not in kannada  # markers are not spoken
        assert any("ಀ" <= ch <= "೿" for ch in kannada)
        # Mid-call: "English please" asks the same question again, in English.
        english = say(2, "English please")
        assert english.startswith("For B.Tech Computer Science and Engineering")
        assert not any("ಀ" <= ch <= "೿" for ch in english)


# ------------------------------------------------------------------ WhatsApp: text and voice notes


def wa(settings, tenant, speech, llm=None, **s_kw):
    s = replace(
        settings,
        whatsapp_app_secret=SECRET,
        whatsapp_verify_token="vt",
        contact_salt="salt",
        direct_catalogue_answers=True,
        **s_kw,
    )
    store.register_channel(s.database_url, tenant, "whatsapp", PNID)
    sender = OutboxSender(s.database_url)
    app = create_app(
        s,
        Deps(s, FakeKnowledge(), llm or TranslatingLLM(), RateLimiter(100, 60)),
        FakeExperience(),
        sender,
        speech,
    )
    client = TestClient(app)
    # The college opted in to Sarvam for voice notes (model_policy.voice_external).
    r = client.post(
        "/tenant-pack",
        json={
            "college": {"name": "Demo College"},
            "model_policy": {"voice_external": ["sarvam"]},
        },
        headers={"X-Service-Key": "key-from-c", "X-Tenant-Id": tenant},
    )
    assert r.status_code == 200, r.text
    return client, sender


def outbox_all(tenant):
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        return [
            r[0]
            for r in conn.execute(
                "select payload from chat.wa_outbox where tenant_id = %s order by id",
                (tenant,),
            )
        ]


def voice_note(i, media_id):
    return {
        "id": f"wamid.v{i}",
        "from": "919845012345",
        "type": "audio",
        "audio": {"id": media_id, "mime_type": "audio/ogg; codecs=opus", "voice": True},
    }


def test_a_hindi_whatsapp_message_is_answered_in_hindi(settings, tenant):
    client, _ = wa(settings, tenant, MultilingualSpeech(FakeLocal(), None))
    post(client, payload(text(1, "CSE की फीस कितनी है?")))
    [msg] = outbox_all(tenant)
    assert "फीस" in msg["text"] or "शुल्क" in msg["text"]
    assert "2,50,000" in msg["text"] and "स्रोत:" in msg["text"]  # "Sources" in Hindi


def test_a_kannada_voice_note_gets_kannada_text_and_a_kannada_voice_note(
    settings, tenant
):
    sarvam = FakeSarvam(heard=("CSE ಶುಲ್ಕ ಎಷ್ಟು?", "kn"))
    client, sender = wa(settings, tenant, MultilingualSpeech(FakeLocal(), sarvam))
    sender.put_media("media-1", b"OggS-voice-note", "audio/ogg; codecs=opus")
    post(client, payload(voice_note(1, "media-1")))
    text_msg, audio_msg = outbox_all(tenant)
    assert text_msg["text"].startswith("ನೀವು ಹೇಳಿದ್ದು: “CSE ಶುಲ್ಕ ಎಷ್ಟು?”")
    assert "2,50,000" in text_msg["text"] and "ಮೂಲಗಳು:" in text_msg["text"]
    assert audio_msg["type"] == "audio" and audio_msg["mime"] == "audio/mpeg"
    assert audio_msg["bytes"] == len(
        b"SARVAM-mp3"
    )  # size and hash only, never the audio
    assert ("stt", "audio/ogg; codecs=opus", None) in sarvam.calls
    tts = [c for c in sarvam.calls if c[0] == "tts"]
    assert tts == [("tts", "kn", "mp3", tts[0][3])] and "[1]" not in tts[0][3]


def test_an_english_voice_note_is_answered_with_a_local_voice_note(settings, tenant):
    local = FakeLocal("What is the fee for CSE?")
    client, sender = wa(settings, tenant, MultilingualSpeech(local, None))
    sender.put_media("media-2", b"OggS", "audio/ogg")
    post(client, payload(voice_note(2, "media-2")))
    text_msg, audio_msg = outbox_all(tenant)
    assert text_msg["text"].startswith("You said: “What is the fee for CSE?”")
    assert (
        audio_msg["mime"] == "audio/ogg"
    )  # Piper's WAV turned into a WhatsApp voice note


def test_text_only_on_whatsapp_means_no_voice_notes(settings, tenant):
    sarvam = FakeSarvam(heard=("CSE ಶುಲ್ಕ ಎಷ್ಟು?", "kn"))
    client, sender = wa(settings, tenant, MultilingualSpeech(FakeLocal(), sarvam))
    post(client, payload(text(1, "text only")))
    sender.put_media("media-3", b"OggS", "audio/ogg")
    post(client, payload(voice_note(3, "media-3")))
    sent = outbox_all(tenant)
    assert sent[0]["text"] == message("output_text", "en")
    assert [m["type"] for m in sent[1:]] == ["interactive"]  # text, no audio


def test_a_voice_note_that_cannot_be_fetched_asks_to_type(settings, tenant):
    client, _ = wa(settings, tenant, MultilingualSpeech(FakeLocal(), FakeSarvam()))
    post(client, payload(voice_note(4, "missing")))
    [msg] = outbox_all(tenant)
    assert "can't listen to voice notes" in msg["text"]


def test_when_gemini_cannot_translate_sarvam_does_with_the_same_checks(
    settings, tenant, monkeypatch
):
    """Seen 19 Nov 2026: Gemini's quota ran out and every Kannada answer became "could not
    answer reliably". Sarvam (the college opted in) translates the same checked text."""
    from assistant_api import sarvam as sarvam_api

    sent = []

    def fake_translate(key, text, language, timeout=15.0):
        sent.append((text, language))
        return KN_TUITION

    monkeypatch.setattr(sarvam_api, "translate", fake_translate)
    llm = TranslatingLLM(TUITION, translations=["ಬಿ.ಟೆಕ್ ಶುಲ್ಕ.", "ಬಿ.ಟೆಕ್ ಶುಲ್ಕ."])
    client = opted_in(settings, tenant, llm, sarvam_api_key="sk-test")
    _, events = ask(
        client, tenant, "What is the tuition fee for B.Tech CSE? Answer in Kannada"
    )
    assert text_of(events) == KN_TUITION + " [1]"
    assert sent and sent[0][1] == "kn" and "[1]" not in sent[0][0]  # markers stay here


def test_a_sarvam_translation_that_changes_a_number_is_never_shown(
    settings, tenant, monkeypatch
):
    from assistant_api import sarvam as sarvam_api

    monkeypatch.setattr(
        sarvam_api,
        "translate",
        lambda key, text, language, timeout=15.0: "ಬಿ.ಟೆಕ್ ಸಿಎಸ್‌ಇ ಶುಲ್ಕ ವರ್ಷಕ್ಕೆ ₹2,05,000.",
    )
    llm = TranslatingLLM(TUITION, translations=["ಬಿ.ಟೆಕ್ ಶುಲ್ಕ.", "ಬಿ.ಟೆಕ್ ಶುಲ್ಕ."])
    client = opted_in(settings, tenant, llm, sarvam_api_key="sk-test")
    _, events = ask(
        client, tenant, "What is the tuition fee for B.Tech CSE? Answer in Kannada"
    )
    assert "2,05,000" not in text_of(events)
    assert done_of(events)["outcome"] == "partial"
