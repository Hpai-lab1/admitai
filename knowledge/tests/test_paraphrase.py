"""Paraphrased questions carry the words the college's pages use (paraphrase.py)."""

from knowledge_api.paraphrase import expand


def test_visitor_words_gain_the_page_words():
    assert "intake" in expand("How many seats are there in CSE?")
    assert "computer science engineering" in expand("How many seats are there in CSE?")
    assert "mathematics" in expand("what % is maths in maths and computing")
    assert "percentage" in expand("what % is maths in maths and computing")
    assert "fee" in expand("how much does btech cost").split()
    assert "eligibility" in expand("Can I join with 42% in PCM?")
    assert "hostel" in expand("is there accommodation on campus")
    assert "placement" in expand("what salary packages do students get")


def test_a_question_keeps_its_own_words_first_and_gains_nothing_twice():
    q = "What is the fee for the hostel?"
    out = expand(q)
    assert out.startswith(q)
    words = [w.strip("?.,") for w in out.split()]
    assert words.count("fee") == 1 and words.count("hostel") == 1


def test_a_question_in_an_indic_script_is_left_alone():
    q = "बी.टेक की फीस कितनी है?"
    assert expand(q) == q


def test_a_question_with_nothing_to_expand_is_unchanged():
    assert expand("Tell me about the university") == "Tell me about the university"
