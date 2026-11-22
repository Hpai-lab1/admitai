"""Patterns run on what visitors type stay fast on crafted input (CodeQL py/polynomial-redos,
Hpai-lab1/admitai PR 17, 22 Nov 2026) and still accept what they did before."""

import time

from assistant_api import language
from assistant_api.suggest import AFFIRMATIVE, choice


def fast(fn, *args):
    start = time.perf_counter()
    fn(*args)
    return time.perf_counter() - start < 0.5


def test_yes_still_matches_with_its_courtesies():
    for t in [
        "yes",
        "Yes please.",
        "ok",
        "okay!",
        "haan ji",
        "ಹೌದು",
        "yeah, it is",
        "ya i did",
    ]:
        assert AFFIRMATIVE.match(t), t
    for t in ["yes and the fees?", "yesterday", "no"]:
        assert not AFFIRMATIVE.match(t), t
    assert choice("yes please", 3) == 0


def test_yes_pattern_is_fast_on_many_tabs():
    assert fast(AFFIRMATIVE.match, "ya" + "\t" * 50_000 + "x")


def test_question_cleanup_is_fast_on_many_spaces_and_keeps_marks_tight():
    assert fast(language.preferences, "fees" + " " * 50_000 + "x reply in Hindi")
    assert (
        language.preferences("What is the fee  ?  reply in Hindi").question
        == "What is the fee?"
    )
