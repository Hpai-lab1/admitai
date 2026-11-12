"""Level 1 completion, learning loop: implicit signals (F8.3), the approval SLA with a named
delegate, the monthly second review (8.3), the weekly digest (F7.7) and the quality report (F9.3)."""

from datetime import date, datetime, timedelta, timezone

import psycopg

from conftest import needs_db
from knowledge_api import learning, quality

pytestmark = needs_db


def h(tenant):
    return {"X-Tenant-Id": tenant}


def signal_event(client, tenant, n, kind, question, **extra):
    r = client.post(
        "/events",
        json={
            "event_id": f"sig-{n}",
            "type": "signal.logged",
            "tenant_id": tenant,
            "data": {
                "kind": kind,
                "question": question,
                "conversation_id": f"conv-{n}",
                **extra,
            },
        },
    )
    assert r.status_code == 200, r.text


def overdue_proposal(settings, tenant, department="Computer Science", days_late=1):
    from knowledge_api.db import Jsonb, tx

    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "insert into learn.proposals (tenant_id, kind, root_cause, rung, title, proposal, diff, department,"
            " severity, due_at, created_at) values (%s,'curated_answer','knowledge_gap',1,'Gap','p',%s,"
            " %s,2, now() - make_interval(days => %s), now() - interval '5 days') returning id",
            (tenant, Jsonb({"question": "q", "answer": None}), department, days_late),
        )
        pid = cur.fetchone()["id"]
        cur.execute(
            "insert into learn.signals (tenant_id, kind, source_ref, question, root_cause, proposal_id)"
            " values (%s,'no_answer',%s,'q','knowledge_gap',%s)",
            (tenant, f"x-{pid}", pid),
        )
    return pid


def row(settings, pid):
    with psycopg.connect(settings.admin_database_url) as conn:
        cur = conn.execute(
            "select escalation_level, assigned_to, department, escalated, due_at, note"
            " from learn.proposals where id = %s",
            (pid,),
        )
        cols = [c.name for c in cur.description]
        return dict(zip(cols, cur.fetchone()))


# ------------------------------------------------------------------ F8.3 implicit signals


def test_implicit_signals_are_triaged_and_weak_ones_only_counted():
    t = learning.triage
    assert t({"kind": "rephrase", "question": "hostel fee?"}) == (
        "knowledge_gap",
        "unreviewed",
    )
    assert t({"kind": "talk_to_human", "question": "When is counselling?"})[0] == (
        "knowledge_gap"
    )
    cited = [{"source_type": "document", "ref_id": "d1", "title": "Fees"}]
    assert (
        t(
            {
                "kind": "talk_to_human",
                "question": "How do I get a refund?",
                "outcome": "answered",
                "sources": cited,
            }
        )[0]
        == "policy"
    )
    for kind in ("abandoned", "voice_hangup", "barge_in"):
        assert t({"kind": kind, "question": "x"}) == (None, "unreviewed")


def test_one_rephrase_waits_two_similar_open_a_proposal(lclient, settings, tenant):
    signal_event(lclient, tenant, 1, "rephrase", "Is there a gym on campus?")
    lclient.post("/improvements/analyse", headers=h(tenant))
    assert (
        lclient.get("/improvements?status=pending", headers=h(tenant)).json()["items"]
        == []
    )
    # A second, similar question asked again by another visitor: now worth an expert's minute.
    signal_event(lclient, tenant, 2, "rephrase", "Is there a gym on the campus?")
    signal_event(lclient, tenant, 3, "barge_in", "Is there a gym?", channel="web_voice")
    lclient.post("/improvements/analyse", headers=h(tenant))
    items = lclient.get("/improvements?status=pending", headers=h(tenant)).json()[
        "items"
    ]
    assert len(items) == 1 and items[0]["root_cause"] == "knowledge_gap"
    assert (
        items[0]["evidence_count"] == 2
    )  # the barge-in is counted, not used as evidence
    report = lclient.get("/reports/weekly", headers=h(tenant)).json()
    assert report["implicit_signals"]["rephrase"] == 2
    assert report["implicit_signals"]["barge_in"] == 1
    with psycopg.connect(settings.admin_database_url) as conn:
        channel = conn.execute(
            "select channel from learn.signals where kind = 'barge_in' and tenant_id = %s",
            (tenant,),
        ).fetchone()[0]
    assert channel == "web_voice"


def test_unknown_event_types_are_still_refused(lclient, tenant):
    r = lclient.post(
        "/events",
        json={"event_id": 9, "type": "signal.other", "tenant_id": tenant, "data": {}},
    )
    assert r.status_code == 422


# ------------------------------------------------------------------ 8.3 named delegate, then College Admin


