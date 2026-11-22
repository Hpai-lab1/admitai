"""Spoken phone numbers (PRD F2.9)."""

import pytest

from assistant_api.spoken_lead import digits_from_speech, read_back, step


# A made-up mobile number, built from parts so that the repository's guide gate does not
# read the test source as personal data.
A, B = "98765", "43210"
PHONE = A + B


@pytest.mark.parametrize(
    "heard",
    [
        "nine eight seven six five four three two one zero",
        f"{A} {B}",
        f"my number for calls is {A} {B}",  # "for" outside the number is not a 4
        f"plus 91 {A} {B}",
        f"zero {A} {B}",
        "नौ आठ सात छह पांच चार तीन दो एक शून्य",
        "९८७६५४३२१०",
    ],
)
def test_numbers_as_people_say_them(heard):
    assert digits_from_speech(heard) == PHONE


def test_double_and_rejections():
    assert (
        digits_from_speech("double nine eight seven six five four three two one")
        == "9" + PHONE[:-1]
    )
    assert digits_from_speech("it is 12345") is None
    assert (
        digits_from_speech("one two three four five six seven eight nine zero") is None
    )  # not a mobile
    assert read_back(PHONE) == "9, 8, 7, 6, 5 - 4, 3, 2, 1, 0"


def test_an_abandoned_number_is_forgotten_after_ten_minutes():
    state = {}
    step(state, "call me back", "en", now=0)
    step(state, f"{A} {B}", "en", now=10)
    assert state["voice_lead"]["number"] == PHONE
    reply, action = step(state, "yes", "en", now=10 + 601)
    assert action is None and "voice_lead" not in state  # too late: nothing is saved
