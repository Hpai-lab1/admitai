"""An approved curated answer given word for word when it is clearly what was asked
(direct.curated_answer). Seen live, 19 Nov 2026: the model rewrote the approved "No, admissions
open around April each year" as "RUAS accepts admissions throughout the year [1]"."""

from assistant_api.checks import Source
from assistant_api.direct import curated_answer

NO_ANSWER = (
    "Q: Does Ramaiah University accept admissions throughout the year?\n"
    "A: No, admissions open around April each year, with applications starting in December "
    "and selection based on merit."
)


def src(n, text, kind, rerank):
    return Source(n, text, {"source_type": kind, "title": "t", "rerank_score": rerank})


def test_a_clear_curated_match_is_given_word_for_word_and_keeps_its_no():
    sources = [
        src(1, NO_ANSWER, "curated_qa", 6.6),
        src(2, "Admission process page", "document", 1.0),
    ]
    parts, top = curated_answer(sources, 4.0)
    assert top.n == 1
    assert parts[0].startswith("No, admissions open around April each year")
    assert all(p.endswith("[1]") for p in parts)


def test_a_weak_match_or_a_document_on_top_goes_to_the_model():
    assert curated_answer([src(1, NO_ANSWER, "curated_qa", 3.9)], 4.0) is None
    on_top = [src(1, NO_ANSWER, "curated_qa", 5.0), src(2, "A page", "document", 7.0)]
    assert curated_answer(on_top, 4.0) is None


def test_catalogue_sources_do_not_decide_the_top_result():
    sources = [
        src(1, "Programme catalogue 2026-27: B.Tech fee 4,00,000", "catalogue", None),
        src(2, NO_ANSWER, "curated_qa", 6.6),
    ]
    parts, top = curated_answer(sources, 4.0)
    assert top.n == 2 and all(p.endswith("[2]") for p in parts)


def test_no_scores_means_no_direct_answer():
    # an Indian-language query is not reranked: the model answers (and its reply is translated)
    assert curated_answer([src(1, NO_ANSWER, "curated_qa", None)], 4.0) is None


def test_the_application_fee_is_never_answered_with_a_course_fee():
    """Seen 19 Nov 2026: "ಅರ್ಜಿ ಶುಲ್ಕ ಎಷ್ಟು?" got the B.Tech Civil course fee."""
    from assistant_api.direct import NOT_A_PROGRAMME_FEE as NEEDS_JUDGEMENT

    for q in [
        "ಅರ್ಜಿ ಶುಲ್ಕ ಎಷ್ಟು?",
        "आवेदन शुल्क कितना है?",
        "What is the application fee?",
        "What is the RUASAT registration fee?",
        "application fee kitna hai",
    ]:
        assert NEEDS_JUDGEMENT.search(q), q
    assert not NEEDS_JUDGEMENT.search("What is the fee for B.Tech CSE?")


def test_a_semester_in_roman_numerals_supports_an_answer_in_digits():
    """Seen 19 Nov 2026: "In Semester 5, students study ..." was blocked because the page says
    "Semester V"."""
    from decimal import Decimal

    from assistant_api.checks import numbers_in

    assert Decimal(5) in numbers_in("Semester V Course Credits Indian Economics 4")
    assert Decimal(8) in numbers_in("Sem - VIII: Project Work")
    assert numbers_in("The Vice Chancellor visited") == set()  # a word, not a semester


def test_a_chunks_own_heading_supports_its_numbers():
    from decimal import Decimal

    s = Source(
        1,
        "CC13: Indian Economics (3 credits) CC14: International Trade (3 credits)",
        {
            "source_type": "document",
            "title": "t",
            "heading_path": "B.Sc. Economics > Semester 5",
        },
    )
    assert Decimal(5) in s.numbers
    nested = Source(
        2,
        "Pass in 2nd PUC with 45% marks",
        {
            "source_type": "document",
            "title": "t",
            "heading_path": "B.Tech > Semester 8 > Eligibility",
        },
    )
    assert Decimal(8) not in nested.numbers  # only the chunk's own heading counts
