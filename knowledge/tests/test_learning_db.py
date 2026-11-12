"""The governed improvement loop (PRD 8): signals, triage, proposals, approval, evaluation gate,
deployment and rollback, plus the content-operations rules (8.3, 17)."""

from datetime import date, datetime, timedelta, timezone

import psycopg

from conftest import TEST_URL, needs_db
from knowledge_api import learning

pytestmark = needs_db


def h(tenant):
    return {"X-Tenant-Id": tenant}


def doc(client, tenant, name, text, **meta):
    r = client.post(
        "/kb/documents",
        headers=h(tenant),
        files={"file": (name, text.encode())},
        data={k: str(v) for k, v in meta.items()},
    )
    d = r.json()
    client.patch(
        f"/kb/documents/{d['id']}",
        headers=h(tenant),
        json={"action": "approve", "actor": "admin"},
    )
    return d["id"]


def qa(client, tenant, question, answer, status="approve", **kw):
    body = {
        "question": question,
        "answer": answer,
        "academic_year": "2027-28",
        "valid_to": "2099-07-31",
        **kw,
    }
    q = client.post("/curated-qa", headers=h(tenant), json=body).json()
    if status == "approve":
        client.post(
            f"/curated-qa/{q['id']}/approve",
            headers=h(tenant),
            json={"actor": "officer"},
        )
    return q["id"]


def event(client, tenant, n, type_, **data):
    r = client.post(
        "/events",
        json={"event_id": n, "type": type_, "tenant_id": tenant, "data": data},
    )
    assert r.status_code == 200


def b_message(assistant, mid, question, answer, sources, outcome="answered"):
    assistant.messages[mid] = {
        "id": mid,
        "question": question,
        "answer": answer,
        "sources": sources,
        "outcome": outcome,
        "trace_id": f"trace-{mid}",
    }


def proposals(client, tenant, status="pending"):
    return client.get(f"/improvements?status={status}", headers=h(tenant)).json()[
        "items"
    ]


def case(
    settings,
    tenant,
    ext,
    question,
    expected=(),
    forbidden=(),
    kind="direct",
    critical=False,
):
    with psycopg.connect(settings.admin_database_url) as conn:
        conn.execute(
            "insert into learn.eval_cases (tenant_id, ext_id, question, kind, expected_refs, forbidden_refs, critical)"
            " values (%s,%s,%s,%s,%s,%s,%s)",
            (tenant, ext, question, kind, list(expected), list(forbidden), critical),
        )


# ------------------------------------------------------------------ triage (no database)


def test_triage_checks_correctness_first():
    cited = [{"source_type": "document", "ref_id": "d1", "title": "Fees"}]
    t = learning.triage
    assert t(
        {
            "kind": "thumb_down",
            "reason": "not_what_i_asked",
            "outcome": "answered",
            "sources": cited,
            "question": "What is the fee?",
        }
    ) == ("correct_but_unwelcome", "correct_but_unwelcome")
    assert (
        t(
            {
                "kind": "thumb_down",
                "reason": None,
                "outcome": "answered",
                "sources": cited,
                "question": "fee?",
            }
        )[0]
        == "correct_but_unwelcome"
    )
    assert (
        t(
            {
                "kind": "thumb_down",
                "reason": "outdated",
                "outcome": "answered",
                "sources": cited,
                "question": "fee?",
            }
        )[0]
        == "outdated"
    )
    assert (
        t(
            {
                "kind": "thumb_down",
                "reason": "hard_to_understand",
                "outcome": "answered",
                "sources": cited,
                "question": "fee?",
            }
        )[0]
        == "tone"
    )
    assert (
        t(
            {
                "kind": "thumb_down",
                "reason": None,
                "outcome": "answered",
                "sources": cited,
                "question": "How do I get a refund?",
            }
        )[0]
        == "policy"
    )
    assert (
        t({"kind": "no_answer", "outcome": "no_answer", "sources": []})[0]
        == "knowledge_gap"
    )
    assert (
        t(
            {
                "kind": "correction",
                "reason": "fee changed this year",
                "outcome": "answered",
                "sources": cited,
            }
        )[0]
        == "hallucination"  # a correction always proposes the corrected answer
    )
    assert t({"kind": "thumb_up", "sources": []})[0] is None


def test_working_day_sla():
    friday = datetime(2026, 10, 30, 10, tzinfo=timezone.utc)
    assert learning.add_working_days(friday, 3).date() == date(2026, 11, 4)


# ------------------------------------------------------------------ the loop end to end


