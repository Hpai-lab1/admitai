"""'Did you mean ...?' and labelled general answers, through the whole engine (engine.py)."""

import uuid
from dataclasses import replace

import psycopg
from fastapi.testclient import TestClient

from conftest import TEST_URL, FakeKnowledge, FakeLLM, hit, needs_db
from test_engine_db import ask, done_of, text_of

pytestmark = needs_db

S1, S2 = str(uuid.uuid4()), str(uuid.uuid4())


def test_a_misheard_programme_gets_did_you_mean_and_yes_asks_it(make_client, tenant):
    # The only answer call is the one after "yes".
    llm = FakeLLM("The B.Tech CSE tuition fee is ₹2,50,000 per year [1].")
    client, _ = make_client(llm)
    _, events = ask(
        client, tenant, "What is the fee for computer signs engineering?", session=S1
    )
    text = text_of(events)
    assert text.startswith("Did you mean")
    assert "B.Tech Computer Science and Engineering" in text
    done = done_of(events)
    assert done["outcome"] == "clarify" and done["offer_handoff"] is False
    assert [c for c in llm.calls if not c.get("router")] == []  # no model call
    # "yes" asks the suggested question, with the programme's real name.
    _, events = ask(client, tenant, "yes", session=S1)
    asked = next(c for c in llm.calls if "messages" in c)["messages"][-1]["content"]
    assert "B.Tech Computer Science and Engineering" in asked
    assert "2,50,000" in text_of(events)
    assert done_of(events)["outcome"] == "answered"
    with psycopg.connect(TEST_URL) as conn:
        conn.execute("select set_config('app.tenant_id', %s, false)", (tenant,))
        said = [
            r[0]
            for r in conn.execute(
                "select content from chat.messages where sender = 'user' order by created_at"
            )
        ]
    assert said[-1] == "yes"  # the visitor's own words are kept


def test_no_to_did_you_mean_asks_again(make_client, tenant):
    client, _ = make_client(FakeLLM())
    ask(client, tenant, "What is the fee for computer signs engineering?", session=S2)
    _, events = ask(client, tenant, "no", session=S2)
    assert "tell me the programme name again" in text_of(events)
    assert done_of(events)["outcome"] == "clarify"


def test_a_question_naming_the_programme_gets_no_suggestion(make_client, tenant):
    client, _ = make_client(FakeLLM("Hostels are separate for men and women [2]."))
    _, events = ask(client, tenant, "Is there a hostel for CSE students?")
    assert "Did you mean" not in text_of(events)


class FashionKnowledge(FakeKnowledge):
    async def programs(self, tenant):
        return {
            "items": [
                {
                    "id": "p9",
                    "code": "BDES-FD",
                    "name": "B.Des. Fashion Design",
                    "aliases": [],
                    "department": "Design",
                    "level": "UG",
                    "duration_months": 48,
                    "intake": 60,
                    "eligibility": "10+2 in any stream",
                    "contact": None,
                    "academic_year": "2027-28",
                    "fees": [],
                    "dates": [],
                }
            ],
            "general_dates": [],
        }


def test_a_correct_name_written_differently_is_used_without_asking(make_client, tenant):
    client, _ = make_client(FakeLLM("NO_ANSWER"), FashionKnowledge())
    _, events = ask(client, tenant, "What careers can B.Des Fashion Design lead to?")
    done = done_of(events)
    assert "Did you mean" not in text_of(events)
    assert done["outcome"] != "clarify"
    assert done["route"]["programs"] == ["BDES-FD"]


class GeneralLLM(FakeLLM):
    """The router gets its usual reply; on Gemini, the spelling fix returns `spelling` (default:
    the question unchanged) and the general answer returns `general`."""

    def __init__(self, general, *replies, spelling=None):
        super().__init__(*replies)
        self.general = general
        self.spelling = spelling

    async def complete(self, model, messages, max_tokens, timeout):
        from assistant_api.prompts import SPELLING_PROMPT

        if model.startswith("gemini/"):
            if isinstance(self.general, Exception):
                raise self.general
            if messages[0]["content"] == SPELLING_PROMPT:
                self.calls.append({"model": model, "spelling": messages})
                return self.spelling or messages[-1]["content"]
            self.calls.append({"model": model, "general": messages})
            return self.general
        return await super().complete(model, messages, max_tokens, timeout)


def _opted_in(settings, tenant, llm, general_answers=True, knowledge=None):
    from assistant_api.engine import Deps
    from assistant_api.guard import RateLimiter
    from assistant_api.main import create_app

    s = replace(settings, gemini_api_key="test-key")
    client = TestClient(
        create_app(
            s,
            Deps(s, knowledge or FakeKnowledge(results=[]), llm, RateLimiter(100, 60)),
        )
    )
    pack = {
        "college": {"name": "Demo College"},
        "model_policy": {"external": "gemini", "general_answers": general_answers},
    }
    r = client.post(
        "/tenant-pack",
        json=pack,
        headers={"X-Service-Key": "key-from-c", "X-Tenant-Id": tenant},
    )
    assert r.status_code == 200, r.text
    return client


