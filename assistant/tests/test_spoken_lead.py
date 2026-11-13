"""Spoken phone numbers (PRD F2.9)."""

import pytest

from assistant_api.spoken_lead import digits_from_speech, read_back, step


@pytest.mark.parametrize(
    "heard",
    [
        "nine eight seven six five four three two one zero",
        "98765 43210",
        "my number for calls is 98765 43210",  # "for" outside the number is not a 4
        "plus 91 98765 43210",
        "zero 98765 43210",
        "नौ आठ सात छह पांच चार तीन दो एक शून्य",
        "९८७६५४३२१०",
    ],
)
def test_numbers_as_people_say_them(heard):
    assert digits_from_speech(heard) == "9876543210"


def test_double_and_rejections():
    assert (
        digits_from_speech("double nine eight seven six five four three two one")
        == "9987654321"
    )
    assert digits_from_speech("it is 12345") is None
    assert (
        digits_from_speech("one two three four five six seven eight nine zero") is None
    )  # not a mobile
    assert read_back("9876543210") == "9, 8, 7, 6, 5 - 4, 3, 2, 1, 0"


def test_an_abandoned_number_is_forgotten_after_ten_minutes():
    state = {}
    step(state, "call me back", "en", now=0)
    step(state, "98765 43210", "en", now=10)
    assert state["voice_lead"]["number"] == "9876543210"
    reply, action = step(state, "yes", "en", now=10 + 601)
    assert action is None and "voice_lead" not in state  # too late: nothing is saved