def test_outdated_document_is_expired_after_the_gate_and_can_be_rolled_back(
    lclient, assistant, settings, tenant
):
    old = doc(
        lclient,
        tenant,
        "fees-2026.md",
        "# Hostel fee\n\nThe hostel fee is 85000 per year.",
    )
    doc(
        lclient,
        tenant,
        "hostel.md",
        "# Hostel rooms\n\nTriple rooms for first-year students.",
    )
    case(
        settings,
        tenant,
        "c1",
        "What kind of hostel rooms are there?",
        expected=["doc:hostel.md"],
        critical=True,
    )
    src = [{"source_type": "document", "ref_id": old, "title": "fees-2026.md"}]
    for i in (1, 2):
        b_message(
            assistant,
            f"m{i}",
            "What is the hostel fee?",
            "The hostel fee is 85000 per year.",
            src,
        )
        event(
            lclient,
            tenant,
            i,
            "feedback.created",
            message_id=f"m{i}",
            kind="thumb",
            value=-1,
            reason="outdated",
        )
    report = lclient.post("/improvements/analyse", headers=h(tenant)).json()
    assert (
        report["ingested"]["events"] == 2 and report["proposals"]["new_proposals"] == 1
    )
    [p] = proposals(lclient, tenant)
    assert (
        p["kind"] == "document_action"
        and p["root_cause"] == "outdated"
        and p["evidence_count"] == 2
    )
    assert (
        0 < p["impact_score"] <= 1
        and p["examples"][0]["question"] == "What is the hostel fee?"
    )

    r = lclient.post(
        f"/improvements/{p['id']}/approve",
        headers=h(tenant),
        json={"decided_by": "officer-1"},
    )
    assert r.status_code == 200 and r.json()["status"] == "evaluating"
    done = lclient.get(
        f"/improvements/{p['id']}", headers=h(tenant)
    ).json()  # background task has run
    assert done["status"] == "deployed", done
    assert (
        done["eval"]["passed"] and done["eval"]["gate"]["baseline"]["accuracy"] == 1.0
    )
    assert (
        lclient.get(f"/kb/documents/{old}", headers=h(tenant)).json()["status"]
        == "expired"
    )
    # the answering happened on a sandbox copy, which is gone again
    assert {t for t, _ in assistant.asked} - {tenant}
    with psycopg.connect(settings.admin_database_url) as conn:
        assert conn.execute("select count(*) from kb.sandboxes").fetchone()[0] == 0
    assert set(assistant.config) <= {
        tenant
    }  # the sandbox's Module B settings were deleted too
    # the proposal's own case joined the regression set
    with psycopg.connect(settings.admin_database_url) as conn:
        assert (
            conn.execute(
                "select count(*) from learn.eval_cases where source = 'proposal'"
            ).fetchone()[0]
            == 1
        )

    r = lclient.post(
        f"/improvements/{p['id']}/rollback",
        headers=h(tenant),
        json={"decided_by": "admin"},
    )
    assert r.json()["status"] == "rolled_back"
    with psycopg.connect(settings.admin_database_url) as conn:
        retired = conn.execute(
            "select active from learn.eval_cases where source = 'proposal'"
        ).fetchone()
    assert retired[0] is False  # rolled back: the cases it added are retired
    assert (
        lclient.get(f"/kb/documents/{old}", headers=h(tenant)).json()["status"]
        == "approved"
    )


def test_gate_blocks_a_change_that_breaks_a_critical_case(
    lclient, assistant, settings, tenant
):
    shared = doc(
        lclient,
        tenant,
        "prospectus.md",
        "# Hostel fee\n\nThe hostel fee is 90000 per year.",
    )
    case(
        settings,
        tenant,
        "c1",
        "What is the hostel fee?",
        expected=["doc:prospectus.md"],
        critical=True,
    )
    src = [{"source_type": "document", "ref_id": shared, "title": "prospectus.md"}]
    b_message(assistant, "m1", "What is the hostel fee?", "90000", src)
    event(
        lclient,
        tenant,
        1,
        "feedback.created",
        message_id="m1",
        kind="thumb",
        value=-1,
        reason="wrong",
    )
    lclient.post("/improvements/analyse", headers=h(tenant))
    [p] = proposals(lclient, tenant)
    lclient.post(
        f"/improvements/{p['id']}/approve",
        headers=h(tenant),
        json={"decided_by": "officer"},
    )
    done = lclient.get(f"/improvements/{p['id']}", headers=h(tenant)).json()
    assert done["status"] == "failed_gate"
    assert any("critical case c1" in r for r in done["eval"]["gate"]["reasons"])
    assert (
        lclient.get(f"/kb/documents/{shared}", headers=h(tenant)).json()["status"]
        == "approved"
    )


def test_expert_correction_becomes_a_curated_answer(
    lclient, assistant, settings, tenant
):
    b_message(
        assistant,
        "m1",
        "Can I pay the fee in instalments?",
        "I don't know.",
        [],
        outcome="no_answer",
    )
    event(
        lclient,
        tenant,
        1,
        "correction.submitted",
        message_id="m1",
        conversation_id="c1",
        corrected_answer="Yes, the fee can be paid in two instalments, in August and January.",
        reason="Staff reply to a callback",
        departments=[],
    )
    lclient.post("/improvements/analyse", headers=h(tenant))
    [p] = proposals(lclient, tenant)
    assert p["kind"] == "curated_answer" and p["proposal"].startswith(
        "Yes, the fee can be paid"
    )
    lclient.post(
        f"/improvements/{p['id']}/approve",
        headers=h(tenant),
        json={"decided_by": "officer"},
    )
    assert (
        lclient.get(f"/improvements/{p['id']}", headers=h(tenant)).json()["status"]
        == "deployed"
    )
    items = lclient.post(
        "/kb/search", headers=h(tenant), json={"query": "pay fee in instalments"}
    ).json()["items"]
    assert items[0]["kind"] == "curated_qa" and "two instalments" in items[0]["content"]
    with psycopg.connect(TEST_URL) as conn:
        conn.execute("select set_config('app.tenant_id', %s, false)", (tenant,))
        # No catalogue or curated answers yet: the year falls back to the current academic year.
        status, year, valid_to, ref = conn.execute(
            "select status, academic_year, valid_to, external_ref from kb.curated_qa"
        ).fetchone()
        today = date.today()
        assert (
            status == "approved"
            and year == f"{today.year}-{(today.year + 1) % 100:02d}"
        )
        assert valid_to == date(today.year + 1, 7, 31) and ref.startswith("proposal-")


