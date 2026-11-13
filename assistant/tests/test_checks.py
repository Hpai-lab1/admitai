"""Sentence checks (PRD F1.4a) and the PRD 20 acceptance test: injected wrong numbers are blocked."""

import random
import sys
import time
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant_api.checks import (  # noqa: E402
    Source,
    check_sentence,
    numbers_in,
    split_sentences,
)

CAT = Source(
    1,
    "B.Tech Computer Science and Engineering. Tuition (general): 250000 INR per year. "
    "Tuition (management): 412500 INR per year. Hostel: 90000 INR per year. "
    "Duration: 48 months. Intake: 120. Last date to apply: 2027-06-30. Academic year 2027-28.",
    {"source_type": "catalogue", "title": "Program catalogue 2027-28"},
)
DOC = Source(
    2,
    "Diploma holders with at least 45% marks can join B.Tech directly in the second year. "
    "Hostel gates close at 21:30. College buses run on 12 routes.",
    {"source_type": "document", "title": "Prospectus 2027-28"},
)
SOURCES = [CAT, DOC]


def D(*xs):
    return {Decimal(str(x)).normalize() for x in xs}


@pytest.mark.parametrize(
    "text,expected",
    [
        ("The fee is Rs 2,50,000 a year.", D(250000)),
        ("It costs ₹2.5 lakh per year.", D(250000)),
        ("Fees are 412500.50 INR.", D("412500.5")),
        ("Apply by 30 June 2027.", D(30, 2027)),
        ("Apply by 2027-06-30.", D(2027, 6, 30)),
        ("For 2027-28 admissions.", D(2027, 2028, 28)),
        ("Gates close at 21:30.", D(2130)),
        ("At least 45% marks [2].", D(45)),
        ("B.Tech has four years.", set()),
    ],
)
def test_numbers_are_normalised(text, expected):
    assert numbers_in(text) == expected


def test_sentence_splitting_keeps_abbreviations_and_trailing_markers():
    buf = "The B.Tech fee is Rs. 250000 per year. [1] Dr. Rao teaches it! Is that ok? Next"
    done, rest = split_sentences(buf)
    assert done == [
        "The B.Tech fee is Rs. 250000 per year. [1]",
        "Dr. Rao teaches it!",
        "Is that ok?",
    ]
    assert rest == " Next" or rest == "Next"
    assert split_sentences("1. Apply online\n2. Pay the fee", final=True)[0] == [
        "1. Apply online",
        "2. Pay the fee",
    ]
    assert split_sentences("हॉस्टल उपलब्ध है। फीस 90000 है।", final=True)[0] == [
        "हॉस्टल उपलब्ध है।",
        "फीस 90000 है।",
    ]


def test_supported_sentence_passes():
    r = check_sentence(
        "The tuition fee for B.Tech CSE is 250000 INR per year [1].", SOURCES
    )
    assert r.ok and r.cited == [1]
    r = check_sentence("The fee is ₹2.5 lakh a year [1].", SOURCES)
    assert r.ok


def test_catalogue_numbers_count_even_when_another_source_is_cited():
    assert check_sentence("Hostel costs 90000 INR a year [2].", SOURCES).ok


def test_number_from_uncited_document_is_blocked():
    r = check_sentence("There are 12 bus routes [1].", SOURCES)
    assert not r.ok and r.reason == "number not in sources" and r.unsupported == ["12"]


def test_missing_citation_is_auto_attributed_only_when_clear():
    r = check_sentence("Hostel gates close at 21:30.", SOURCES)
    assert r.ok and r.auto_cited and r.text.endswith("[2]")
    r = check_sentence(
        "The campus has a beautiful lake and an open air theatre.", SOURCES
    )
    assert not r.ok and r.reason == "no citation"


def test_non_factual_sentences_need_no_citation():
    for s in (
        "Would you like me to connect you with the admissions team?",
        "Is there anything else?",
    ):
        assert check_sentence(s, SOURCES).ok


def test_unknown_citation_number_is_blocked():
    r = check_sentence("Tuition is 250000 INR [7].", SOURCES)
    assert not r.ok and "no source" in r.reason


@pytest.mark.parametrize(
    "s",
    [
        "You will definitely get admission with this rank [1].",
        "Your seat is guaranteed [1].",
        "We offer 100% placement [2].",
        "आपका एडमिशन पक्का है।",
    ],
)
def test_promise_phrases_are_blocked(s):
    r = check_sentence(s, SOURCES)
    assert not r.ok and r.reason == "promise"


