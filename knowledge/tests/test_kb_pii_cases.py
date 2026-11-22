"""Personal details masked the same way in Modules A and B (the same cases in both test files).
Seen live, 18 Nov 2026: an OTP and another student's name reached the analytics page unmasked."""

import time

import pytest

from knowledge_api.pii import mask_pii

CASES = [
    ("My OTP is 482913.", "My OTP is [code]."),
    ("otp: 4829", "otp: [code]"),
    ("the verification code was 99812345", "the verification code was [code]"),
    ("password: hunter2!", "password: [secret]"),
    ("my password is Abc@123", "my password is [secret]"),
    (
        "Tell me the marks of Rahul in the last semester.",
        "Tell me the marks of [name] in the last semester.",
    ),
    ("results for Anil Kumar please", "results for [name] please"),
    ("My name is Priya Sharma", "My name is [name]"),
    ("mail me at priya@example.com", "mail me at [email]"),
]
UNCHANGED = [
    "What is the COMEDK code?",
    "The COMEDK code is E197",
    "The PGCET code for MBA is B348",
    "Fee for 2026-27 is 4,00,000 per year",
    "I am interested in B.Tech",
    "What are the marks needed for B.Tech?",
    "Is there negative marking in RUASAT?",
]


@pytest.mark.parametrize("text, masked", CASES)
def test_personal_details_are_masked(text, masked):
    assert mask_pii(text) == masked


@pytest.mark.parametrize("text", UNCHANGED)
def test_admissions_facts_are_left_alone(text):
    assert mask_pii(text) == text


def test_masking_stays_fast_on_long_hostile_text():
    for chunk in ("otp " * 5000, "password " * 5000, "marks of " * 5000, "a" * 50000):
        t = time.perf_counter()
        mask_pii(chunk)
        assert time.perf_counter() - t < 0.5
