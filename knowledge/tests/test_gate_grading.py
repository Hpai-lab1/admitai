"""A correction that agrees with the catalogue passes the gate, whichever source is cited."""

from knowledge_api import gate

FEE = "The B.Tech CSE tuition fee (general category) is 250000 INR per year."


def reply(text, source_type, ref_id):
    return {
        "text": text,
        "done": {
            "outcome": "answered",
            "sources": [{"source_type": source_type, "ref_id": ref_id}],
        },
    }


def case(ext_id, refs, facts):
    return {
        "ext_id": ext_id,
        "kind": "direct",
        "expected_refs": refs,
        "forbidden_refs": [],
        "expected_facts": facts,
        "expected_role": None,
        "critical": True,
    }


def test_the_catalogue_case_passes_when_a_correction_with_the_same_fee_is_cited():
    fee_case = case("cat-fee", ["catalogue:BTECH-CSE"], ["250000"])
    by_qa = reply(FEE + " [1]", "curated_qa", "qa-1")
    assert gate.grade(fee_case, by_qa, {"qa-1": "proposal-1"})["passed"]
    # a wrong figure still fails, whoever is cited
    wrong = reply("The fee is 90000 INR per year [1].", "curated_qa", "qa-1")
    assert not gate.grade(fee_case, wrong, {"qa-1": "proposal-1"})["passed"]


def test_the_corrections_own_case_passes_when_the_catalogue_is_cited():
    p = {
        "id": "310f8581-0000-0000-0000-000000000000",
        "kind": "curated_answer",
        "diff": {"question": "What is the tuition fee for B.Tech CSE?", "answer": FEE},
        "examples": [],
    }
    [new] = gate.new_cases(p)
    assert new["expected_facts"] == ["250000"]  # not the year
    by_catalogue = reply(FEE + " [1]", "catalogue", "BTECH-CSE")
    assert gate.grade(new, by_catalogue, {})["passed"]
    off = reply("The fee is 200000 INR [1].", "catalogue", "BTECH-CSE")
    assert not gate.grade(new, off, {})["passed"]


def test_a_correction_without_figures_must_still_be_cited():
    p = {
        "id": "aaaa0000-0000-0000-0000-000000000000",
        "kind": "curated_answer",
        "diff": {
            "question": "Is there an ATM?",
            "answer": "Yes, beside the main gate.",
        },
        "examples": [],
    }
    [new] = gate.new_cases(p)
    assert new["expected_refs"] == ["qa:proposal-aaaa0000"]
    doc = reply("Yes, beside the main gate [1].", "catalogue", "BTECH-CSE")
    assert not gate.grade(new, doc, {})["passed"]


def test_a_correction_is_tested_with_its_own_question_not_the_grouped_ones():
    p = {
        "id": "aef15b17-0000-0000-0000-000000000000",
        "kind": "curated_answer",
        "diff": {
            "question": "What is the eligibility for B.Des. Fashion Design?",
            "answer": "Passed 12th from a recognised board with at least 80% of marks.",
        },
        "examples": [
            {"question": "What careers can B.Des. Fashion Design lead to?"},
            {"question": "What is the eligibility for B.Des. Fashion Design?"},
        ],
    }
    [new] = gate.new_cases(p)
    assert new["question"] == "What is the eligibility for B.Des. Fashion Design?"


def test_figures_skip_years_and_repeat_amounts():
    assert gate._figures("In 2027-28 the fee is 2,50,000 (250000) for 120 seats.") == [
        "250000",
        "120",
    ]


def test_a_fee_that_looks_like_a_year_is_still_a_figure_to_check():
    # The RUAS application fee is Rs. 2000: an amount, not the year 2000.
    assert gate._figures("The application fee is ₹2000 per program FY 2026.") == [
        "2000"
    ]
    assert gate._figures("It costs Rs. 2000 per program.") == ["2000"]
    assert gate._figures("It costs 2000 INR per program.") == ["2000"]
    assert gate._figures("Admissions for 2026 open in December.") == []
    p = {
        "id": "f76014f6-0000-0000-0000-000000000000",
        "kind": "curated_answer",
        "diff": {
            "question": "What is the application fee?",
            "answer": "The application fee is ₹2000 per program FY 2026.",
        },
        "examples": [],
    }
    [new] = gate.new_cases(p)
    assert new["expected_facts"] == ["2000"]


def test_citation_markers_are_not_kept_in_a_corrected_answer():
    from knowledge_api.curated import strip_citation_markers

    assert (
        strip_citation_markers("The fee is ₹2000 per program FY 2026 [1].")
        == "The fee is ₹2000 per program FY 2026."
    )
    assert strip_citation_markers("A [1]. B [2, 3].") == "A. B."


def test_the_gate_names_the_questions_that_broke():
    base = {
        "scores": {"accuracy": 0.5},
        "results": [
            {"id": "r01", "passed": True, "critical": False},
            {"id": "r28", "passed": False, "critical": False},
        ],
    }
    cand = {
        "scores": {"accuracy": 3 / 7},
        "results": [
            {"id": "r01", "passed": False, "critical": False},
            {"id": "r28", "passed": False, "critical": False},
            {"id": "proposal-x-0", "passed": True, "critical": False},
        ],
    }
    v = gate.compare(base, cand, 1.0, {"proposal-x-0"})
    assert not v["passed"]
    assert v["reasons"] == [
        "accuracy fell from 50.0% to 42.9%",
        "passed before, fail with the change: r01",
    ]
    # a passing gate gains no reason from this
    assert gate.compare(base, base, 1.0, set())["reasons"] == []


def test_the_one_cited_curated_answer_is_the_one_corrected():
    from knowledge_api.learning import cited_answer

    qa = {"source_type": "curated_qa", "ref_id": "qa-3"}
    doc = {"source_type": "document", "ref_id": "d-1"}
    assert cited_answer([{"sources": [qa, doc]}, {"sources": [qa]}]) == "qa-3"
    other = {"source_type": "curated_qa", "ref_id": "qa-9"}
    assert cited_answer([{"sources": [qa, other]}]) is None  # ambiguous: fall back
    assert cited_answer([{"sources": [doc]}]) is None
