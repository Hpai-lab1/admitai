"""Module A adapter (PRD 21.4): the agreed contract, normalisation of what Module A returns,
failure handling, tenant isolation, and a full chat turn through the adapter.

Module A here is mocks/knowledge.py, served in-process through httpx's ASGI transport (real
requests and responses, no network), or httpx.MockTransport for hand-built replies. These are
mocked tests: the live check against Module A's own code is scripts/e2e_module_b.py."""

import asyncio
import json
import uuid
from dataclasses import replace

import httpx
import pytest

from assistant_api.clients import UpstreamError
from assistant_api.config import Settings
from assistant_api.knowledge import KnowledgeClient, normalise_search, with_facts
from conftest import FakeLLM, needs_db, visitor_token
from mocks import knowledge as mock_a

TENANT = str(uuid.uuid4())
OTHER = str(uuid.uuid4())


@pytest.fixture
def mock_tenants(monkeypatch):
    monkeypatch.setenv("MOCK_KB_TENANTS", TENANT)
    monkeypatch.setenv("MOCK_KB_KEY", "key-b-to-a")
    monkeypatch.delenv("MOCK_KB_NO_LIST", raising=False)


def adapter(transport=None) -> KnowledgeClient:
    s = replace(
        Settings(),
        knowledge_api_url="http://module-a.test",
        key_to_knowledge="key-b-to-a",
    )
    transport = transport or httpx.ASGITransport(app=mock_a.app)
    return KnowledgeClient(s, httpx.AsyncClient(transport=transport))


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ the agreed contract


def test_search_returns_cited_items_in_the_engines_shape(mock_tenants):
    res = run(adapter().search(TENANT, "Is there a hostel for first-year students?"))
    assert res["embedder"] == "primary" and res["items"]
    top = res["items"][0]
    assert {"id", "content", "score", "vector_score", "citation"} <= set(top)
    assert top["citation"]["source_type"] in ("document", "curated_qa")
    assert "hostel" in top["content"].lower()


def test_search_sends_the_contract_fields_and_the_key():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json={"items": [], "embedder": "primary"})

    run(
        adapter(httpx.MockTransport(handler)).search(
            TENANT,
            "electives",
            top_k=6,
            role="faculty",
            department="Computer Science",
            academic_year="2027-28",
        )
    )
    assert seen["body"] == {
        "query": "electives",
        "top_k": 6,
        "role": "faculty",
        "department": "Computer Science",
        "academic_year": "2027-28",
    }
    assert (
        seen["headers"]["x-tenant-id"] == TENANT
        and seen["headers"]["x-service-key"] == "key-b-to-a"
    )


def test_programme_facts_by_code_or_alias_and_unknown_is_none(mock_tenants):
    a = adapter()
    by_code = run(a.program_facts(TENANT, "BTECH-CSE"))
    by_alias = run(a.program_facts(TENANT, "CSE"))
    assert by_code["code"] == by_alias["code"] == "BTECH-CSE"
    tuition = next(
        f
        for f in by_code["fees"]
        if f["component"] == "tuition" and f["category"] == "general"
    )
    assert (
        tuition["amount"] == 250000
    )  # exactly as Module A gave it, never re-formatted
    assert run(a.program_facts(TENANT, "NO-SUCH-PROGRAMME")) is None


def test_another_college_gets_nothing(mock_tenants):
    a = adapter()
    assert run(a.search(OTHER, "hostel fee"))["items"] == []
    assert run(a.programs(OTHER))["items"] == []
    assert run(a.program_facts(OTHER, "BTECH-CSE")) is None


def test_a_wrong_key_is_refused(mock_tenants, monkeypatch):
    monkeypatch.setenv("MOCK_KB_KEY", "something-else")
    with pytest.raises(UpstreamError, match="HTTP 401"):
        run(adapter().search(TENANT, "hostel"))


def test_without_the_programme_list_the_turn_still_has_search(
    mock_tenants, monkeypatch
):
    """A Module A that implements only the agreed contract (no GET /catalogue/programs)."""
    monkeypatch.setenv("MOCK_KB_NO_LIST", "1")
    a = adapter()
    cat = run(a.programs(TENANT))
    assert cat["items"] == [] and cat["unavailable"] == "programme list"
    assert run(a.search(TENANT, "hostel"))["items"]