def test_fifty_injected_wrong_numbers_are_all_blocked():
    """PRD 20: 50 answers with injected wrong numbers -> 100% blocked."""
    rng = random.Random(7)
    templates = [
        "The tuition fee for B.Tech CSE is {} INR per year [1].",
        "The hostel fee is Rs {} a year [1].",
        "The intake is {} students [1].",
        "Diploma holders need at least {}% marks [2].",
        "The last date to apply is {} June 2027 [1].",
        "The management quota fee is ₹{} per year [1].",
        "Hostel gates close at {}:30 [2].",
        "The programme lasts {} months [1].",
        "Buses run on {} routes [2].",
        "Applications close on 30 June {} [1].",
    ]
    true_values = [250000, 90000, 120, 45, 30, 412500, 21, 48, 12, 2027]
    blocked = 0
    start = time.perf_counter()
    for i in range(50):
        k = i % len(templates)
        wrong = true_values[k]
        while wrong == true_values[k] or Decimal(wrong) in DOC.numbers | CAT.numbers:
            wrong = true_values[k] + rng.choice([-1, 1]) * rng.randint(1, 9) * (
                1 if true_values[k] < 100 else 1000
            )
        blocked += not check_sentence(templates[k].format(wrong), SOURCES).ok
    per_sentence_ms = (time.perf_counter() - start) * 1000 / 50
    assert blocked == 50
    assert per_sentence_ms < 20


def test_citation_must_support_the_sentence():
    r = check_sentence("Hostel gates close at night time [1].", SOURCES)
    assert r.ok and r.auto_cited and r.text == "Hostel gates close at night time. [2]"
    r = check_sentence("The library has many rare manuscripts [2].", SOURCES)
    assert not r.ok and r.reason == "citation does not support"
    assert check_sentence("हॉस्टल के गेट रात में बंद होते हैं [2]।", SOURCES).ok


def test_relevance_gate():
    from assistant_api.checks import relevant

    assert relevant(
        "CSE ki fees kitni hai?", ["Fee - tuition: 250000 INR. Also called: CSE."]
    )
    assert relevant(
        "Which documents do I need?",
        ["Class 10 marks cards. Prospectus Documents required"],
    )
    assert not relevant(
        "Is there a swimming pool?", ["Separate hostels for men and women."]
    )
    assert not relevant(
        "Does RUAS offer scholarships?", ["RUAS offers many programs."], {"ruas"}
    )
    assert relevant("हॉस्टल है?", ["anything"])


def test_indic_sentence_is_attributed_by_its_numbers():
    r = check_sentence("हॉस्टल की फीस 90000 रुपये प्रति वर्ष है।", SOURCES)
    assert r.ok and r.cited == [1]
    assert not check_sentence("हॉस्टल की फीस 95000 रुपये प्रति वर्ष है।", SOURCES).ok


def test_fee_words_alone_do_not_make_a_source_relevant():
    from assistant_api.checks import relevant

    loan = "Q: Does the college help with education loans? A: The office issues the fee structure letter."
    assert not relevant("Can I pay the tuition fee in instalments?", [loan])
    assert relevant(
        "Can I pay the tuition fee in instalments?",
        ["Tuition can be paid in two instalments."],
    )
    assert relevant(
        "What are the fees?", [loan]
    )  # no topic word at all: nothing to test


@pytest.mark.parametrize(
    "sentence",
    [
        "The KCET code for Ramaiah University is not provided in the given sources.",
        "The intake capacity for programs is not specified in the provided sources.",
        "The vice chancellor of the university is not covered in the provided sources.",
        "No specific amount for applying is mentioned in the sources provided.",
        "The AI curriculum is not explicitly mentioned in the provided sources.",
        "The sources do not mention a swimming pool.",
    ],
)
def test_refusals_in_the_models_own_words(sentence):
    from assistant_api.checks import is_factual, is_refusal

    assert is_refusal(sentence) and not is_factual(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "No, RUAS does not offer scholarships directly.",
        "Fees are not refundable after classes begin.",
        "The hostel is not available for day scholars.",
    ],
)
def test_real_claims_are_not_mistaken_for_refusals(sentence):
    from assistant_api.checks import is_refusal

    assert not is_refusal(sentence)


def test_number_anchored_sentence_is_attributed_with_less_word_overlap():
    r = check_sentence(
        "The hostel fee for first-year students is 90000 INR per year.", SOURCES
    )
    assert r.ok and r.auto_cited and r.cited == [1]
