"""Programme matching with a large catalogue: same results as before, without recompiling."""

import re
import time

from assistant_api import router
from assistant_api.router import _WORD_ALIASES, match_programs


def reference(text, programs):
    """The matcher as it was (compiled every name on every call): the behaviour to keep."""
    low = text.lower()
    hits = []
    for i, p in enumerate(programs):
        for name in [p["code"], p["name"], *p.get("aliases", [])]:
            raw = name.strip()
            n = raw.lower()
            if len(n) < 2:
                continue
            if n in _WORD_ALIASES:
                found = re.finditer(rf"(?<![\w.]){re.escape(raw)}(?![\w])", text)
            else:
                found = re.finditer(rf"(?<![\w.]){re.escape(n)}(?![\w])", low)
            hits.extend((m.start(), m.end(), i) for m in found)
    kept = {
        i
        for start, end, i in hits
        if not any(
            other != i and s <= start and end <= e and e - s > end - start
            for s, e, other in hits
        )
    }
    return [p for i, p in enumerate(programs) if i in kept]


BASE = [
    {
        "code": "BTECH-CSE",
        "name": "B.Tech. in Computer Science and Engineering",
        "aliases": [
            "CSE",
            "B.Tech CSE",
            "Computer Science",
            "BTech Computer Science and Engineering",
        ],
    },
    {
        "code": "MTECH-DSE",
        "name": "M.Tech. in Data Science and Engineering",
        "aliases": ["M.Tech Data Science"],
    },
    {
        "code": "BTECH-ME",
        "name": "B.Tech. in Mechanical Engineering",
        "aliases": ["ME", "Mechanical", "Mech"],
    },
    {
        "code": "MBA",
        "name": "Master's of Business Administration (MBA)",
        "aliases": ["MBA"],
    },
    {
        "code": "BCA",
        "name": "Bachelor of Computer Applications (BCA)",
        "aliases": ["BCA"],
    },
    {
        "code": "MCA",
        "name": "Master of Computer Applications (MCA)",
        "aliases": ["MCA"],
    },
]
# a large catalogue (111 programmes, several names each)
BIG = BASE + [
    {
        "code": f"P{i}",
        "name": f"Programme number {i} in Applied Studies",
        "aliases": [f"Prog {i}", f"Applied Studies {i}", f"AS{i}", f"P-{i} course"],
    }
    for i in range(105)
]
TEXTS = [
    "What is the fee for B.Tech CSE?",
    "Tell me the fee for M.Tech Data Science and Engineering",
    "tell me about ME and MBA",
    "Is Mechanical better than Computer Science?",
    "fees for BCA or MCA",
    "what about Prog 7 and Applied Studies 42?",
    "Is there a hostel?",
    "B.Tech. in Computer Science and Engineering seats",
]


def test_same_programmes_as_the_previous_matcher():
    for text in TEXTS:
        for programs in (BASE, BIG):
            assert match_programs(text, programs) == reference(text, programs), text


def test_a_large_catalogue_is_matched_quickly():
    router._MATCHERS.clear()
    match_programs("warm-up", BIG)
    start = time.perf_counter()
    for _ in range(200):  # more calls than one turn makes
        for text in TEXTS:
            match_programs(text, BIG)
    assert (
        time.perf_counter() - start < 2.0
    )  # was ~40 ms a call: 1600 calls took over a minute