def test_a_list_without_facts_is_completed_from_the_facts_endpoint(mock_tenants):
    a = adapter()
    listed = [
        {"code": "BTECH-CSE", "name": "B.Tech CSE"},
        {"code": "BTECH-ME", "name": "B.Tech ME"},
    ]
    filled = run(with_facts(a, TENANT, listed))
    assert all(p["fees"] for p in filled)
    assert filled[0]["name"].startswith("B.Tech Computer Science")


# ------------------------------------------------------------------ failures


def test_connection_failures_are_tried_once_more_then_reported():
    attempts = []

    def handler(request):
        attempts.append(1)
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(UpstreamError, match="ConnectError"):
        run(adapter(httpx.MockTransport(handler)).search(TENANT, "q"))
    assert len(attempts) == 2


def test_a_slow_module_a_is_not_retried():
    attempts = []

    def handler(request):
        attempts.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(UpstreamError, match="ReadTimeout"):
        run(adapter(httpx.MockTransport(handler)).search(TENANT, "q"))
    assert len(attempts) == 1  # waiting twice would double the visitor's wait


@pytest.mark.parametrize(
    "response,match",
    [
        (httpx.Response(500, json={"detail": "boom"}), "HTTP 500"),
        (httpx.Response(200, content=b"<html>proxy error</html>"), "not JSON"),
        (httpx.Response(200, json=["not", "an", "object"]), "not an object"),
    ],
)
def test_bad_replies_are_upstream_errors(response, match):
    with pytest.raises(UpstreamError, match=match):
        run(adapter(httpx.MockTransport(lambda r: response)).search(TENANT, "q"))


def test_other_spellings_of_the_contract_are_normalised():
    """A Module A built separately may name fields differently; the engine sees one shape."""
    res = normalise_search(
        {
            "chunks": [
                {
                    "text": "Hostel fee is ₹90,000.",
                    "similarity": 0.71,
                    "citation": {"title": "Fees"},
                },
                {"text": "   "},  # nothing to cite: dropped
                "not a hit",
            ]
        }
    )
    assert len(res["items"]) == 1
    hit = res["items"][0]
    assert hit["content"] == "Hostel fee is ₹90,000." and hit["vector_score"] == 0.71
    assert hit["citation"]["source_type"] == "document" and res["embedder"] == "primary"


# ------------------------------------------------------------------ a whole turn through the adapter


def _chat(client, tenant, text, conv):
    r = client.post(
        f"/chat/{conv}/messages",
        json={"text": text},
        headers={"Authorization": f"Bearer {visitor_token(tenant)}"},
    )
    events = []
    for block in r.text.split("\n\n"):
        if block.strip():
            name = block.split("\n")[0].removeprefix("event: ")
            events.append((name, json.loads(block.split("data: ", 1)[1])))
    return events


@needs_db
def test_a_fee_question_is_answered_from_module_a_catalogue_facts(
    make_client, mock_tenants
):
    llm = FakeLLM(
        "The general category tuition fee for B.Tech CSE is 2,50,000 INR per year [1]."
    )
    client, _ = make_client(llm, adapter())
    events = _chat(client, TENANT, "What is the tuition fee for B.Tech CSE?", "c-a-1")
    done = events[-1][1]
    assert done["outcome"] == "answered"
    assert (
        done["sources"][0]["title"]
        == "Program catalogue 2027-28: B.Tech Computer Science and Engineering"
    )
    assert "2,50,000" in "".join(d["text"] for n, d in events if n == "token")
    prompt = llm.calls[0]["messages"][1]["content"]
    assert "Tuition fee (general category): ₹2,50,000 per year." in prompt


@needs_db
def test_a_swapped_fee_from_the_model_never_reaches_the_visitor(
    make_client, mock_tenants
):
    llm = FakeLLM("The hostel fee for B.Tech CSE is 2,50,000 INR per year [1].")
    client, _ = make_client(llm, adapter())
    events = _chat(client, TENANT, "What is the hostel fee for CSE?", "c-a-2")
    text = "".join(d["text"] for n, d in events if n == "token")
    assert "2,50,000" not in text and "not certain" in text
    assert ("handoff_requested" in [n for n, _ in events]) and events[-1][1][
        "outcome"
    ] == "blocked"


@needs_db
def test_module_a_down_means_an_honest_no_answer_and_a_person(
    make_client, mock_tenants
):
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    client, _ = make_client(
        FakeLLM("should not be used"), adapter(httpx.MockTransport(down))
    )
    events = _chat(client, TENANT, "Is there a hostel?", "c-a-3")
    names = [n for n, _ in events]
    assert events[-1][1]["outcome"] == "no_answer" and "handoff_requested" in names
