"""A programme named by its degree and subject, without its generic words (suggest.named_by_subject):
"B.Tech in Aerospace" for "B. Tech. in Aerospace Engineering". Seen live on 17 Nov 2026: the
spoken question (no programme prefix from the widget) got no answer, the typed one did."""

from assistant_api.router import route
from assistant_api.suggest import named_by_subject


def prog(code, name):
    return {"code": code, "name": name, "aliases": [], "department": None}


CATALOGUE = [
    prog("BT-AERO", "B. Tech. in Aerospace Engineering"),
    prog("MT-AERO", "M.Tech. in Aerospace Engineering"),
    prog("BT-CSE", "B.Tech. in Computer Science and Engineering"),
    prog("BT-AIML", "B.Tech. in Computer Science and Engineering (AI and ML)"),
    prog("BT-MECH", "B.Tech. in Mechanical Engineering"),
    prog("BA-ENG", "B.A. in English"),
    prog("MBA", "Master's of Business Administration (MBA)"),
]


def named(q):
    p = named_by_subject(q, CATALOGUE)
    return p["code"] if p else None


def test_a_programme_is_named_without_its_generic_words():
    assert named("What is the fee for B.Tech in Aerospace?") == "BT-AERO"
    assert named("BTech mechanical fee") == "BT-MECH"
    assert named("fee for B Tech computer science") == "BT-CSE"


def test_the_degree_said_decides_between_bachelor_and_master():
    assert named("MTech aerospace fees") == "MT-AERO"
    assert named("M.Tech in Aerospace") == "MT-AERO"


def test_the_most_specific_programme_wins():
    assert named("B.Tech computer science AI ML fee") == "BT-AIML"


def test_no_degree_or_no_subject_or_a_question_about_a_subject_names_nothing():
    assert (
        named("What is the fee for aerospace?") is None
    )  # B.Tech or M.Tech: ask instead
    assert named("B.Tech fees") is None
    assert named("Is the course taught in English?") is None  # not B.A. in English
    assert named("Is there a hostel?") is None


def test_routing_uses_it_when_no_name_matches():
    decision = route("What is the fee for B.Tech in Aerospace?", "en", [], CATALOGUE)
    assert [p["code"] for p in decision.programs] == ["BT-AERO"]
    # A follow-up with no programme named still keeps the previous one.
    follow = route("and the hostel fee?", "en", [], CATALOGUE, [CATALOGUE[0]])
    assert [p["code"] for p in follow.programs] == ["BT-AERO"]


def test_a_programme_is_named_by_its_initials():
    """Live, 17 Nov 2026: "B.Tech CSE fees" matched no programme at RUAS (full names only)."""
    catalogue = CATALOGUE + [
        prog("BT-ECE", "B.Tech. in Electronics and Communication Engineering")
    ]
    assert (
        named_by_subject("How much is the B.Tech CSE fees?", catalogue)["code"]
        == "BT-CSE"
    )
    assert named_by_subject("BTech ECE fee", catalogue)["code"] == "BT-ECE"
    assert named_by_subject("CSE fees", catalogue) is None  # no degree said


def test_the_and_in_a_programme_name_is_not_a_second_question():
    """Live, 17 Nov 2026: "fee for B.Tech computer science and engineering" (no "in", no dots)
    was taken for two questions and went to the model instead of the catalogue."""
    from assistant_api.direct import _TWO_THINGS, _plain

    cse = prog("BT-CSE", "B.Tech. in Computer Science and Engineering")
    q = "What is the fee for B.Tech computer science and engineering?"
    assert not _TWO_THINGS.search(_plain(q, [cse]).lstrip().lower())
    # A real second question is still two.
    q2 = "What is the fee for B.Tech computer science and is there a hostel?"
    assert _TWO_THINGS.search(_plain(q2, [cse]).lstrip().lower())
