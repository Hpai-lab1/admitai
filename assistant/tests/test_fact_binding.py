"""Label binding in the sentence checks (PRD F1.4a): a number must be the same fact in its source.

Generated from the synthetic demo-college catalogue (tests/fixtures/demo_college.json) with the
engine's own context builder, so the sources look exactly as they do in a real turn. Every true
fact, in several phrasings, must pass; every swap a small model makes when it mixes up catalogue
rows (another fee component, category, programme, date event or month) must be blocked.
"""

import itertools
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant_api.checks import check_sentence, dates_in  # noqa: E402
from assistant_api.context import build_sources  # noqa: E402
from assistant_api.router import Route, RouteDecision, match_programs  # noqa: E402

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "demo_college.json").read_text()
)
CATALOGUE = FIXTURE["catalogue"]
PROGRAMS = CATALOGUE["items"]


def indian(n: int) -> str:
    """250000 -> '2,50,000' (Indian digit grouping)."""
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return ",".join([head, *groups, tail]) if head else ",".join([*groups, tail])


def context(programs: list[dict], intents=("fees", "dates", "programs")):
    decision = RouteDecision(
        [Route(i, "admission", 0.9, "q") for i in intents], "en", programs=programs
    )
    return build_sources(decision, CATALOGUE, [], max_sources=5)


def cites(sources) -> str:
    return "".join(f"[{s.n}]" for s in sources)


def fee_sentences(p: dict, fee: dict, amount: int) -> list[str]:
    c, cat, per = fee["component"], fee["category"], fee["period"]
    alias = (p["aliases"] or [p["name"]])[0]
    out = [
        f"The {c} fee for {p['name']} is Rs {indian(amount)} {per}",
        f"For {alias}, the {cat} category {c} fee is {amount} INR {per}",
        f"You pay ₹{indian(amount)} {per} as the {c} fee for {alias}",
    ]
    if amount % 100000 == 0 or amount % 10000 == 0:
        out.append(f"The {c} fee for {alias} is {amount / 100000:g} lakh rupees {per}")
    return out


def true_sentences():
    for p in PROGRAMS:
        for fee in p["fees"]:
            for s in fee_sentences(p, fee, fee["amount"]):
                yield p, s
        yield p, f"{p['name']} has {p['intake']} seats"
        yield p, f"{p['name']} lasts {p['duration_months']} months"
        for d in p["dates"]:
            day = date.fromisoformat(d["date"])
            yield p, f"{d['label']} is on {day.day} {day.strftime('%B')} {day.year}"
            yield p, f"{d['label']}: {day.strftime('%B')} {day.day}, {day.year}"


def swapped_sentences():
    """(program, sentence, what was swapped). Only swaps whose wrong value is a real number
    somewhere in the context, i.e. the ones a presence check alone lets through."""
    for p in PROGRAMS:
        by = {(f["component"], f["category"]): f["amount"] for f in p["fees"]}
        for (c1, k1), (c2, k2) in itertools.permutations(by, 2):
            a1, a2 = by[(c1, k1)], by[(c2, k2)]
            if a1 == a2:
                continue
            fee = {"component": c1, "category": k1, "period": "per year"}
            what = "category" if c1 == c2 else "component"
            yield (
                p,
                f"The {k1} category {c1} fee for {p['name']} is Rs {indian(a2)} per year",
                what,
            )
            del fee
        yield p, f"{p['name']} has {p['duration_months']} seats", "intake vs duration"
        labels = [(d["label"], date.fromisoformat(d["date"])) for d in p["dates"]]
        for (l1, d1), (_l2, d2) in itertools.permutations(labels, 2):
            if (d1.month, d1.day) != (d2.month, d2.day):
                yield (
                    p,
                    f"{l1} is on {d2.day} {d2.strftime('%B')} {d2.year}",
                    "date event",
                )
        for label, d1 in labels:
            wrong_month = date(d1.year, d1.month % 12 + 1, min(d1.day, 28))
            yield (
                p,
                f"{label} is on {d1.day} {wrong_month.strftime('%B')} {d1.year}",
                "month",
            )