def test_overdue_proposal_goes_to_the_named_delegate_then_the_college_admin(
    lclient, settings, tenant
):
    from knowledge_api.db import tx

    r = lclient.put(
        "/learning/settings",
        headers=h(tenant),
        json={
            "delegates": {
                "default": "deputy.registrar@college.example",
                "Computer Science": "hod.cse@college.example",
            },
            "updated_by": "admin",
        },
    )
    assert r.status_code == 200
    assert r.json()["peak_season"] is False  # left out = unchanged
    pid = overdue_proposal(settings, tenant)
    now = datetime.now(timezone.utc)
    with tx(settings.database_url, tenant) as cur:
        learning.refresh_pending(cur, now)
    p = row(settings, pid)
    assert p["escalation_level"] == 1 and p["assigned_to"] == "hod.cse@college.example"
    assert (
        p["department"] == "Computer Science"
    )  # still the department's, not the admin's yet
    assert p["due_at"] > now  # a fresh SLA for the delegate
    # The delegate also lets it pass the new date: it goes to the College Admin.
    later = p["due_at"] + timedelta(hours=1)
    with tx(settings.database_url, tenant) as cur:
        learning.refresh_pending(cur, later)
    p = row(settings, pid)
    assert p["escalation_level"] == 2 and p["escalated"] and p["department"] is None
    item = lclient.get(f"/improvements/{pid}", headers=h(tenant)).json()
    assert item["escalation_level"] == 2 and item["diff"]["department"] == (
        "Computer Science"
    )


def test_department_without_a_delegate_uses_the_college_wide_one(
    lclient, settings, tenant
):
    from knowledge_api.db import tx

    lclient.put(
        "/learning/settings",
        headers=h(tenant),
        json={"delegates": {"default": "deputy.registrar@college.example"}},
    )
    pid = overdue_proposal(settings, tenant, department="Mechanical")
    with tx(settings.database_url, tenant) as cur:
        learning.refresh_pending(cur, datetime.now(timezone.utc))
    assert row(settings, pid)["assigned_to"] == "deputy.registrar@college.example"


def test_bad_delegate_settings_are_refused(lclient, tenant):
    r = lclient.put(
        "/learning/settings", headers=h(tenant), json={"delegates": {"": "someone"}}
    )
    assert r.status_code == 422
    assert (
        lclient.get("/learning/settings", headers=h(tenant)).json()["delegates"] == {}
    )


# ------------------------------------------------------------------ 8.3 monthly second review


def approved_proposals(settings, tenant, n, month_day: date, approver="officer"):
    from knowledge_api.db import Jsonb, tx

    ids = []
    with tx(settings.database_url, tenant) as cur:
        for i in range(n):
            cur.execute(
                "insert into learn.proposals (tenant_id, kind, root_cause, rung, title, proposal, diff,"
                " severity, due_at, status, decided_by, decided_at) values (%s,'curated_answer',"
                " 'knowledge_gap',1,%s,'p',%s,2, now(), 'deployed', %s, %s) returning id",
                (tenant, f"P{i}", Jsonb({}), approver, month_day),
            )
            ids.append(str(cur.fetchone()["id"]))
    return ids


def test_ten_percent_of_last_months_approvals_get_a_second_review(
    lclient, settings, tenant
):
    approved_proposals(settings, tenant, 25, date(2026, 10, 12))
    approved_proposals(settings, tenant, 4, date(2026, 11, 2))  # this month: not yet
    first = quality.sample_second_reviews(settings, tenant, today=date(2026, 11, 3))
    assert first == {"month": "2026-10", "sampled": 3, "approved": 25}  # ceil(2.5)
    again = quality.sample_second_reviews(settings, tenant, today=date(2026, 11, 20))
    assert again["sampled"] == 0 and again["already_done"]
    pending = lclient.get(
        "/improvements?second_review=pending", headers=h(tenant)
    ).json()["items"]
    assert len(pending) == 3 and all(p["second_review"] == "pending" for p in pending)

    pid = pending[0]["id"]
    same = lclient.post(
        f"/improvements/{pid}/second-review",
        headers=h(tenant),
        json={"verdict": "agree", "reviewer": "Officer"},
    )
    assert same.status_code == 409  # the approver cannot check their own approval
    ok = lclient.post(
        f"/improvements/{pid}/second-review",
        headers=h(tenant),
        json={"verdict": "disagree", "reviewer": "hod", "note": "Answer too vague"},
    )
    assert ok.status_code == 200 and ok.json()["second_review"] == "disagreed"
    twice = lclient.post(
        f"/improvements/{pid}/second-review",
        headers=h(tenant),
        json={"verdict": "agree", "reviewer": "hod"},
    )
    assert twice.status_code == 409
    month = lclient.get("/reports/weekly", headers=h(tenant)).json()["second_review"][
        "months"
    ][0]
    assert (month["month"], month["pending"], month["disagreed"]) == ("2026-10", 2, 1)


def test_a_small_month_still_gets_one_second_review(settings, tenant):
    approved_proposals(settings, tenant, 2, date(2026, 10, 30))
    r = quality.sample_second_reviews(settings, tenant, today=date(2026, 11, 1))
    assert r["sampled"] == 1