def test_knowledge_gaps_cluster_and_need_an_answer_to_approve(
    lclient, assistant, settings, tenant
):
    now = datetime.now(timezone.utc)
    for i, q in enumerate(
        [
            "Is there a swimming pool on campus?",
            "Is there a swimming pool on the campus?",
            "What is the library timing?",
        ]
    ):
        assistant.turns.append(
            {
                "id": f"t{i}",
                "conversation_id": f"c{i}",
                "question": q,
                "answer": "I don't know.",
                "outcome": "no_answer",
                "sources": [],
                "trace_id": None,
                "created_at": now - timedelta(minutes=10 - i),
            }
        )
    lclient.post("/improvements/analyse", headers=h(tenant))
    props = proposals(lclient, tenant)
    assert sorted(p["evidence_count"] for p in props) == [1, 2]
    pool = next(p for p in props if p["evidence_count"] == 2)
    assert (
        pool["impact_score"]
        > next(p for p in props if p["evidence_count"] == 1)["impact_score"]
    )
    assert props[0]["id"] == pool["id"]  # ranked by impact
    r = lclient.post(
        f"/improvements/{pool['id']}/approve",
        headers=h(tenant),
        json={"decided_by": "officer"},
    )
    assert r.status_code == 422
    r = lclient.post(
        f"/improvements/{pool['id']}/approve",
        headers=h(tenant),
        json={
            "decided_by": "officer",
            "note": "There is no swimming pool on campus at present.",
        },
    )
    assert (
        lclient.get(f"/improvements/{pool['id']}", headers=h(tenant)).json()["status"]
        == "deployed"
    )
    # running the analyst again does not re-raise the same turns
    assert (
        lclient.post("/improvements/analyse", headers=h(tenant)).json()["ingested"][
            "b_turns"
        ]
        == 0
    )


def test_correct_but_unwelcome_changes_nothing_and_is_excluded_from_acceptance(
    lclient, assistant, tenant
):
    src = [{"source_type": "document", "ref_id": "d1", "title": "fees.md"}]
    b_message(
        assistant, "m1", "What is the fee?", "The fee is 250000 per year [1].", src
    )
    b_message(assistant, "m2", "Is there a hostel?", "Yes [1].", src)
    event(
        lclient,
        tenant,
        1,
        "feedback.created",
        message_id="m1",
        kind="thumb",
        value=-1,
        reason="not_what_i_asked",
    )
    event(
        lclient, tenant, 2, "feedback.created", message_id="m2", kind="thumb", value=1
    )
    lclient.post("/improvements/analyse", headers=h(tenant))
    assert proposals(lclient, tenant) == []
    rep = lclient.get("/reports/weekly", headers=h(tenant)).json()
    assert rep["feedback"] == {
        "thumbs_up": 1,
        "thumbs_down": 0,
        "correct_but_unwelcome": 1,
    }
    assert rep["acceptance_rate"] == 1.0


def test_policy_signal_adds_a_sensitive_topic_in_module_b(lclient, assistant, tenant):
    src = [{"source_type": "document", "ref_id": "d1", "title": "rules.md"}]
    b_message(
        assistant,
        "m1",
        "How do I get a refund of fees?",
        "Refunds follow the rules [1].",
        src,
    )
    event(
        lclient,
        tenant,
        1,
        "feedback.created",
        message_id="m1",
        kind="thumb",
        value=-1,
        reason="other",
    )
    lclient.post("/improvements/analyse", headers=h(tenant))
    [p] = proposals(lclient, tenant)
    assert p["kind"] == "sensitive_topic" and p["diff"]["topic"] == "refund"
    lclient.post(
        f"/improvements/{p['id']}/approve",
        headers=h(tenant),
        json={"decided_by": "admin"},
    )
    assert "refund" in assistant.config[tenant]["sensitive_topics"]


def test_overdue_proposals_escalate_to_the_college_admin(settings, embedder, tenant):
    from knowledge_api.db import Jsonb, tx

    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "insert into learn.proposals (tenant_id, kind, root_cause, rung, title, proposal, diff, department,"
            " severity, due_at, created_at) values (%s,'curated_answer','knowledge_gap',1,'t','p',%s,"
            " 'Computer Science',2, now() - interval '1 day', now() - interval '5 days') returning id",
            (tenant, Jsonb({"question": "q", "answer": None})),
        )
        pid = cur.fetchone()["id"]
        cur.execute(
            "insert into learn.signals (tenant_id, kind, source_ref, question, root_cause, proposal_id)"
            " values (%s,'no_answer','x','q','knowledge_gap',%s)",
            (tenant, pid),
        )
        learning.refresh_pending(cur, datetime.now(timezone.utc))
        cur.execute(
            "select department, escalated, diff from learn.proposals where id = %s",
            (pid,),
        )
        p = cur.fetchone()
    assert (
        p["escalated"]
        and p["department"] is None
        and p["diff"]["department"] == "Computer Science"
    )


