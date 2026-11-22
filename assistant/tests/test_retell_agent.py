"""The Retell agent's settings (scripts/retell_agent.py): English-only as before, or English, Hindi
and Kannada in one call with Soniox speech recognition and a voice that speaks all three."""

import pytest

from scripts.retell_agent import agent_body

WS = "wss://example.trycloudflare.com/voice/retell/llm"


def test_english_only_is_the_existing_agent():
    body = agent_body(WS, ["en"])
    assert body["response_engine"] == {"type": "custom-llm", "llm_websocket_url": WS}
    assert body["language"] == "en-IN" and body["voice_id"] == "11labs-Monika"
    assert "stt_mode" not in body and "voice_model" not in body


def test_three_languages_need_soniox_and_a_voice_that_speaks_kannada():
    body = agent_body(WS, ["en", "hi", "kn"])
    assert body["language"] == ["en-IN", "hi-IN", "kn-IN"]
    # Retell: only Soniox and Azure recognise Kannada; Soniox also follows code-switching.
    assert body["stt_mode"] == "custom"
    assert body["custom_stt_config"]["provider"] == "soniox"
    assert 500 <= body["custom_stt_config"]["endpointing_ms"] <= 3000
    # One voice for every language: ElevenLabs v3 speaks Kannada and Hindi (multilingual v2 and
    # flash do not); OpenAI's gpt-4o-mini-tts changed its voice between sentences.
    assert (body["voice_id"], body["voice_model"]) == ("11labs-Monika", "eleven_v3")
    assert (
        body["response_engine"]["type"] == "custom-llm"
    )  # answers still come from here


def test_english_and_hindi_keep_retells_default_recognition():
    body = agent_body(WS, ["en", "hi"])
    assert body["language"] == ["en-IN", "hi-IN"] and "stt_mode" not in body


def test_a_chosen_voice_and_unknown_languages():
    body = agent_body(WS, ["en", "kn"], voice="11labs-Monika", voice_model="eleven_v3")
    assert (body["voice_id"], body["voice_model"]) == ("11labs-Monika", "eleven_v3")
    with pytest.raises(ValueError):
        agent_body(WS, ["en", "ta"])
