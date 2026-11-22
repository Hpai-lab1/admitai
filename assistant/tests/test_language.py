"""Input language vs response language (language.py): detection, code-mixing, asked-for
preferences and the decision, without a model or a database."""

import pytest

from assistant_api.language import decide, detect, preferences, preview


# ------------------------------------------------------------------ detection


@pytest.mark.parametrize(
    "text, lang, script, mixed",
    [
        ("What is the fee for B.Tech CSE?", "en", "latin", False),
        ("ಬಿ.ಟೆಕ್ ಶುಲ್ಕ ಎಷ್ಟು?", "kn", "kannada", False),
        ("बी.टेक की फीस कितनी है?", "hi", "devanagari", False),
        # Code-mixed: English words in an Indic sentence, or Indic words in English letters.
        ("CSE ಶುಲ್ಕ ಎಷ್ಟು?", "kn", "kannada", True),
        ("B.Tech की fees कितनी है?", "hi", "devanagari", True),
        ("B.Tech fees eshtu?", "kn", "latin", True),
        ("first year ge hostel ideya?", "kn", "latin", True),
        ("B.Tech ka fees kitna hai", "hi", "latin", True),
    ],
)
def test_the_input_language_is_detected_with_code_mixing(text, lang, script, mixed):
    d = detect(text)
    assert (d.language, d.script, d.code_mixed) == (lang, script, mixed)


def test_short_english_messages_do_not_tell_the_language():
    assert not detect("yes").strong and not detect("2").strong
    assert not detect("B.Tech fees?").strong
    assert detect("What is the hostel fee?").strong


# ------------------------------------------------------------------ asked-for preferences


@pytest.mark.parametrize(
    "text, lang",
    [
        ("Kannada please", "kn"),
        ("reply in Kannada", "kn"),
        ("ಕನ್ನಡದಲ್ಲಿ ಹೇಳಿ", "kn"),
        ("Kannada alli heli", "kn"),
        ("हिंदी में बताइए", "hi"),
        ("in Hindi please", "hi"),
        ("switch to English", "en"),
        ("English please", "en"),
    ],
)
def test_asking_only_for_a_language_sets_it(text, lang):
    p = preferences(text)
    assert (p.language, p.only, p.question) == (lang, True, "")


def test_a_language_request_with_a_question_keeps_the_question():
    p = preferences("Can you explain the syllabus in Hindi?")
    assert (p.language, p.only, p.question) == (
        "hi",
        False,
        "Can you explain the syllabus?",
    )
    p = preferences("What is the fee? Answer in Hindi")
    assert (p.language, p.question) == ("hi", "What is the fee?")
    p = preferences("B.Tech fees Hindi mein batao")
    assert (p.language, p.question) == ("hi", "B.Tech fees")


@pytest.mark.parametrize(
    "text",
    [
        "Is the course taught in English?",
        "Is Kannada compulsory?",
        "Do you teach Hindi?",
        "What is the hostel fee?",
    ],
)
def test_questions_about_a_language_are_not_requests(text):
    p = preferences(text)
    assert p.language is None and p.output is None and p.question == text


@pytest.mark.parametrize(
    "text, mode",
    [
        ("text only", "text"),
        ("no voice please", "text"),
        ("reply with voice", "voice"),
        ("send voice notes please", "voice"),
        ("both text and voice", "both"),
    ],
)
def test_text_voice_or_both_can_be_asked_for(text, mode):
    p = preferences(text)
    assert p.output == mode and p.only


# ------------------------------------------------------------------ the decision


def test_an_english_question_is_answered_in_english_whatever_is_picked():
    # decided 17 Nov 2026: with हिं or ಕ picked, English questions were answered in Hindi or
    # Kannada, which read as a fault; now every language answers in itself
    for picked in ("en", "hi", "kn", None):
        assert decide(detect("What is the fee?"), picked, {}) == ("en", "same_as_input")
    assert decide(
        detect("How many seats are there in B.Des. Fashion Design?"),
        "kn",
        {"last_language": "kn"},
    ) == ("en", "same_as_input")


def test_the_picked_language_still_answers_short_messages():
    # "yes", "2", "B.Tech fees?" tell no language: the widget's pick decides
    for short in ("yes", "2", "B.Tech fees?"):
        assert decide(detect(short), "hi", {}) == ("hi", "preference")