def test_catalogue_wins_suspends_conflicting_curated_answer(lclient, tenant):
    programs = (
        "code,name,aliases,department,level,duration_months,intake,eligibility,academic_year\n"
        "BTECH-CSE,B.Tech Computer Science,CSE,Computer Science,UG,48,120,PCM,2027-28\n"
    )
    fees = "program_code,category,component,amount\nBTECH-CSE,general,tuition,250000\n"
    wrong = qa(
        lclient,
        tenant,
        "What is the CSE fee?",
        "The CSE tuition is Rs 2,40,000 per year.",
    )
    right = qa(
        lclient, tenant, "CSE tuition?", "The CSE tuition is Rs 2,50,000 per year."
    )
    r = lclient.post(
        "/catalogue/import",
        headers=h(tenant),
        files={"programs_csv": ("p.csv", programs), "fees_csv": ("f.csv", fees)},
    )
    assert r.json()["curated_suspended"] == 1
    assert (
        lclient.get(f"/curated-qa/{wrong}", headers=h(tenant)).json()["status"]
        == "suspended"
    )
    assert (
        lclient.get(f"/curated-qa/{right}", headers=h(tenant)).json()["status"]
        == "approved"
    )
    [p] = proposals(lclient, tenant)
    assert p["kind"] == "curated_conflict" and p["diff"]["quoted"] == ["240000"]


def test_cutover_expires_old_year_and_reissues_drafts(lclient, tenant):
    doc(
        lclient,
        tenant,
        "prospectus.md",
        "# Fees\n\nOld prospectus.",
        academic_year="2027-28",
    )
    q = qa(lclient, tenant, "Is there a hostel?", "Yes, separate hostels.")
    r = lclient.post(
        "/cutover",
        headers=h(tenant),
        json={
            "old_year": "2027-28",
            "new_year": "2028-29",
            "new_valid_to": "2029-07-31",
        },
    )
    assert r.json() == {"documents_expired": 1, "curated_reissued_as_drafts": 1}
    got = lclient.get(f"/curated-qa/{q}", headers=h(tenant)).json()
    assert (got["status"], got["academic_year"], got["version"]) == (
        "draft",
        "2028-29",
        2,
    )
    assert got["history"][-1]["action"] == "cutover"


def test_proposals_are_tenant_scoped(lclient, assistant, tenant, other_tenant):
    assistant.turns.append(
        {
            "id": "t1",
            "conversation_id": "c",
            "question": "pool?",
            "answer": "",
            "trace_id": None,
            "outcome": "no_answer",
            "sources": [],
            "created_at": datetime.now(timezone.utc),
        }
    )
    lclient.post("/improvements/analyse", headers=h(tenant))
    assert len(proposals(lclient, tenant)) == 1
    assert proposals(lclient, other_tenant) == []
    pid = proposals(lclient, tenant)[0]["id"]
    assert (
        lclient.post(
            f"/improvements/{pid}/reject", headers=h(other_tenant), json={}
        ).status_code
        == 404
    )


def test_degraded_export_lists_approved_answers(lclient, tenant):
    qa(lclient, tenant, "Is there a hostel?", "Yes.")
    qa(lclient, tenant, "Draft question?", "Draft.", status="draft")
    items = lclient.get("/export/degraded", headers=h(tenant)).json()["items"]
    assert [i["question"] for i in items] == ["Is there a hostel?"]


def test_grader_wildcards_and_flake_retry(settings, assistant, tenant):
    from knowledge_api import gate

    case = {
        "ext_id": "x",
        "kind": "direct",
        "expected_refs": ["catalogue:*"],
        "forbidden_refs": [],
        "expected_facts": ["90000"],
        "expected_role": None,
        "critical": True,
    }
    reply = {
        "text": "Hostel is 90,000 INR [1].",
        "done": {
            "outcome": "answered",
            "sources": [{"source_type": "catalogue", "ref_id": "MBA"}],
        },
    }
    assert gate.grade(case, reply, {})["passed"]

    # A case that passed in the baseline and fails once in the candidate is asked again.
    base = {"results": [{"id": "x", "passed": True}]}
    cand = {
        "id": None,
        "results": [
            {
                "id": "x",
                "passed": False,
                "category": "accuracy",
                "forbidden_cited": False,
                "role_ok": None,
                "critical": True,
            }
        ],
    }
    calls = []

    class Flaky:
        def ask(self, target, conv, text):
            calls.append(text)
            return reply

    case_row = {**case, "question": "hostel fee?", "language": "en"}
    assert (
        gate.retry_regressions(
            settings, Flaky(), tenant, tenant, base, cand, [case_row]
        )
        == 1
    )
    assert (
        cand["results"][0]["passed"]
        and cand["results"][0]["retried"]
        and len(calls) == 1
    )