def test_sample_endpoint_and_nothing_to_sample(lclient, tenant):
    r = lclient.post("/improvements/second-reviews/sample", headers=h(tenant))
    assert r.status_code == 200 and r.json()["sampled"] == 0


# ------------------------------------------------------------------ F7.7 digest and F9.3 quality


def test_digest_lists_the_top_proposals_without_visitor_text(lclient, settings, tenant):
    from knowledge_api.db import Jsonb, tx

    with tx(settings.database_url, tenant) as cur:
        for i in range(12):
            cur.execute(
                "insert into learn.proposals (tenant_id, kind, root_cause, rung, title, proposal, diff,"
                " severity, due_at, impact_score, examples) values (%s,'curated_answer','knowledge_gap',1,"
                " %s,'p',%s,2, now() + make_interval(days => %s) + interval '1 hour', %s, %s)",
                (
                    tenant,
                    f"Proposal {i}",
                    Jsonb({}),
                    i - 2,  # two are overdue
                    i / 10,
                    Jsonb(
                        [{"question": "Visitor wrote: please ring my mobile tonight"}]
                    ),
                ),
            )
    d = lclient.get("/reports/digest", headers=h(tenant)).json()
    assert d["send"] and len(d["items"]) == 10
    assert d["items"][0]["title"] == "Proposal 11"  # highest impact first
    assert d["backlog"]["size"] == 12 and d["backlog"]["overdue"] == 2
    assert "2 overdue" in d["subject"] and "Proposal 11" in d["text"]
    assert "ring my mobile" not in str(
        d
    )  # visitors' questions never go into staff e-mail


def test_digest_titles_leave_out_the_visitors_question(lclient, settings, tenant):
    from knowledge_api.db import Jsonb, tx

    titles = [
        ("No reliable answer (3x): my son Ravi wants a hostel near the gate", None),
        ("Hard to understand (2x): explain fees again please", "Finance"),
        ("Reported wrong or outdated (1x): Fee notice 2027-28", None),
    ]
    with tx(settings.database_url, tenant) as cur:
        for title, dept in titles:
            cur.execute(
                "insert into learn.proposals (tenant_id, kind, root_cause, rung, title, proposal, diff,"
                " severity, due_at, department) values (%s,'curated_answer','knowledge_gap',1,%s,'p',%s,2,"
                " now() + interval '2 days', %s)",
                (tenant, title, Jsonb({}), dept),
            )
    d = lclient.get("/reports/digest", headers=h(tenant)).json()
    shown = {i["title"] for i in d["items"]}
    assert (
        shown
        == {
            "No reliable answer (3x)",
            "Hard to understand (2x) - Finance",
            "Reported wrong or outdated (1x): Fee notice 2027-28",  # a document title, not a visitor's
        }
    )
    assert "Ravi" not in str(d) and "explain fees" not in str(d)


def test_empty_digest_says_not_to_send(lclient, tenant):
    d = lclient.get("/reports/digest", headers=h(tenant)).json()
    assert d["send"] is False and d["items"] == []


def test_quality_report_by_week(lclient, settings, tenant):
    from knowledge_api.db import Jsonb, tx

    now = datetime.now(timezone.utc)
    with tx(settings.database_url, tenant) as cur:
        for i, (kind, verdict) in enumerate(
            [
                ("thumb_up", "unreviewed"),
                ("thumb_up", "unreviewed"),
                ("thumb_up", "unreviewed"),
                ("thumb_down", "wrong"),
                ("thumb_down", "correct_but_unwelcome"),
                ("rephrase", "unreviewed"),
            ]
        ):
            cur.execute(
                "insert into learn.signals (tenant_id, kind, source_ref, verdict, occurred_at)"
                " values (%s,%s,%s,%s,%s)",
                (tenant, kind, f"q{i}", verdict, now),
            )
        cur.execute(
            "insert into learn.proposals (tenant_id, kind, root_cause, rung, title, proposal, diff,"
            " severity, due_at, status, decided_at) values (%s,'curated_answer','knowledge_gap',1,"
            " 't','p',%s,2, now(), 'failed_gate', now())",
            (tenant, Jsonb({})),
        )
        cur.execute(
            "insert into learn.eval_runs (tenant_id, trigger, label, target_tenant, status, scores,"
            " finished_at) values (%s,'manual','adhoc',%s,'done',%s, now())",
            (tenant, tenant, Jsonb({"accuracy": 0.9, "cases": 20})),
        )
    q = lclient.get("/reports/quality?weeks=4", headers=h(tenant)).json()
    assert len(q["weeks"]) == 4  # quiet weeks are listed as zeros
    assert all(w["acceptance_rate"] is None for w in q["weeks"][:-1])
    week = q["weeks"][-1]
    assert (
        week["acceptance_rate"] == 0.75
    )  # 3 up, 1 down; "correct but unwelcome" excluded
    assert week["rephrase"] == 1 and week["proposals_opened"] == 1
    assert week["regressions_caught"] == 1
    assert q["eval_runs"][0]["accuracy"] == 0.9 and q["eval_runs"][0]["cases"] == 20
