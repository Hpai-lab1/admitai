"""Catalogue questions answered from the catalogue without the model (direct.py)."""

from assistant_api import direct
from assistant_api.context import Source
from assistant_api.router import Route

BTECH = {
    "code": "BTCSE",
    "name": "B.Tech Computer Science and Engineering",
    "academic_year": "2027-28",
    "duration_months": 48,
    "fees": [
        {
            "component": "tuition",
            "category": "general",
            "amount": 250000,
            "currency": "INR",
            "period": "per year",
        },
        {
            "component": "hostel",
            "category": "general",
            "amount": 90000,
            "currency": "INR",
            "period": "per year",
        },
    ],
}
SRC = [
    Source(
        1,
        "B.Tech ... Tuition fee (general category): ₹2,50,000 per year.",
        {
            "source_type": "catalogue",
            "ref_id": "BTCSE",
            "title": "Program catalogue 2027-28",
        },
        [BTECH],
    )
]
ONE = [Route("fees", "admission", 0.9, "q")]


def ask(q, programs=(BTECH,), routes=ONE, language="en"):
    return direct.answer(q, language, routes, list(programs), SRC)


def test_plain_fee_and_duration_questions_are_answered_from_the_catalogue():
    assert ask("What is the hostel fee for B.Tech CSE?") == (
        "For B.Tech Computer Science and Engineering (2027-28), the hostel fee is ₹90,000 per year [1]."
    )
    both = ask("How much is B.Tech CSE?")
    assert (
        "tuition fee is ₹2,50,000 per year; the hostel fee is ₹90,000 per year [1]."
        in both
    )
    assert ask("and what about its hostel fee?").endswith(
        "hostel fee is ₹90,000 per year [1]."
    )
    assert ask("How long is the B.Tech course?") == (
        "B.Tech Computer Science and Engineering (2027-28) lasts 4 years (48 months) [1]."
    )


def test_anything_needing_judgement_goes_to_the_model():
    for q in [
        "Is there a scholarship on the B.Tech fee?",
        "Does the hostel fee include food?",
        "What is the fee for the OBC category?",
        "Can I pay the fee in instalments?",
        "What is the fee and the duration?",
        "Is the B.Tech or M.Tech fee higher?",
        "What is the total fee for four years?",
        "Tell me about the campus",  # not a fee or duration question
    ]:
        assert ask(q) is None, q
    assert ask("What is the fee?", programs=()) is None  # no programme named
    assert ask("What is the fee?", programs=(BTECH, BTECH)) is None  # two programmes
    assert ask("What is the fee?", routes=ONE * 2) is None  # a multi-part question
    assert ask("फीस कितनी है?", language="xx") is None  # a language with no templates
    no_general = {
        **BTECH,
        "fees": [
            {**BTECH["fees"][0], "category": "nri", "amount": 500000},
            {**BTECH["fees"][0], "category": "obc", "amount": 200000},
        ],
    }
    assert ask("What is the tuition fee?", programs=(no_general,)) is None


def test_several_categories_give_the_general_fee_and_say_others_differ():
    two = {
        **BTECH,
        "fees": [
            {**BTECH["fees"][0]},
            {**BTECH["fees"][0], "category": "obc", "amount": 200000},
        ],
    }
    assert ask("What is the tuition fee for B.Tech CSE?", programs=(two,)) == (
        "For B.Tech Computer Science and Engineering (2027-28), the tuition fee (general category)"
        " is ₹2,50,000 per year; other categories differ [1]."
    )


# ------------------------------------------------------------------ Kannada, shared fees, dates
CALENDAR = Source(
    2,
    "Admission calendar 2027-28. Applications open: 15 January 2027. Last date to apply: 30 June 2027.",
    {
        "source_type": "catalogue",
        "ref_id": "calendar",
        "title": "Admission calendar 2027-28",
    },
    [BTECH],
)
FEES = Source(
    3,
    "Fees by program. The hostel fee (general) is ₹90,000 per year for every program.",
    {
        "source_type": "catalogue",
        "ref_id": "fees",
        "title": "Program catalogue 2027-28: fees",
    },
    [BTECH],
)
MTECH = {**BTECH, "code": "MTCSE", "name": "M.Tech Computer Science and Engineering", "fees": [
    {"component": "tuition", "category": "general", "amount": 180000, "currency": "INR", "period": "per year"},
    {"component": "hostel", "category": "general", "amount": 90000, "currency": "INR", "period": "per year"},
]}  # fmt: skip
CATALOGUE = {
    "items": [BTECH, MTECH],
    "general_dates": [
        {
            "label": "Applications open",
            "date": "2027-01-15",
            "academic_year": "2027-28",
        },
        {
            "label": "Last date to apply",
            "date": "2027-06-30",
            "academic_year": "2027-28",
        },
    ],
}
DATES = [Route("dates", "admission", 0.9, "q")]


def ask2(q, programs=(), routes=ONE, language="en"):
    return direct.answer(
        q, language, routes, list(programs), [*SRC, CALENDAR, FEES], CATALOGUE
    )


def test_kannada_fee_and_duration_answers_use_the_catalogue_numbers():
    assert ask("ಬಿ.ಟೆಕ್ ಸಿಎಸ್‌ಇ ಹಾಸ್ಟೆಲ್ ಶುಲ್ಕ ಎಷ್ಟು?", language="kn") == (
        "B.Tech Computer Science and Engineering (2027-28): ಹಾಸ್ಟೆಲ್ ಶುಲ್ಕ ಪ್ರತಿ ವರ್ಷಕ್ಕೆ ₹90,000 [1]."
    )
    both = ask("ಬಿ.ಟೆಕ್ ಸಿಎಸ್‌ಇ ಶುಲ್ಕ ಎಷ್ಟು?", language="kn")
    assert "ಬೋಧನಾ ಶುಲ್ಕ ಪ್ರತಿ ವರ್ಷಕ್ಕೆ ₹2,50,000" in both and "₹90,000" in both
    assert ask("ಬಿ.ಟೆಕ್ ಕೋರ್ಸ್‌ನ ಅವಧಿ ಎಷ್ಟು?", language="kn") == (
        "B.Tech Computer Science and Engineering (2027-28) ಕೋರ್ಸ್‌ನ ಅವಧಿ 48 ತಿಂಗಳು [1]."
    )