def test_outdated_catalogue_answer_becomes_a_manual_task(lclient, assistant, tenant):
    src = [
        {
            "source_type": "catalogue",
            "ref_id": "BTECH-CSE",
            "title": "Program catalogue 2027-28: B.Tech CSE",
        }
    ]
    b_message(assistant, "m1", "What is the CSE fee?", "250000 INR [1].", src)
    event(
        lclient,
        tenant,
        1,
        "feedback.created",
        message_id="m1",
        kind="thumb",
        value=-1,
        reason="outdated",
    )
    lclient.post("/improvements/analyse", headers=h(tenant))
    [p] = proposals(lclient, tenant)
    assert p["diff"]["action"] == "review_catalogue" and "re-import" in p["proposal"]
    r = lclient.post(
        f"/improvements/{p['id']}/approve",
        headers=h(tenant),
        json={"decided_by": "officer"},
    )
    assert r.json()["status"] == "approved_manual"


def test_unsupported_answer_from_grounding_review_becomes_a_proposal(
    lclient, assistant, tenant
):
    now = datetime.now(timezone.utc)
    assistant.reviews.append(
        {
            "id": "m9",
            "conversation_id": "c9",
            "trace_id": None,
            "created_at": now,
            "question": "Can I pay the fee in instalments?",
            "answer": "Tuition fees for M.Tech can be paid in instalments [1].",
            "sentences": [
                {
                    "sentence": "Tuition fees for M.Tech can be paid in instalments [1].",
                    "supported": False,
                }
            ],
        }
    )
    first = lclient.post("/improvements/analyse", headers=h(tenant)).json()
    assert first["proposals"]["new_proposals"] == 1
    [p] = proposals(lclient, tenant)
    assert p["root_cause"] == "hallucination" and p["title"].startswith(
        "Unsupported claim in an answer"
    )
    assert "paid in instalments" in p["proposal"] and p["diff"]["answer"] is None
    # the same review is not ingested twice
    assert (
        lclient.post("/improvements/analyse", headers=h(tenant)).json()["proposals"][
            "new_proposals"
        ]
        == 0
    )


def test_correction_to_an_answered_question_becomes_a_new_version(
    lclient, assistant, settings, tenant
):
    old = qa(
        lclient,
        tenant,
        "Can I pay the fee in instalments?",
        "No, the fee is paid at once.",
    )
    src = [
        {
            "source_type": "curated_qa",
            "ref_id": old,
            "title": "Can I pay the fee in instalments?",
        }
    ]
    b_message(
        assistant,
        "m1",
        "Can I pay the fee in instalments?",
        "No, the fee is paid at once [1].",
        src,
    )
    event(
        lclient,
        tenant,
        1,
        "correction.submitted",
        message_id="m1",
        conversation_id="c1",
        corrected_answer="Yes, the fee can be paid in two instalments, in August and January.",
        reason="Fee office rule changed this year",
    )
    lclient.post("/improvements/analyse", headers=h(tenant))
    [p] = proposals(lclient, tenant)
    lclient.post(
        f"/improvements/{p['id']}/approve",
        headers=h(tenant),
        json={"decided_by": "officer"},
    )
    assert (
        lclient.get(f"/improvements/{p['id']}", headers=h(tenant)).json()["status"]
        == "deployed"
    )
    items = lclient.get("/curated-qa", headers=h(tenant)).json()["items"]
    assert len(items) == 1  # updated in place, no duplicate answer
    got = lclient.get(f"/curated-qa/{old}", headers=h(tenant)).json()
    assert (
        got["version"] == 2
        and "two instalments" in got["answer"]
        and got["status"] == "approved"
    )
    lclient.post(
        f"/improvements/{p['id']}/rollback",
        headers=h(tenant),
        json={"decided_by": "admin"},
    )
    got = lclient.get(f"/curated-qa/{old}", headers=h(tenant)).json()
    assert (
        got["answer"] == "No, the fee is paid at once." and got["status"] == "approved"
    )


def test_an_interrupted_gate_is_recovered_and_can_be_approved_again(
    lclient, assistant, settings, tenant
):
    from knowledge_api import gate

    doc(
        lclient,
        tenant,
        "hostel.md",
        "# Hostel\n\nTriple rooms for first-year students.",
    )
    assistant.turns.append(
        {
            "id": "t1",
            "conversation_id": "c",
            "question": "Is there a pool?",
            "answer": "",
            "trace_id": None,
            "outcome": "no_answer",
            "sources": [],
            "created_at": datetime.now(timezone.utc),
        }
    )
    lclient.post("/improvements/analyse", headers=h(tenant))
    [p] = proposals(lclient, tenant)
    sandbox, _ = gate.clone_tenant(settings, tenant)  # as if a gate run was in progress
    with psycopg.connect(settings.admin_database_url, autocommit=True) as conn:
        conn.execute(
            "update learn.proposals set status = 'evaluating', updated_at = now() - interval '2 hours'"
            " where id = %s",
            (p["id"],),
        )
        conn.execute("update kb.sandboxes set created_at = now() - interval '2 hours'")
    assert gate.recover_interrupted(settings) == {
        "proposals": 1,
        "runs": 0,
        "sandboxes": 1,
    }
    got = lclient.get(f"/improvements/{p['id']}", headers=h(tenant)).json()
    assert got["status"] == "failed_gate" and "interrupted" in got["note"]
    with psycopg.connect(settings.admin_database_url) as conn:
        assert (
            conn.execute(
                "select count(*) from kb.documents where tenant_id = %s", (sandbox,)
            ).fetchone()[0]
            == 0
        )
    # it can be approved again (and still needs an answer, as any knowledge gap does)
    assert (
        lclient.post(
            f"/improvements/{p['id']}/reject", headers=h(tenant), json={}
        ).status_code
        == 409
    )
    r = lclient.post(
        f"/improvements/{p['id']}/approve",
        headers=h(tenant),
        json={"decided_by": "officer", "note": "There is no swimming pool on campus."},
    )
    assert r.status_code == 200
    assert (
        lclient.get(f"/improvements/{p['id']}", headers=h(tenant)).json()["status"]
        == "deployed"
    )


