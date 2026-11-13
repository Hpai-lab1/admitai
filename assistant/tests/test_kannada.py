"""Kannada (added 9 Nov 2026): detection, routing, replies, safety checks and spoken numbers."""

from assistant_api import whatsapp
from assistant_api.checks import ELIGIBILITY_VERDICT, check_sentence
from assistant_api.guard import detect_language
from assistant_api.prompts import ENGLISH_ONLY_NOTE, LANGUAGE_LINE, message
from assistant_api.router import INTENTS
from assistant_api.spoken_lead import spoken_rating


def test_kannada_is_detected_and_can_be_chosen():
    assert detect_language("ಬಿ.ಟೆಕ್ ಶುಲ್ಕ ಎಷ್ಟು?") == "kn"
    assert detect_language("What is the fee?", requested="kn") == "kn"
    assert detect_language("What is the fee?", requested="xx") == "en"


def test_kannada_questions_are_routed_by_topic():
    import re

    def intents(q):
        return {
            k
            for k, (_, pats) in INTENTS.items()
            if any(re.search(p, q, re.I) for p in pats)
        }

    assert "fees" in intents("ಬಿ.ಟೆಕ್ ಶುಲ್ಕ ಎಷ್ಟು?")
    assert "hostel" in intents("ಹಾಸ್ಟೆಲ್ ಇದೆಯೆ?")
    assert "dates" in intents("ಅರ್ಜಿ ಸಲ್ಲಿಸಲು ಕೊನೆಯ ದಿನಾಂಕ ಯಾವುದು?")


def test_fixed_replies_and_notes_exist_in_kannada():
    assert message("no_answer", "kn") != message("no_answer", "en")
    assert "ಕ" in message("closed", "kn")
    assert "kn" in ENGLISH_ONLY_NOTE and "Kannada" in LANGUAGE_LINE["kn"]
    assert whatsapp.t("ask_contact", "kn") != whatsapp.t("ask_contact", "en")


def test_kannada_yes_and_no_on_whatsapp():
    assert whatsapp.YES.match("ಹೌದು") and whatsapp.YES.match("ಹೌದು.")
    assert whatsapp.NO.match("ಇಲ್ಲ") and not whatsapp.YES.match("ಇಲ್ಲ")


def test_kannada_eligibility_verdicts_and_promises_are_blocked():
    assert check_sentence("ನೀವು ಅರ್ಹರಲ್ಲ.", []).reason == ELIGIBILITY_VERDICT
    assert check_sentence("ನೀವು ಅರ್ಜಿ ಸಲ್ಲಿಸಲು ಸಾಧ್ಯವಿಲ್ಲ.", []).reason == ELIGIBILITY_VERDICT
    assert check_sentence("ನಿಮಗೆ ಖಚಿತ ಪ್ರವೇಶ ಸಿಗುತ್ತದೆ.", []).reason == "promise"


def test_spoken_ratings_in_kannada():
    assert spoken_rating("ನಾಲ್ಕು") == 4
    assert spoken_rating("೫") == 5
    assert spoken_rating("5 ರಲ್ಲಿ ನಾಲ್ಕು") == 4


def test_kannada_typed_in_english_letters_is_kannada():
    assert detect_language("B.Tech fees eshtu?") == "kn"
    assert detect_language("first year ge hostel ideya?") == "kn"
    assert detect_language("hostel ide na?") == "kn"
    assert detect_language("Is there an IDE lab?") == "en"
    assert detect_language("kya fees hai?") == "hi"


def test_kannada_heard_in_another_script_counts_as_misheard():
    from assistant_api.routes.voice import _wrong_script

    assert _wrong_script(
        "ھر جی سالی سوہ کنے یا دینام", "kn"
    )  # Urdu letters for Kannada
    assert _wrong_script("हर जी साली", "kn")  # Devanagari for Kannada
    assert not _wrong_script("ಅರ್ಜಿ ಸಲ್ಲಿಸಲು ಕೊನೆಯ ದಿನಾಂಕ", "kn")
    assert not _wrong_script("B.Tech fees eshtu", "kn")  # English letters are fine
    assert not _wrong_script("2027", "kn")


def test_the_language_the_visitor_chose_always_decides_the_reply():
    """EN, हिं or ಕ chosen in the widget wins over what the question is typed or heard in."""
    assert detect_language("ಬಿ.ಟೆಕ್ ಶುಲ್ಕ ಎಷ್ಟು?", requested="en") == "en"
    assert detect_language("ಬಿ.ಟೆಕ್ ಶುಲ್ಕ ಎಷ್ಟು?", requested="hi") == "hi"
    assert detect_language("फीस कितनी है?", requested="kn") == "kn"
    # Without a choice (WhatsApp), the language is still recognised from the text.
    assert detect_language("ಬಿ.ಟೆಕ್ ಶುಲ್ಕ ಎಷ್ಟು?") == "kn"
    assert detect_language("फीस कितनी है?") == "hi"


def test_an_english_mode_visitor_heard_in_another_script_is_asked_again():
    from assistant_api.routes.voice import _wrong_script

    assert _wrong_script(
        "ಕಯಸರತಳಸಸ್", "en"
    )  # the screenshot: English chosen, heard as Kannada
    assert not _wrong_script("What is the fee for B.Tech?", "en")
    assert _wrong_script("ಅರ್ಜಿ", "hi") and not _wrong_script("आवेदन की तिथि", "hi")