TRUE = list(true_sentences())
SWAPS = list(swapped_sentences())


def test_the_generated_sets_are_large_enough_to_mean_something():
    assert len(TRUE) >= 100 and len(SWAPS) >= 60


def test_every_true_catalogue_fact_passes_in_every_phrasing():
    blocked = []
    for p, s in TRUE:
        sources = context([p])
        r = check_sentence(f"{s} {cites(sources)}.", sources)
        if not r.ok:
            blocked.append((s, r.reason, r.unsupported))
    assert blocked == []


def test_every_swap_of_real_catalogue_values_is_blocked():
    passed = []
    for p, s, what in SWAPS:
        sources = context([p])
        r = check_sentence(f"{s} {cites(sources)}.", sources)
        if r.ok:
            passed.append((what, s))
    assert passed == []


@pytest.mark.parametrize(
    "asked,quoted", [("BTECH-CSE", "BTECH-ME"), ("BTECH-ME", "BTECH-CE")]
)
def test_another_programmes_fee_is_blocked_when_both_are_in_the_context(asked, quoted):
    """'Compare CSE and Mechanical' puts both catalogue entries in the context: the presence check
    finds every amount, so only the programme label can catch a swapped fee."""
    a = next(p for p in PROGRAMS if p["code"] == asked)
    q = next(p for p in PROGRAMS if p["code"] == quoted)
    sources = context([a, q])
    wrong = next(
        f["amount"]
        for f in q["fees"]
        if (f["component"], f["category"]) == ("tuition", "general")
    )
    right = next(
        f["amount"]
        for f in a["fees"]
        if (f["component"], f["category"]) == ("tuition", "general")
    )
    assert wrong != right
    bad = check_sentence(
        f"The tuition fee for {a['name']} is Rs {indian(wrong)} per year {cites(sources)}.",
        sources,
    )
    good = check_sentence(
        f"The tuition fee for {a['name']} is Rs {indian(right)} per year {cites(sources)}.",
        sources,
    )
    assert not bad.ok and bad.reason == "number does not match its label"
    assert good.ok


def test_comparing_two_programmes_in_one_sentence_passes_and_a_swap_does_not():
    cse, me = (
        next(p for p in PROGRAMS if p["code"] == c) for c in ("BTECH-CSE", "BTECH-ME")
    )
    sources = context([cse, me])
    fee = {
        p["code"]: next(
            f["amount"]
            for f in p["fees"]
            if f["component"] == "tuition" and f["category"] == "general"
        )
        for p in (cse, me)
    }
    ok = f"B.Tech CSE costs {indian(fee['BTECH-CSE'])} a year and B.Tech Mechanical costs {indian(fee['BTECH-ME'])} a year {cites(sources)}."
    swapped = f"B.Tech CSE costs {indian(fee['BTECH-ME'])} a year and B.Tech Mechanical costs {indian(fee['BTECH-CSE'])} a year {cites(sources)}."
    assert check_sentence(ok, sources).ok
    assert not check_sentence(swapped, sources).ok


def test_unlabelled_numbers_are_checked_for_presence_only():
    """No label words near the number: nothing to bind, the old presence check still applies."""
    cse = next(p for p in PROGRAMS if p["code"] == "BTECH-CSE")
    sources = context([cse])
    assert check_sentence(f"It is 2,50,000 a year {cites(sources)}.", sources).ok
    assert not check_sentence(f"It is 2,60,000 a year {cites(sources)}.", sources).ok


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Apply by 30 June 2027.", {(2027, 6, 30)}),
        ("Apply by 30th June.", {(None, 6, 30)}),
        ("Classes begin on August 16, 2027.", {(2027, 8, 16)}),
        ("The deadline is 2027-06-30.", {(2027, 6, 30)}),
        ("The deadline is 30/06/2027.", {(2027, 6, 30)}),
        ("Fees are 2,50,000 a year.", set()),
        ("Fees for 2027-28.", set()),
    ],
)
def test_dates_are_read_with_their_month(text, expected):
    assert dates_in(text) == expected


