"""Drafting the missing answer of a "No reliable answer" proposal from the college's documents."""

from dataclasses import replace
from datetime import datetime, timezone

from conftest import needs_db
from test_learning_db import doc, h, proposals

pytestmark = needs_db

ATM = "Campus services. There is an ATM beside the main gate, open 24 hours a day."


class FakeDraftModel:
    name = "fake-answer-model"

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def complete(self, system, user):
        self.calls.append(user)
        return self.replies.pop(0)


def _gap(lclient, assistant, tenant, question="Is there an ATM on campus?"):
    assistant.turns.append(
        {
            "id": f"t-{question}",
            "conversation_id": "c1",
            "question": question,
            "answer": "I don't know.",
            "outcome": "no_answer",
            "sources": [],
            "trace_id": None,
            "created_at": datetime.now(timezone.utc),
        }
    )
    lclient.post("/improvements/analyse", headers=h(tenant))
    return next(p for p in proposals(lclient, tenant) if question in p["title"])


def _client(lclient, settings, model):
    from knowledge_api.deps import settings_dep

    # The hash embedder used in tests scores low, so every document counts as covering here.
    lclient.app.dependency_overrides[settings_dep] = lambda: replace(
        settings, draft_min_score=0.0
    )
    lclient.app.state.draft_model = model
    return lclient


def test_a_draft_comes_from_the_documents_with_its_sources(
    lclient, assistant, settings, tenant
):
    doc(lclient, tenant, "campus.md", ATM)
    model = FakeDraftModel(
        "Yes, there is an ATM beside the main gate, open 24 hours [1]."
    )
    c = _client(lclient, settings, model)
    p = _gap(c, assistant, tenant)
    r = c.post(f"/improvements/{p['id']}/draft", headers=h(tenant))
    assert r.status_code == 200, r.text
    d = r.json()["draft"]
    assert d["text"] == "Yes, there is an ATM beside the main gate, open 24 hours."
    assert [s["title"] for s in d["sources"]] == ["campus.md"]
    assert "ATM beside the main gate" in model.calls[0]  # the model saw the document
    # Kept on the proposal for the approver; still pending: nothing went live.
    again = c.get(f"/improvements/{p['id']}", headers=h(tenant)).json()
    assert again["diff"]["draft"]["text"] == d["text"] and again["status"] == "pending"


def test_a_draft_is_refused_with_a_reason_rather_than_invented(
    lclient, assistant, settings, tenant
):
    doc(lclient, tenant, "campus.md", ATM)
    c = _client(
        lclient,
        settings,
        FakeDraftModel(
            "The ATM is open 18 hours a day [1].",  # a number that is not in the document
            "There is an ATM beside the main gate.",  # no source number
            "NO_ANSWER",
        ),
    )
    p = _gap(c, assistant, tenant)
    for reason in ("number not found", "did not say which document", "do not answer"):
        d = c.post(f"/improvements/{p['id']}/draft", headers=h(tenant)).json()["draft"]
        assert d["text"] is None and reason in d["reason"], d


def test_without_documents_the_model_is_not_asked(lclient, assistant, settings, tenant):
    model = FakeDraftModel()
    c = _client(lclient, settings, model)
    p = _gap(c, assistant, tenant)
    d = c.post(f"/improvements/{p['id']}/draft", headers=h(tenant)).json()["draft"]
    assert d["text"] is None and "No approved document" in d["reason"]
    assert model.calls == []


def test_only_a_pending_proposal_without_an_answer_can_be_drafted(
    lclient, assistant, settings, tenant, other_tenant
):
    c = _client(lclient, settings, FakeDraftModel())
    p = _gap(c, assistant, tenant)
    assert (
        c.post(f"/improvements/{p['id']}/draft", headers=h(other_tenant)).status_code
        == 404
    )
    c.post(
        f"/improvements/{p['id']}/reject",
        headers=h(tenant),
        json={"decided_by": "officer"},
    )
    assert (
        c.post(f"/improvements/{p['id']}/draft", headers=h(tenant)).status_code == 409
    )
