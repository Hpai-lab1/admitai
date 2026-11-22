"""Roadmap item 1: the assistant never judges the visitor's own eligibility (checks.py)."""

from assistant_api.checks import ELIGIBILITY_VERDICT, check_sentence
from assistant_api.context import Source

SRC = [
    Source(
        1,
        "Eligibility: Class 12 with Physics, Chemistry and Mathematics, 60% aggregate.",
        {"source_type": "catalogue", "ref_id": "BTCSE", "title": "Program catalogue"},
        [],
    )
]


def reason(sentence):
    return check_sentence(sentence, SRC).reason


def test_verdicts_about_the_visitor_are_blocked_even_when_cited():
    for s in [
        "As you are in class 11, you are not eligible for a seat in our college.",
        "You are not eligible for B.Tech [1].",
        "You're eligible for B.Tech [1].",
        "You cannot apply for this programme [1].",
        "You can't join B.Tech yet.",
        "You do not qualify for admission [1].",
        "You don't meet the eligibility criteria [1].",
        "You qualify for B.Tech [1].",
        "Since you have 55%, you cannot get admission.",
        "You are too young for this programme.",
        "आप पात्र नहीं हैं [1]।",
        "आप आवेदन नहीं कर सकते।",
    ]:
        assert reason(s) == ELIGIBILITY_VERDICT, s


def test_criteria_and_conditions_are_not_verdicts():
    for s in [
        "Candidates need Class 12 with Physics, Chemistry and Mathematics and a 60% aggregate [1].",
        "You are eligible if you have passed Class 12 with PCM and 60% aggregate [1].",
        "You can apply once you have your Class 12 results [1].",
        "Students who have passed Class 12 are eligible [1].",
        "You cannot apply without a Class 12 certificate [1].",
    ]:
        assert reason(s) != ELIGIBILITY_VERDICT, s