def test_asking_for_a_language_still_wins_over_an_english_question():
    state: dict = {}
    assert decide(detect("reply in Kannada"), "en", state, "kn")[0] == "kn"
    assert decide(detect("What is the fee?"), "en", state) == ("kn", "requested")


def test_hindi_and_kannada_questions_are_answered_in_their_own_language():
    assert decide(detect("ಹಾಸ್ಟೆಲ್ ಇದೆಯೆ?"), "en", {}) == ("kn", "same_as_input")
    assert decide(detect("क्या हॉस्टल है?"), "en", {}) == ("hi", "same_as_input")
    # Whatever was picked: a Kannada question gets Kannada.
    assert decide(detect("ಹಾಸ್ಟೆಲ್ ಇದೆಯೆ?"), "hi", {})[0] == "kn"


def test_a_request_in_the_conversation_wins_until_changed():
    state = {}
    assert decide(detect("reply in Hindi"), "en", state, asked="hi") == (
        "hi",
        "requested",
    )
    # Next messages: English questions are answered in Hindi; the widget still sends its
    # unchanged pick ("en"), which does not undo the request. A question written in Kannada is
    # answered in Kannada (19 Nov 2026), and the request stays for the next English one.
    assert decide(detect("What is the fee?"), "en", state) == ("hi", "requested")
    assert decide(detect("ಹಾಸ್ಟೆಲ್ ಇದೆಯೆ?"), "en", state) == ("kn", "same_as_input")
    assert decide(detect("What is the fee?"), "en", state) == ("hi", "requested")
    assert decide(detect("English please"), "en", state, asked="en") == (
        "en",
        "requested",
    )
    assert decide(detect("What is the fee?"), "en", state)[0] == "en"


def test_a_new_pick_in_the_widget_replaces_an_earlier_request():
    state = {}
    decide(detect("What is the fee?"), "en", state)
    decide(detect("Kannada please"), "en", state, asked="kn")
    # the new pick (हिं) replaces the request: short messages now follow it
    assert decide(detect("B.Tech fees?"), "hi", state) == ("hi", "preference")
    # and a full English question is still answered in English (17 Nov 2026)
    assert decide(detect("What is the fee?"), "hi", state) == ("en", "same_as_input")


def test_a_short_reply_keeps_the_conversations_language():
    state = {"last_language": "kn"}
    assert decide(detect("yes"), "en", state) == ("kn", "continuing")
    assert decide(detect("B.Tech fees?"), None, state) == ("kn", "continuing")
    # A full English question goes by the pick again.
    assert decide(detect("What is the hostel fee?"), "en", state)[0] == "en"


def test_preview_does_not_change_the_conversation():
    state = {"reply_language": {"lang": "hi", "by": "request"}}
    assert preview("What is the fee?", "en", state) == "hi"
    assert preview("Kannada please", "en", state) == "kn"
    assert state == {"reply_language": {"lang": "hi", "by": "request"}}


# ------------------------------------------------------------------ translations keep the facts


def test_a_translation_must_keep_every_number_date_and_code():
    from assistant_api.engine import same_facts

    en = "The COMEDK code of Ramaiah University of Applied Science (RUAS) is E197."
    assert same_facts(en, "RUAS ನ COMEDK ಕೋಡ್ E197 ಆಗಿದೆ.")
    assert not same_facts(en, "Rama ಅವರ COMEDK ಕೋಡ್")  # seen live: the code was dropped
    fee = "The fee is ₹2,50,000 per year."
    assert same_facts(fee, "ಶುಲ್ಕ ವರ್ಷಕ್ಕೆ ₹2,50,000.")
    assert not same_facts(fee, "ಶುಲ್ಕ ವರ್ಷಕ್ಕೆ ₹2,05,000.")
    assert same_facts(
        "Gates close at 21:30.", "ಗೇಟ್‌ಗಳು ೨೧:೩೦ ಕ್ಕೆ ಮುಚ್ಚುತ್ತವೆ."
    )  # Kannada digits
    assert not same_facts("Hostels are separate.", "ಹಾಸ್ಟೆಲ್ 2 ಇವೆ.")  # nothing added


