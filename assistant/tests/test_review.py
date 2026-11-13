"""Asynchronous grounding review (PRD F1.4b)."""

import asyncio
import uuid

from assistant_api import review
from conftest import FakeLLM, needs_db

FEES = (
    "Fees by program. M.Tech Computer Science and Engineering (PG, 24 months): tuition (general) 180000 INR "
    "per year; hostel (general) 90000 INR per year."
)


class Quoting(FakeLLM):
    def __init__(self, quote):
        super().__init__()
        self.quote = quote

    async def complete(self, model, messages, max_tokens, timeout):
        self.calls.append(messages[0]["content"])
        return self.quote


def run(llm, answer, content=FEES):
    return asyncio.run(
        review.review_message(llm, "m", answer, [{"n": 1, "content": content}])
    )


def test_invented_claim_is_unsupported_even_with_a_plausible_quote():
    # Day 4's real hallucination: nothing in the source mentions instalments.
    r = run(
        Quoting(
            "M.Tech Computer Science and Engineering (PG, 24 months): tuition (general) 180000 INR"
        ),
        "Tuition fees for M.Tech Computer Science and Engineering can be paid in instalments [1].",
    )
    assert r["verdict"] == "unsupported" and r["sentences"][0]["new_words"] == [
        "instalments",
        "paid",
    ]


def test_supported_claim_with_a_verified_quote():
    r = run(
        Quoting("hostel (general) 90000 INR per year"),
        "The hostel fee is 90000 INR per year [1].",
    )
    assert r["verdict"] == "supported"


# One word ("residents") the source lacks: too few to reject outright, so the judge decides.
NEEDS_JUDGE = (
    "The M.Tech Computer Science and Engineering hostel fee is 90000 INR per year for "
    "residents [1]."
)


def test_quote_that_is_not_in_the_source_or_none_fails():
    assert run(Quoting("NONE"), NEEDS_JUDGE)["verdict"] == "unsupported"
    made_up = Quoting("hostel fees are waived for all girls")
    assert run(made_up, NEEDS_JUDGE)["verdict"] == "unsupported"


def test_nothing_to_review():
    assert (
        run(Quoting("x"), "Would you like me to connect you with the team?")["verdict"]
        == "skipped"
    )
    r = asyncio.run(
        review.review_message(
            Quoting("x"), "m", "The fee is 1 [1].", [{"n": 1, "content": None}]
        )
    )
    assert r["verdict"] == "skipped"


@needs_db
def test_review_runs_on_stored_answers_and_lists_unsupported(
    make_client, settings, tenant
):
    from conftest import SERVICE_KEY, visitor_token

    client, deps = make_client(
        FakeLLM(
            "Hostel gates close at 21:30 [1].",
            "Hostels have a rooftop swimming pool open at 21:30 [1].",
        )
    )
    for i, q in enumerate(
        ["When do hostel gates close?", "Is there a pool in the hostel?"]
    ):
        client.post(
            f"/chat/r{i}/messages",
            json={"text": q},
            headers={
                "Authorization": f"Bearer {visitor_token(tenant, str(uuid.uuid4()))}"
            },
        )
    deps.llm = Quoting("Hostel gates close at 21:30.")
    svc = {"X-Service-Key": SERVICE_KEY, "X-Tenant-Id": tenant}
    counts = client.post("/reviews/run", headers=svc).json()
    items = client.get("/reviews/unsupported", headers=svc).json()["items"]
    assert counts["supported"] >= 1
    assert all("pool" not in i["question"] or i["sentences"] for i in items)


def test_echoed_prompt_label_is_ignored():
    r = run(
        Quoting("Words from the source: hostel (general) 90000 INR per year."),
        "The hostel fee is 90000 INR per year [1].",
    )
    assert r["verdict"] == "supported"


def test_filler_words_and_synonyms_are_not_new_claims():
    labs = "The department has a programming lab, a networks lab, an AI and machine learning lab and an IoT lab."
    assert (
        review.novel_words(
            "The department has several labs including a networks lab.", [labs]
        )
        == []
    )
    hostel = "Separate hostels for men and women are available on campus."
    assert (
        review.novel_words("Yes, there is a hostel for girls on campus.", [hostel])
        == []
    )
    assert review.novel_words("The hostel has a swimming pool.", [hostel]) == [
        "pool",
        "swimming",
    ]