def test_a_double_click_on_approve_runs_the_gate_once(lclient, assistant, tenant):
    assistant.turns.append(
        {
            "id": "t1",
            "conversation_id": "c",
            "question": "Is there a pool?",
            "answer": "",
            "trace_id": None,
            "outcome": "no_answer",
            "sources": [],
            "created_at": datetime.now(timezone.utc),
        }
    )
    lclient.post("/improvements/analyse", headers=h(tenant))
    [p] = proposals(lclient, tenant)
    body = {"decided_by": "officer", "note": "There is no swimming pool on campus."}
    assert (
        lclient.post(
            f"/improvements/{p['id']}/approve", headers=h(tenant), json=body
        ).status_code
        == 200
    )
    assert (
        lclient.post(
            f"/improvements/{p['id']}/approve", headers=h(tenant), json=body
        ).status_code
        == 409
    )


# ------------------------------------------------------------------ privacy (PRD 11.4)


def _rows(tenant, sql):
    with psycopg.connect(TEST_URL) as conn:
        conn.execute("select set_config('app.tenant_id', %s, false)", (tenant,))
        return conn.execute(sql).fetchall()


def test_signals_are_stored_without_contact_details(lclient, assistant, tenant):
    assistant.turns.append(
        {
            "id": "t1",
            "conversation_id": "c1",
            "question": "I am Priya Sharma, call me on 9876543210 about the hostel",
            "answer": "I don't know.",
            "outcome": "no_answer",
            "sources": [],
            "trace_id": None,
            "created_at": datetime.now(timezone.utc),
        }
    )
    event(
        lclient,
        tenant,
        1,
        "feedback.created",
        kind="thumb",
        value=-1,
        conversation_id="c1",
        comment="wrong, mail me at priya@example.com",
    )
    lclient.post("/improvements/analyse", headers=h(tenant))
    stored = " ".join(
        str(v)
        for r in _rows(tenant, "select question, comment from learn.signals")
        for v in r
    )
    assert "Priya" not in stored and "9876543210" not in stored
    assert "priya@example.com" not in stored
    assert "[name]" in stored and "[phone]" in stored and "[email]" in stored


def test_erasure_forgets_a_conversation_but_keeps_the_counts(
    lclient, assistant, tenant
):
    now = datetime.now(timezone.utc)
    for i, conv in enumerate(["gone", "gone", "kept"]):
        assistant.turns.append(
            {
                "id": f"t{i}",
                "conversation_id": conv,
                "question": "Is there a swimming pool on campus?",
                "answer": "I don't know.",
                "outcome": "no_answer",
                "sources": [],
                "trace_id": None,
                "created_at": now - timedelta(minutes=5 - i),
            }
        )
    lclient.post("/improvements/analyse", headers=h(tenant))
    (pool,) = proposals(lclient, tenant)
    assert pool["evidence_count"] == 3
    r = lclient.post(
        "/events",
        json={
            "event_id": 99,
            "type": "erasure.requested",
            "tenant_id": tenant,
            "data": {"session_id": "s", "conversation_ids": ["gone"]},
        },
    )
    assert r.json()["erased"]["signals"] == 2
    assert sorted(_rows(tenant, "select question from learn.signals")) == [
        ("Is there a swimming pool on campus?",),
        ("[erased]",),
        ("[erased]",),
    ]
    assert _rows(
        tenant, "select count(*) from learn.signals where conversation_id = 'gone'"
    ) == [(0,)]
    detail = lclient.get(f"/improvements/{pool['id']}", headers=h(tenant)).json()
    assert {e["conversation_id"] for e in detail["examples"]} == {"kept"}
    assert detail["evidence_count"] == 3  # counts stay, so impact does not jump
    # Delivery is at-least-once: the same event again is harmless.
    again = lclient.post(
        "/events",
        json={
            "event_id": 99,
            "type": "erasure.requested",
            "tenant_id": tenant,
            "data": {"session_id": "s", "conversation_ids": ["gone"]},
        },
    )
    assert again.json()["duplicate"] is True and again.json()["erased"]["signals"] == 0


# ------------------------------------------------------------------ groundedness is measured (PRD 20)