def test_a_date_without_a_year_matches_on_day_and_month():
    sources = context([PROGRAMS[0]])
    assert check_sentence(
        f"The last date to apply is 30 June {cites(sources)}.", sources
    ).ok
    assert not check_sentence(
        f"The last date to apply is 30 July {cites(sources)}.", sources
    ).ok


@pytest.mark.parametrize(
    "text,codes",
    [
        ("Can you tell me the hostel fee for CSE?", ["BTECH-CSE"]),
        ("Call me back please", []),
        ("What is the ME fee?", ["BTECH-ME"]),
        ("Is mechanical a good branch?", ["BTECH-ME"]),
    ],
)
def test_short_aliases_that_are_english_words_need_their_own_case(text, codes):
    """'ME' is the Mechanical alias; 'me' in 'tell me' is not Mechanical."""
    assert [p["code"] for p in match_programs(text, PROGRAMS)] == codes


def _mtech_and_all_sources():
    # No programme named in the question: the fee overview for every programme is the source.
    return next(p for p in PROGRAMS if p["code"] == "MTECH-CSE"), context([], ("fees",))


def test_duration_next_to_a_fee_is_not_read_as_the_fee():
    """The model copies the catalogue's "(PG, 24 months)" into a true sentence about a fee."""
    p, sources = _mtech_and_all_sources()
    hostel = next(f["amount"] for f in p["fees"] if f["component"] == "hostel")
    sentence = (
        f"The hostel fee for {p['name']} (PG, 24 months) is {hostel} INR per year [1]."
    )
    assert check_sentence(sentence, sources).ok


def test_fee_swap_after_a_duration_is_still_blocked():
    p, sources = _mtech_and_all_sources()
    tuition = next(f["amount"] for f in p["fees"] if f["component"] == "tuition")
    sentence = (
        f"The hostel fee for {p['name']} (PG, 24 months) is {tuition} INR per year [1]."
    )
    assert not check_sentence(sentence, sources).ok


def test_wrong_duration_is_still_blocked():
    p = next(p for p in PROGRAMS if p["code"] == "MBA")
    assert p["duration_months"] != 48
    sentence = f"{p['name']} lasts 48 months [1]."
    assert not check_sentence(sentence, context([], ("fees",))).ok


def test_the_longest_programme_name_wins():
    codes = lambda text: [p["code"] for p in match_programs(text, PROGRAMS)]  # noqa: E731
    assert codes("M.Tech Computer Science and Engineering") == ["MTECH-CSE"]
    assert codes("M.Tech Computer Science") == ["MTECH-CSE"]
    assert codes("Computer Science") == ["BTECH-CSE"]
    assert codes("How long is the M.Tech course?") == ["MTECH-CSE"]
    # Naming both programmes still finds both.
    assert codes("M.Tech CSE or B.Tech Computer Science and Engineering") == [
        "BTECH-CSE",
        "MTECH-CSE",
    ]


def test_another_programmes_duration_is_blocked():
    sources = context([], ("fees",))
    name = "M.Tech Computer Science and Engineering"
    assert check_sentence(f"{name} lasts 24 months [1].", sources).ok
    assert not check_sentence(f"{name} lasts 48 months [1].", sources).ok


def test_a_fee_named_after_a_term_or_seat_is_still_checked_as_a_fee():
    p, sources = _mtech_and_all_sources()
    tuition = next(f["amount"] for f in p["fees"] if f["component"] == "tuition")
    for sentence in (
        f"The hostel fee for {p['name']} is a {tuition} semester fee [1].",
        f"The hostel fee for {p['name']} is a {tuition} seat deposit [1].",
    ):
        assert not check_sentence(sentence, sources).ok