def test_kannada_questions_needing_judgement_go_to_the_model():
    for q in [
        "ಬಿ.ಟೆಕ್ ಶುಲ್ಕಕ್ಕೆ ವಿದ್ಯಾರ್ಥಿವೇತನ ಇದೆಯೆ?",  # scholarship
        "ಹಾಸ್ಟೆಲ್ ಶುಲ್ಕದಲ್ಲಿ ಊಟ ಸೇರಿದೆಯೆ?",  # does it include food
        "ಶುಲ್ಕವನ್ನು ಕಂತುಗಳಲ್ಲಿ ಕಟ್ಟಬಹುದೆ?",  # instalments
        "ಬಿ.ಟೆಕ್ ಮತ್ತು ಎಂ.ಟೆಕ್ ಶುಲ್ಕ ಎಷ್ಟು?",  # two things
    ]:
        assert ask(q, language="kn") is None, q


def test_a_fee_every_programme_shares_is_answered_without_naming_one():
    assert ask2("What is the hostel fee?") == (
        "For every programme, the hostel fee is ₹90,000 per year [3]."
    )
    assert ask2("ಹಾಸ್ಟೆಲ್ ಶುಲ್ಕ ಎಷ್ಟು?", language="kn") == (
        "ಎಲ್ಲ ಕೋರ್ಸ್‌ಗಳಿಗೂ ಹಾಸ್ಟೆಲ್ ಶುಲ್ಕ ಪ್ರತಿ ವರ್ಷಕ್ಕೆ ₹90,000 [3]."
    )
    assert (
        ask2("What is the tuition fee?") is None
    )  # differs by programme: ask which one


def test_admission_dates_in_english_and_kannada():
    assert ask2("What is the last date to apply?", routes=DATES) == (
        "The last date to apply is 30 June 2027 [2]."
    )
    assert ask2("ಅರ್ಜಿ ಸಲ್ಲಿಸಲು ಕೊನೆಯ ದಿನಾಂಕ ಯಾವುದು?", routes=DATES, language="kn") == (
        "ಅರ್ಜಿ ಸಲ್ಲಿಸಲು ಕೊನೆಯ ದಿನಾಂಕ 30 June 2027 [2]."
    )
    assert ask2("When do applications open?", routes=DATES) == (
        "Applications open on 15 January 2027 [2]."
    )
    # "Open" about something else, or a date routed elsewhere, is the model's call.
    assert ask2("When does the hostel open?", routes=DATES) is None
    assert ask2("When does the hostel open?", routes=ONE) is None


def test_kannada_written_in_english_letters_by_the_speech_model():
    """Whisper base heard "ಅರ್ಜಿ ಸಲ್ಲಿಸಲು ಕೊನೆಯ ದಿನಾಂಕ ಯಾವುದು" as these English letters."""
    assert ask2(
        "Arji Salislu Kone Yadinaam Ki Yavudhu", routes=DATES, language="kn"
    ) == ("ಅರ್ಜಿ ಸಲ್ಲಿಸಲು ಕೊನೆಯ ದಿನಾಂಕ 30 June 2027 [2].")
    assert ask2("hostel shulka eshtu", language="kn") == (
        "ಎಲ್ಲ ಕೋರ್ಸ್‌ಗಳಿಗೂ ಹಾಸ್ಟೆಲ್ ಶುಲ್ಕ ಪ್ರತಿ ವರ್ಷಕ್ಕೆ ₹90,000 [3]."
    )


def test_hindi_fee_duration_and_date_answers_use_the_catalogue_numbers():
    assert ask("बी.टेक सीएसई की हॉस्टल फीस कितनी है?", language="hi") == (
        "B.Tech Computer Science and Engineering (2027-28): हॉस्टल फीस प्रति वर्ष ₹90,000 [1]."
    )
    assert ask("बी.टेक की अवधि कितनी है?", language="hi") == (
        "B.Tech Computer Science and Engineering (2027-28) की अवधि 48 महीने है [1]."
    )
    assert ask2("हॉस्टल की फीस कितनी है?", language="hi") == (
        "सभी कोर्सों के लिए हॉस्टल फीस प्रति वर्ष ₹90,000 [3]."
    )
    assert ask2("आवेदन की अंतिम तिथि क्या है?", routes=DATES, language="hi") == (
        "आवेदन की अंतिम तिथि 30 June 2027 है [2]."
    )
    assert ask("क्या बी.टेक की फीस में छात्रवृत्ति है?", language="hi") is None  # judgement


def test_a_programme_name_with_and_in_it_is_one_question_not_two():
    # "Computer Science and Engineering" went to the model (3-5 s) as if it asked two things.
    a = ask("What is the hostel fee for B.Tech Computer Science and Engineering?")
    assert a and "₹90,000" in a
    a = ask(
        "What is the tuition fee for B.Tech Computer Science and Engineering (general category)?"
    )
    assert a and "₹2,50,000" in a
    # a real second question, or another category, still goes to the model
    assert ask("What is the hostel fee and is there a mess?") is None
    assert ask("What is the tuition fee for the NRI category?") is None