def test_an_answer_the_review_cannot_support_is_shown_to_the_approver(
    lclient, assistant, settings, tenant
):
    doc(
        lclient,
        tenant,
        "prospectus.md",
        "# Hostel fee\n\nThe hostel fee is 90000 per year.",
    )
    case(
        settings,
        tenant,
        "c1",
        "What is the hostel fee?",
        expected=["doc:prospectus.md"],
    )
    assistant.turns.append(
        {
            "id": "t1",
            "conversation_id": "c1",
            "question": "Is there a swimming pool on campus?",
            "answer": "I don't know.",
            "outcome": "no_answer",
            "sources": [],
            "trace_id": None,
            "created_at": datetime.now(timezone.utc),
        }
    )
    lclient.post("/improvements/analyse", headers=h(tenant))
    [p] = proposals(lclient, tenant)
    note = "There is an Olympic swimming pool on campus open all night."
    assistant.unsupported_texts.add(note)  # the judge finds no source stating this
    lclient.post(
        f"/improvements/{p['id']}/approve",
        headers=h(tenant),
        json={"decided_by": "officer", "note": note},
    )
    done = lclient.get(f"/improvements/{p['id']}", headers=h(tenant)).json()
    # One claim of two is within the judge's noise, so it does not block on its own; the
    # approver sees the unsupported sentence and can roll back.
    gate_result = done["eval"]["gate"]
    assert any("Olympic swimming pool" in w for w in gate_result["warnings"])


def test_unchanged_answers_are_not_reviewed_twice_and_a_missing_review_fails_the_gate(
    assistant,
):
    from knowledge_api import gate

    r1 = {"cited": ["doc:a"], "message_id": "m1"}
    r2 = {"cited": ["doc:a"], "message_id": "m2"}
    assistant.answers.update({"m1": "Fee is 90000.", "m2": "Fee is 90000."})
    cache: dict = {}
    assert gate.ground(assistant, "t", [(r1, "Fee is 90000.")], cache) is None
    assert gate.ground(assistant, "t", [(r2, "Fee is 90000.")], cache) is None
    assert assistant.reviewed == ["m1"] and r2["supported_claims"] == 1

    base = {"scores": {"groundedness": 1.0, "claims": 30}, "results": []}
    lower = {"scores": {"groundedness": 0.6, "claims": 30}, "results": []}
    noise = {"scores": {"groundedness": 28 / 30, "claims": 30}, "results": []}
    unmeasured = {"scores": {"groundedness": None}, "results": []}
    assert gate.compare(base, lower, 1.0, set())["reasons"] == [
        "groundedness fell from 100.0% to 60.0%"
    ]
    # Two claims of thirty is within the judge's noise: shown, not blocking.
    within = gate.compare(base, noise, 1.0, set())
    assert within["passed"] and "within the review's noise" in within["warnings"][0]
    assert gate.compare(base, unmeasured, 1.0, set())["reasons"] == [
        "groundedness could not be measured for the change"
    ]


def test_eval_run_reports_when_the_review_is_unavailable(
    settings, assistant, tenant, lclient
):
    from knowledge_api import gate
    from knowledge_api.assistant_client import AssistantError

    doc(
        lclient,
        tenant,
        "prospectus.md",
        "# Hostel fee\n\nThe hostel fee is 90000 per year.",
    )
    case(
        settings,
        tenant,
        "c1",
        "What is the hostel fee?",
        expected=["doc:prospectus.md"],
    )

    def down(*a):
        raise AssistantError("Assistant service: ConnectError")

    assistant.review = down
    run = gate.run_suite(settings, assistant, tenant, tenant, "adhoc", "test")
    assert run["scores"]["groundedness"] is None
    assert run["scores"]["review_error"] == "Assistant service: ConnectError"


def test_deployed_improvements_are_relayed_to_module_c_for_webhooks(
    lclient, assistant, settings, tenant, monkeypatch
):
    from knowledge_api import gate

    sent = []
    monkeypatch.setattr(
        gate, "notify", lambda s, t, kind, data: sent.append((t, kind, data))
    )
    assistant.turns.append(
        {
            "id": "t1",
            "conversation_id": "c1",
            "question": "Is there a swimming pool on campus?",
            "answer": "I don't know.",
            "outcome": "no_answer",
            "sources": [],
            "trace_id": None,
            "created_at": datetime.now(timezone.utc),
        }
    )
    lclient.post("/improvements/analyse", headers=h(tenant))
    [p] = proposals(lclient, tenant)
    lclient.post(
        f"/improvements/{p['id']}/approve",
        headers=h(tenant),
        json={
            "decided_by": "officer",
            "note": "There is no swimming pool on campus at present.",
        },
    )
    assert [(t, k) for t, k, _ in sent] == [(tenant, "improvement.deployed")]
    assert sent[0][2]["proposal_id"] == p["id"]


def test_relay_sends_the_event_with_module_as_key(settings, monkeypatch):
    from dataclasses import replace

    from knowledge_api import experience_client

    calls = []

    def post(url, json=None, headers=None, timeout=None):
        import httpx

        calls.append((url, json, headers))
        return httpx.Response(202, request=httpx.Request("POST", url))

    monkeypatch.setattr(experience_client.httpx, "post", post)
    s = replace(
        settings, experience_api_url="http://c.test", key_to_experience="k-a-to-c"
    )
    assert experience_client.notify(s, "t1", "kb.sync.completed", {"source_id": "s"})
    url, body, headers = calls[0]
    assert (
        url == "http://c.test/webhooks/events"
        and headers["X-Service-Key"] == "k-a-to-c"
    )
    assert body["type"] == "kb.sync.completed" and body["event_id"]
    # Not configured: nothing is sent and nothing fails.
    assert experience_client.notify(settings, "t1", "kb.sync.completed", {}) is False