def test_an_academic_year_with_a_kannada_suffix_is_the_same_fact():
    """Live, 17 Nov 2026: every correct translation of an answer with "2026-27" was rejected."""
    from assistant_api.engine import same_facts

    en = "For 2026-27 the total fee is 6,00,000."
    assert same_facts(en, "2026-27ನೇ ಸಾಲಿಗೆ ಒಟ್ಟು ಶುಲ್ಕ 6,00,000 ಇರುತ್ತದೆ.")
    assert not same_facts(en, "2026-27ನೇ ಸಾಲಿಗೆ ಒಟ್ಟು ಶುಲ್ಕ 6,50,000 ಇರುತ್ತದೆ.")
    assert not same_facts(en, "2026-2027 ಸಾಲಿಗೆ ಶುಲ್ಕ 6,00,000.")  # rewritten, not kept


def test_a_translation_with_letters_of_another_script_is_not_used():
    from assistant_api.engine import clean_script

    assert clean_script("हाँ, हॉस्टल के बारे में जानकारी है।", "hi")
    assert not clean_script("सुविधाओं के बारेغ में", "hi")  # an Arabic letter, seen live
    assert clean_script("ಹೌದು, B.Tech ಹಾಸ್ಟೆಲ್ ಇದೆ.", "kn")  # English letters are fine
    assert not clean_script("ಹೌದು, ಹಾಸ್ಟೆಲ್ है.", "kn")


def test_a_hindi_question_after_a_kannada_request_is_answered_in_hindi():
    """Seen 19 Nov 2026: after "ಕನ್ನಡದಲ್ಲೇ ಹೇಳಿ", a question in Hindi was answered in Kannada."""
    state: dict = {}
    decide(detect("Kannada please"), "en", state, "kn")
    assert decide(detect("बी.टेक की फीस कितनी है?"), "en", state) == (
        "hi",
        "same_as_input",
    )
    # Hinglish in English letters is less certain: the request still decides it
    assert decide(detect("B.Tech ka fees kitna hai"), "en", state)[0] == "kn"


def test_on_a_call_a_few_words_in_another_script_keep_the_calls_language():
    """Seen 19 Nov 2026: speech-to-text wrote English or mixed speech partly in Kannada letters
    and the call switched to Kannada."""
    from assistant_api.language import decide, detect

    for heard in [
        "International online ಬರುತ್ತೆ. Okay, thank you. What is the fee?",
        "कंप्यूटर साइंस ಮತ್ತು ಮೆಕಾನಿಕಲ್ difference",
        "Call to change my assistant please. ಕನ್ನಡ",
    ]:
        state = {"last_language": "en"}
        assert decide(detect(heard), "en", state, spoken=True)[0] == "en", heard


def test_on_a_call_a_sentence_mostly_in_one_script_still_switches():
    from assistant_api.language import decide, detect

    state = {"last_language": "en"}
    assert decide(detect("बीपीटी में कितने सीट्स हैं?"), "en", state, spoken=True)[0] == "hi"
    state = {"last_language": "en"}
    assert (
        decide(
            detect("BPT ಕೋರ್ಸ್ ಮತ್ತು BBA ಕೋರ್ಸ್ ನಡುವೆ ಏನು ವ್ಯತ್ಯಾಸ?"), "en", state, spoken=True
        )[0]
        == "kn"
    )


def test_typed_chat_still_answers_any_indian_script_in_that_language():
    from assistant_api.language import decide, detect

    state = {"last_language": "en"}
    assert decide(detect("कंप्यूटर साइंस ಮತ್ತು ಮೆಕಾನಿಕಲ್ difference"), "en", state)[0] in (
        "hi",
        "kn",
    )


def test_on_a_call_an_unclear_sentence_follows_the_tab_the_visitor_picked():
    """Seen 19 Nov 2026: Hindi tab, Hindi speech written mostly in Kannada letters, answered in
    English because the previous reply was English."""
    from assistant_api.language import decide, detect

    heard = "ಆರ್ಟಿಫಿಷಿಯಲ್ ಇಂಟೆಲಿಜೆನ್ಸ್ or artificial data science ಕಾರ್ಡ್. ಡಿಫರೆನ್ಸ್ क्या है?"
    state = {
        "last_language": "en",
        "picked_language": "hi",
        "reply_language": {"lang": "hi", "by": "picker"},
    }
    assert decide(detect(heard), "hi", state, spoken=True)[0] == "hi"
