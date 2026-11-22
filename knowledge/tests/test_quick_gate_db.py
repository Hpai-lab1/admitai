"""An approval is tested on the questions it can affect, a few at a time; the whole set
runs nightly."""

from dataclasses import replace

import psycopg

from conftest import needs_db
from knowledge_api import gate
from test_level1_evals_db import import_catalogue

pytestmark = needs_db


def add_case(settings, tenant, ext, question, critical=False, refs=()):
    with psycopg.connect(settings.admin_database_url, autocommit=True) as conn:
        conn.execute(
            "insert into learn.eval_cases (tenant_id, ext_id, question, kind, expected_refs,"
            " expected_facts, critical, source) values (%s,%s,%s,'direct',%s,'{}',%s,'pack')",
            (tenant, ext, question, list(refs), critical),
        )


def test_a_quick_test_picks_the_programmes_and_similar_questions(
    lclient, settings, embedder, tenant
):
    import_catalogue(lclient, tenant)
    for i in range(30):
        add_case(settings, tenant, f"x{i:02d}", f"Is the library open on day {i}?")
    add_case(settings, tenant, "fee", "What is the CSE fee?", critical=True)
    add_case(settings, tenant, "mba", "MBA fee?", refs=["catalogue:MBA"])
    s = replace(settings, gate_mode="quick", quick_max_cases=8, quick_similar=3)
    p = {
        "id": "p",
        "title": "Expert correction: B.Tech CSE tuition",
        "diff": {"question": "What is the B.Tech CSE tuition fee?", "answer": "400000"},
    }
    chosen = gate.select_quick(s, embedder, tenant, p, None)
    cse = {c for c in chosen if "CSE" in c or c == "fee"}
    assert "fee" in chosen  # same programme, critical
    assert "mba" not in chosen or len(chosen) <= 8
    assert len(chosen) <= 8 and cse


def test_questions_are_asked_several_at_a_time_in_order(
    lclient, assistant, settings, tenant
):
    for i in range(6):
        add_case(settings, tenant, f"q{i}", f"Question number {i}?")
    run = gate.run_suite(
        settings,
        assistant,
        tenant,
        tenant,
        "candidate",
        "t",
        only={"q1", "q3", "q4"},
        parallel=3,
    )
    assert [r["id"] for r in run["results"]] == ["q1", "q3", "q4"]


def test_the_nightly_run_tests_everything_and_reports_what_got_worse(
    lclient, assistant, settings, tenant, monkeypatch
):
    add_case(settings, tenant, "c1", "Is there a hostel?", critical=True)
    told = []
    monkeypatch.setattr(gate, "_tell_staff", lambda *a: told.append(a))
    first = gate.nightly_full_test(settings, assistant, tenant)
    assert first["worse"] == [] and not told
    # last night it passed; tonight it fails
    with psycopg.connect(settings.admin_database_url, autocommit=True) as conn:
        conn.execute(
            "update learn.eval_runs set results = jsonb_set(results, '{0,passed}', 'true')"
            " where label = 'nightly'"
        )
    second = gate.nightly_full_test(settings, assistant, tenant)
    assert second["worse"] == ["c1"] and told


def test_a_quick_test_still_blocks_a_change_that_breaks_a_critical_question(
    lclient, assistant, settings, tenant
):
    """The critical-case scenario of the full gate, in quick mode."""
    from test_learning_db import b_message, case, doc, event, h, proposals

    object.__setattr__(lclient.app.state.settings, "gate_mode", "quick")
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