def _thumbs_down(lclient, assistant, tenant, start, n, question, mid_prefix):
    src = [{"source_type": "document", "ref_id": "d1", "title": "fees.md"}]
    for i in range(n):
        mid = f"{mid_prefix}{i}"
        b_message(assistant, mid, question, "The fee is 250000 per year [1].", src)
        event(
            lclient,
            tenant,
            start + i,
            "feedback.created",
            message_id=mid,
            conversation_id=f"conv-{mid}",
            kind="thumb",
            value=-1,
            reason="wrong",
        )


def test_coordinated_thumbs_down_is_rate_limited_and_reported(
    lclient, assistant, settings, tenant
):
    # Twelve "wrong" ratings of one question within minutes: only the first five count.
    _thumbs_down(
        lclient, assistant, tenant, 100, 12, "What is the B.Tech fee?", "burst"
    )
    r = lclient.post("/improvements/analyse", headers=h(tenant)).json()
    assert r["proposals"]["suppressed_as_burst"] == 7
    [p] = proposals(lclient, tenant)
    assert p["evidence_count"] == 5
    rep = lclient.get("/reports/weekly", headers=h(tenant)).json()
    [a] = rep["feedback_anomalies"]
    assert a["question"] == "What is the B.Tech fee?" and a["suppressed"] == 7
    # a second run does not suppress the same signals again
    assert (
        lclient.post("/improvements/analyse", headers=h(tenant)).json()["proposals"][
            "suppressed_as_burst"
        ]
        == 0
    )


def test_burst_limit_is_per_question_and_per_hour(settings, tenant):
    from knowledge_api.db import tx

    t0 = datetime.now(timezone.utc) - timedelta(hours=5)
    with tx(settings.database_url, tenant) as cur:
        for i in range(8):  # one every 20 minutes: never more than 3 in an hour
            cur.execute(
                "insert into learn.signals (tenant_id, kind, source_ref, question, root_cause, occurred_at)"
                " values (%s, 'thumb_down', %s, 'Hostel fee?', 'hallucination', %s)",
                (tenant, f"slow{i}", t0 + timedelta(minutes=20 * i)),
            )
        for i in range(4):  # a different question in the same minute
            cur.execute(
                "insert into learn.signals (tenant_id, kind, source_ref, question, root_cause, occurred_at)"
                " values (%s, 'thumb_down', %s, 'Bus routes?', 'hallucination', %s)",
                (tenant, f"other{i}", t0),
            )
        assert (
            learning.limit_bursts(cur, limit=3) == 1
        )  # only the 4th "Bus routes?" rating


def test_peak_season_holds_lower_severity_proposals_without_escalating(
    lclient, settings, embedder, tenant
):
    from knowledge_api.db import Jsonb, tx

    with tx(settings.database_url, tenant) as cur:
        ids = {}
        for cause, severity in (("knowledge_gap", 2), ("hallucination", 3)):
            cur.execute(
                "insert into learn.proposals (tenant_id, kind, root_cause, rung, title, proposal, diff,"
                " severity, due_at) values (%s,'curated_answer',%s,1,%s,'p',%s,%s, now() - interval '1 day')"
                " returning id",
                (tenant, cause, cause, Jsonb({"question": "q"}), severity),
            )
            ids[cause] = cur.fetchone()["id"]
            cur.execute(
                "insert into learn.signals (tenant_id, kind, source_ref, question, root_cause, proposal_id)"
                " values (%s,'no_answer',%s,'q',%s,%s)",
                (tenant, cause, cause, ids[cause]),
            )
    r = lclient.put(
        "/learning/settings",
        headers=h(tenant),
        json={"peak_season": True, "updated_by": "admin"},
    )
    assert r.status_code == 200 and r.json()["peak_season"] is True
    with tx(settings.database_url, tenant) as cur:
        learning.refresh_pending(cur, datetime.now(timezone.utc))
        cur.execute("select root_cause, escalated from learn.proposals")
        escalated = {p["root_cause"]: p["escalated"] for p in cur.fetchall()}
    assert escalated == {"knowledge_gap": False, "hallucination": True}

    inbox = lclient.get("/improvements?status=pending", headers=h(tenant)).json()
    assert [p["root_cause"] for p in inbox["items"]] == ["hallucination"]
    assert inbox["peak_season"] and inbox["held"] == 1
    assert (
        len(
            proposals_all := lclient.get(
                "/improvements?status=pending&include_held=true", headers=h(tenant)
            ).json()["items"]
        )
        == 2
    ), proposals_all
    assert (
        lclient.get("/reports/weekly", headers=h(tenant)).json()["approval_backlog"][
            "held_for_peak_season"
        ]
        == 1
    )

    lclient.put("/learning/settings", headers=h(tenant), json={"peak_season": False})
    assert len(proposals(lclient, tenant)) == 2  # back after the peak
