"""Regexes that read visitor text stay linear (CodeQL py/polynomial-redos) and match as before."""

import random
import re
import time

from assistant_api import guard, router

# The patterns before the fix: the reference for "the same matches".
OLD_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
OLD_SPLIT = re.compile(
    r"\?\s+|\s+(?:and also|also|and|plus|aur|और)\s+(?=(?:what|how|is|are|can|do|does|which|when|"
    r"where|will|tell|kya|क्या)\b)",
    re.IGNORECASE,
)
PIECES = [
    "a",
    "b.",
    "x_y",
    "+",
    "-",
    "@",
    "mail",
    ".com",
    ".in",
    " ",
    "  ",
    "?",
    " and ",
    " also ",
    " plus ",
    " aur ",
    "what",
    "how",
    "is",
    "kya",
    "9",
    "98765",
    "\t",
    "\n",
    "co.",
    "@x",
]


def random_text(rng):
    return "".join(rng.choice(PIECES) for _ in range(rng.randint(0, 40)))


LONG_RUN = re.compile(r"[\w.+-]{65,}")


def test_the_new_patterns_find_exactly_what_the_old_ones_found():
    rng = random.Random(2026)
    checked = 0
    for _ in range(20000):
        s = random_text(rng)
        # extra spaces may now end a part; parts are trimmed before use
        assert [p.strip() for p in router._SPLIT.split(s)] == [
            p.strip() for p in OLD_SPLIT.split(s)
        ], s
        if not LONG_RUN.search(
            s
        ):  # every address part of a legal length (<= 64 before "@")
            assert guard._EMAIL.findall(s) == OLD_EMAIL.findall(s), s
            checked += 1
    assert checked > 15000


def test_addresses_written_together_are_all_masked():
    # Found by the comparison above: a lookbehind version left the second address visible.
    # Reserved test domains (RFC 2606), as the repository's guide-gate asks for test addresses.
    s = "mail a.b@one.test+c@two.test now"
    assert guard.mask_pii(s) == "mail [email][email] now"


def test_a_hostile_message_cannot_make_them_slow():
    n = 4000  # the longest message the API accepts
    hostile = [
        "9" * n,
        "a" * n,
        " " * n + "x",
        " and" * (n // 4),
        "a.b" * (n // 3),
        "x@" * (n // 2),
    ]
    start = time.perf_counter()
    for s in hostile:
        guard.mask_pii(s)
        router._SPLIT.split(s)
    assert (
        time.perf_counter() - start < 0.2
    )  # quadratic before: about 0.5 s for one of them