@needs_db
def test_evaluation_can_review_named_answers_and_gets_claim_counts(
    make_client, settings, tenant
):
    import psycopg
    from conftest import TEST_URL, visitor_token

    client, deps = make_client(
        FakeLLM(
            "Hostel gates close at 21:30 [1].",
            "There are separate hostels for men and women with wardens [1].",
        )
    )
    for i, q in enumerate(
        ["When do hostel gates close?", "Are hostels separate for men and women?"]
    ):
        client.post(
            f"/chat/e{i}/messages",
            json={"text": q},
            headers={
                "Authorization": f"Bearer {visitor_token(tenant, str(uuid.uuid4()))}"
            },
        )
    with psycopg.connect(TEST_URL) as conn:
        conn.execute("select set_config('app.tenant_id', %s, false)", (tenant,))
        ids = dict(
            conn.execute(
                "select conversation_id, id::text from chat.messages where sender = 'assistant'"
            ).fetchall()
        )

    class Judge(Quoting):  # quotes the gate time; finds nothing for the other claim
        async def complete(self, model, messages, max_tokens, timeout):
            self.calls.append(messages[0]["content"])
            return (
                self.quote
                if "21:30" in messages[0]["content"].split("Claim:")[1]
                else "NONE"
            )

    deps.llm = Judge("Hostel gates close at 21:30.")
    from_a = {"X-Service-Key": "key-from-a", "X-Tenant-Id": tenant}
    r = client.post(
        "/reviews/messages", headers=from_a, json={"message_ids": list(ids.values())}
    )
    assert r.status_code == 200
    items = r.json()["items"]
    good, bad = items[ids["e0"]], items[ids["e1"]]
    assert good == {
        "verdict": "supported",
        "claims": 1,
        "supported": 1,
        "unsupported": [],
    }
    assert bad["verdict"] == "unsupported" and bad["supported"] == 0
    # Asking again reuses the stored review (no model calls).
    calls = len(deps.llm.calls)
    again = client.post(
        "/reviews/messages", headers=from_a, json={"message_ids": list(ids.values())}
    ).json()["items"]
    assert again == items and len(deps.llm.calls) == calls
    # Module C may not run reviews.
    from_c = {"X-Service-Key": "key-from-c", "X-Tenant-Id": tenant}
    assert (
        client.post(
            "/reviews/messages", headers=from_c, json={"message_ids": []}
        ).status_code
        == 403
    )


LABS = (
    "The department has a programming lab, a networks lab, an AI and machine learning lab with "
    "GPU workstations, and an IoT lab. Labs are open until 20:00 on weekdays."
)
HOSTEL_QA = (
    "Q: Is there a hostel for first-year students?\nA: Yes. There are separate hostels for men "
    "and women on campus, and first-year students share triple rooms."
)


def test_a_claim_the_source_states_is_supported_even_if_the_judge_says_none():
    # Seen on qwen2.5:3b: NONE for a near-verbatim sentence. No model call is needed for it.
    llm = Quoting("NONE")
    r = run(
        llm,
        "The CSE department has several labs including a programming lab, a networks lab, an AI "
        "and machine learning lab with GPU workstations, and an IoT lab [1].",
        LABS,
    )
    assert r["verdict"] == "supported" and r["sentences"][0]["method"] == "words"
    assert llm.calls == []
    assert (
        run(Quoting("NONE"), "Yes, there is a hostel for girls [1].", HOSTEL_QA)[
            "verdict"
        ]
        == "supported"
    )


def test_word_support_does_not_cover_negation_wrong_or_borrowed_numbers():
    for claim, source in [
        ("There is no hostel for girls [1].", HOSTEL_QA),
        ("The M.Tech hostel fee is 95000 INR per year [1].", FEES),
        (
            "The M.Tech tuition fee is 90000 INR per year [1].",
            FEES,
        ),  # the hostel's number
    ]:
        r = run(Quoting("NONE"), claim, source)
        assert r["verdict"] == "unsupported", claim


def test_the_colleges_own_name_is_not_a_new_claim():
    claim = "The M.Tech hostel fee at Demo College of Engineering is 90000 INR per year [1]."
    assert run(Quoting("NONE"), claim)["verdict"] == "unsupported"
    r = asyncio.run(
        review.review_message(
            Quoting("NONE"),
            "m",
            claim,
            [{"n": 1, "content": FEES}],
            known="Demo College of Engineering",
        )
    )
    assert r["verdict"] == "supported"
