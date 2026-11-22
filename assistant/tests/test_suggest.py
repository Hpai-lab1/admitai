"""'Did you mean ...?' for misheard or misspelt programme names (suggest.py)."""

from assistant_api.suggest import (
    AFFIRMATIVE,
    NEGATIVE,
    choice,
    corrected,
    did_you_mean,
    phrase,
    related_questions,
    strip_focus,
)

PROGRAMS = [
    {"code": "BT-AERO", "name": "B. Tech. in Aerospace Engineering", "aliases": []},
    {"code": "MT-AERO", "name": "M.Tech. in Aerospace Engineering", "aliases": []},
    {"code": "BV-MECH", "name": "B.Voc. in Mechatronics", "aliases": []},
    {"code": "MSC-BIO", "name": "M.Sc. Biotechnology", "aliases": []},
    {"code": "BT-CIVIL", "name": "B.Tech. in Civil Engineering", "aliases": []},
    {"code": "BDES-FD", "name": "B.Des. Fashion Design", "aliases": []},
]


def questions(text):
    return [o.question for o in did_you_mean(text, PROGRAMS)]


def test_a_misheard_name_becomes_the_corrected_question():
    # Speech-to-text heard "Aerospace" as "Nero Space"; "B.Tech" picks the B. Tech. programme.
    assert questions("What is the eligibility of B.Tech, Nero Space Engineering?") == [
        "What is the eligibility of B. Tech. in Aerospace Engineering?"
    ]


def test_a_misspelt_name_is_corrected():
    assert questions("fees for mechatronix") == ["fees for B.Voc. in Mechatronics"]


def test_the_degree_the_visitor_said_wins_among_equally_close_names():
    assert questions("m tech aero space engineering fees") == [
        "M.Tech. in Aerospace Engineering fees"
    ]


def test_without_a_degree_both_close_programmes_are_offered():
    assert len(questions("tell me about aero space engineering")) == 2


def test_ordinary_questions_get_no_suggestion():
    for text in (
        "Is there a hostel for first year students?",
        "What is the COMEDK code?",
        "What documents are required?",
        "Is there a scholarship for sports?",
    ):
        assert questions(text) == [], text


def test_a_fragment_of_a_name_is_not_a_mishearing():
    # "technology" is part of "biotechnology", not a misheard name.
    assert questions("does the college have a food technology course") == []


def test_a_window_does_not_swallow_filler_words():
    assert questions("tell me about sivil engineering") == [
        "tell me about B.Tech. in Civil Engineering"
    ]


def test_yes_and_no_in_three_languages():
    for said in ("yes", "Yes please", "yeah", "haan", "हाँ", "ಹೌದು", "correct"):
        assert AFFIRMATIVE.match(said), said
    for said in ("no", "nope", "नहीं", "ಇಲ್ಲ"):
        assert NEGATIVE.match(said), said
    assert not AFFIRMATIVE.match("yes what is the hostel fee")
    assert not NEGATIVE.match("no hostel for girls?")


def test_phrase_quotes_one_and_numbers_several():
    assert phrase(["A?"]) == "“A?”"
    assert phrase(["A?", "B?"]) == "1. “A?” 2. “B?”"


def test_a_correct_name_written_differently_is_recognised_not_questioned():
    # Only the dots differ from "B.Des. Fashion Design": the engine uses it without asking.
    (offer,) = did_you_mean("What careers can B.Des Fashion Design lead to?", PROGRAMS)
    assert offer.exact and offer.program["code"] == "BDES-FD"
    # A real mishearing is not exact, so the visitor is asked.
    (offer,) = did_you_mean("careers after fashion desine", PROGRAMS)
    assert not offer.exact


def faq(question, score, answer="An answer."):
    return {
        "content": f"Q: {question} A: {answer}",
        "vector_score": score,
        "citation": {"title": "FAQ", "heading_path": ""},
    }


def test_a_typo_finds_the_colleges_own_question():
    items = [
        faq("What is the COMEDK code for Ramaiah University?", 0.64),
        {
            "content": "Course code table",
            "vector_score": 0.62,
            "citation": {"title": "X"},
        },
        faq(
            "What is the PGCET code for engineering programs?", 0.52
        ),  # below the floor
    ]
    assert related_questions("what is the comdk code", items, {"ramaiah"}) == [
        "What is the COMEDK code for Ramaiah University?"
    ]


def test_a_generic_word_alone_is_not_related():
    items = [faq("Is fee payment allowed in instalments?", 0.7)]
    assert related_questions("is ragging allowed", items) == []


def test_a_page_section_that_is_a_question_counts_too():
    item = {
        "content": "Candidates with a diploma join in the third semester.",
        "vector_score": 0.6,
        "citation": {
            "title": "B.Tech",
            "heading_path": "Admissions > Is lateral entry possible?",
        },
    }
    assert related_questions("lateral entry", [item]) == ["Is lateral entry possible?"]


def test_the_visitors_own_question_is_not_offered_back():
    assert (
        related_questions(
            "What is the COMEDK code?", [faq("What is the COMEDK code?", 0.9)]
        )
        == []
    )


def test_a_reply_picks_a_suggestion():
    assert choice("yes", 3) == 0
    assert choice("2", 3) == 1
    assert choice("the second one", 3) == 1
    assert choice("option 3", 3) == 2
    assert choice("3", 2) is None  # only two were offered
    assert choice("what is the hostel fee", 3) is None


def test_only_a_real_correction_is_accepted():
    assert (
        corrected("hostle fee for gilrs", "hostel fee for girls")
        == "hostel fee for girls"
    )
    assert corrected("what is the comdk code", '"What is the COMEDK code?"') == (
        "What is the COMEDK code?"
    )
    assert corrected("Is there a hostel?", "Is there a hostel?") is None  # unchanged
    assert (
        corrected(
            "comdk code",
            "COMEDK UGET is an entrance exam held every year in May "
            "for private engineering colleges in Karnataka.",
        )
        is None
    )  # an answer
    assert corrected("x", "   ") is None and corrected("x", None) is None


def test_the_widgets_programme_prefix_is_dropped_for_the_search():
    progs = [{"name": "B. Tech. in Aerospace Engineering"}]
    assert strip_focus(
        "B. Tech. in Aerospace Engineering: what is the comdk code", progs
    ) == ("what is the comdk code")
    assert strip_focus("Note: what is the fee", progs) == "Note: what is the fee"