def test_nothing_in_the_colleges_information_gives_a_labelled_general_answer(
    settings, tenant
):
    llm = GeneralLLM(
        "COMEDK UGET is an entrance exam for private engineering colleges in Karnataka. "
        "The college code is E123. "
        "You will surely get a seat. "
        "Check the official COMEDK website for the list of participating colleges."
    )
    client = _opted_in(settings, tenant, llm)
    _, events = ask(client, tenant, "What is the COMEDK code?")
    text = text_of(events)
    assert "official information doesn't cover this" in text
    assert "COMEDK UGET is an entrance exam" in text
    assert "official COMEDK website" in text
    # A number (a code) and a promise are never passed on from a general answer.
    assert "E123" not in text and "surely" not in text
    assert "I can ask someone from the admissions team" in text
    done = done_of(events)
    # Still a knowledge gap for Module A, and a person is still offered.
    assert done["outcome"] == "no_answer" and done["offer_handoff"] is True
    assert done["sources"] == []
    sent = next(c for c in llm.calls if "general" in c)
    assert (
        "Never state anything specific to Demo College" in sent["general"][0]["content"]
    )


def test_a_general_no_answer_or_a_gemini_failure_gives_the_usual_reply(
    settings, tenant
):
    from assistant_api.clients import UpstreamError

    for i, general in enumerate(("NO_ANSWER", UpstreamError("Gemini: 429"))):
        client = _opted_in(settings, tenant, GeneralLLM(general))
        _, events = ask(client, tenant, "Who won the cricket match yesterday?", f"g{i}")
        assert "don't have reliable information" in text_of(events)
        assert done_of(events)["outcome"] == "no_answer"


def test_without_the_opt_in_no_general_answer_is_asked_for(settings, tenant):
    llm = GeneralLLM("A general answer.")
    client = _opted_in(settings, tenant, llm, general_answers=False)
    _, events = ask(client, tenant, "What is the COMEDK code?")
    assert "don't have reliable information" in text_of(events)
    assert not [c for c in llm.calls if "general" in c]


def test_general_answers_need_gemini_and_a_true_or_false(settings):
    from assistant_api import policy

    s = replace(settings, gemini_api_key="k")
    assert policy.problems({"external": "gemini", "general_answers": True}, s) == []
    assert any(
        "needs external: gemini" in p
        for p in policy.problems({"general_answers": True}, s)
    )
    assert any(
        "true or false" in p
        for p in policy.problems({"external": "gemini", "general_answers": "yes"}, s)
    )
    assert policy.general_model({"external": "gemini", "general_answers": True}, s)
    assert (
        policy.general_model(
            {"external": "gemini", "general_answers": True},
            replace(s, gemini_api_key=""),
        )
        is None
    )


COMEDK = hit(
    "q1",
    "Q: What is the COMEDK code for Demo College? A: The COMEDK code of Demo College is E123.",
    "COMEDK code",
    score=0.62,
    kind="curated_qa",
)
KCET = hit(
    "q2",
    "Q: What is the KCET code for Demo College? A: The KCET code of Demo College is E124.",
    "KCET code",
    score=0.6,
    kind="curated_qa",
)


def test_an_unanswered_typo_offers_the_colleges_own_questions(make_client, tenant):
    llm = FakeLLM("NO_ANSWER", "The COMEDK code of Demo College is E123 [1].")
    client, _ = make_client(llm, FakeKnowledge(results=[COMEDK, KCET]))
    session = str(uuid.uuid4())
    _, events = ask(client, tenant, "what is the comdk code", "rq1", session=session)
    text = text_of(events)
    assert "What is the COMEDK code for Demo College?" in text
    # The full match first ("yes" picks it), the related KCET question after it.
    assert "1. “What is the COMEDK code for Demo College?" in text
    assert text.index("COMEDK") < text.index("KCET")
    done = done_of(events)
    assert done["outcome"] == "clarify" and done["offer_handoff"] is False
    _, events = ask(client, tenant, "yes", "rq1", session=session)
    assert "E123" in text_of(events) and "[1]" in text_of(events)
    assert done_of(events)["outcome"] == "answered"


def test_a_number_picks_one_of_several_suggestions(make_client, tenant):
    llm = FakeLLM("NO_ANSWER", "The KCET code of Demo College is E124 [1].")
    client, _ = make_client(llm, FakeKnowledge(results=[COMEDK, KCET]))
    session = str(uuid.uuid4())
    _, events = ask(client, tenant, "comdk or kcet code", "rq2", session=session)
    assert "1. \u201cWhat is the COMEDK" in text_of(events)
    assert "2. \u201cWhat is the KCET" in text_of(events)
    _, events = ask(client, tenant, "2", "rq2", session=session)
    asked = [c for c in llm.calls if "messages" in c][-1]["messages"][-1]["content"]
    assert "What is the KCET code for Demo College?" in asked


def test_a_misspelt_question_is_offered_corrected_on_gemini(settings, tenant):
    llm = GeneralLLM("A general answer.", spelling="Is there a hostel for girls?")
    client = _opted_in(settings, tenant, llm)
    _, events = ask(client, tenant, "is there a hostle for gilrs", "sp1")
    assert text_of(events).startswith(
        "Did you mean \u201cIs there a hostel for girls?\u201d"
    )
    assert done_of(events)["outcome"] == "clarify"
    assert not [c for c in llm.calls if "general" in c]  # no general answer needed
