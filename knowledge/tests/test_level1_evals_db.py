"""Level 1 completion, Evaluation Agent: catalogue-generated launch cases (F10.3), the voice vs
chat check (F2.10), rubric grading by the judge models with human spot checks, and the
model-comparison harness (PRD 20)."""

from datetime import datetime

import psycopg

from conftest import needs_db
from knowledge_api import catalogue_cases, judge

pytestmark = needs_db

PROGRAMS = (
    "code,name,aliases,department,level,duration_months,intake,eligibility,academic_year\n"
    "BTECH-CSE,B.Tech Computer Science,CSE,Computer Science,UG,48,120,PCM with 50%,2027-28\n"
    "MBA,Master of Business Administration,,Management,PG,24,60,,2027-28\n"
)
FEES = (
    "program_code,category,component,amount\n"
    "BTECH-CSE,general,tuition,250000\n"
    "BTECH-CSE,nri,tuition,412500.50\n"
    "BTECH-CSE,general,hostel,90000\n"
    "MBA,general,tuition,300000\n"
)
DATES = (
    "program_code,label,date,academic_year\n"
    ",Applications open,2027-01-15,2027-28\n"
    "MBA,Group discussion,2027-04-10,2027-28\n"
)


def h(tenant):
    return {"X-Tenant-Id": tenant}


def import_catalogue(client, tenant, programs=PROGRAMS, fees=FEES, dates=DATES):
    files = {"programs_csv": ("p.csv", programs)}
    if fees:
        files["fees_csv"] = ("f.csv", fees)
    if dates:
        files["dates_csv"] = ("d.csv", dates)
    r = client.post("/catalogue/import", headers=h(tenant), files=files)
    assert r.status_code == 200, r.text
    return r.json()


def approved_doc(client, tenant, name, text):
    d = client.post(
        "/kb/documents", headers=h(tenant), files={"file": (name, text.encode())}
    ).json()
    client.patch(
        f"/kb/documents/{d['id']}",
        headers=h(tenant),
        json={"action": "approve", "actor": "officer"},
    )


# ------------------------------------------------------------------ F10.3 catalogue cases


def test_catalogue_cases_quote_exact_values_and_follow_the_catalogue(lclient, tenant):
    r = import_catalogue(lclient, tenant)
    assert r["eval_cases"]["generated"] > 0
    cases = {
        c["ext_id"]: c
        for c in lclient.get("/evals/cases?source=catalogue", headers=h(tenant)).json()[
            "items"
        ]
    }
    fee = cases["cat-BTECH-CSE-fee-tuition-general"]
    assert fee["question"] == "What is the fee for B.Tech Computer Science?"
    assert fee["expected_facts"] == ["250000"] and fee["critical"]
    assert fee["expected_refs"] == ["catalogue:BTECH-CSE"]
    nri = cases["cat-BTECH-CSE-fee-tuition-nri"]
    assert "NRI category" in nri["question"] and nri["expected_facts"] == ["412500.5"]
    assert not nri["critical"]
    assert cases["cat-BTECH-CSE-fee-hostel-general"]["question"].startswith(
        "What is the hostel fee"
    )
    assert cases["cat-BTECH-CSE-intake"]["expected_facts"] == ["120"]
    assert (
        "cat-MBA-eligibility" not in cases
    )  # no eligibility in the catalogue: no case
    assert cases["cat-MBA-date-group-discussion"]["expected_refs"] == ["catalogue:MBA"]
    assert cases["cat-general-date-applications-open"]["expected_refs"] == [
        "catalogue:*"
    ]

    # The MBA is dropped from the next catalogue: its cases are switched off, not left to fail.
    import_catalogue(
        lclient,
        tenant,
        programs=PROGRAMS.splitlines()[0] + "\n" + PROGRAMS.splitlines()[1] + "\n",
        fees="program_code,category,component,amount\nBTECH-CSE,general,tuition,260000\n",
        dates=None,
    )
    now = {
        c["ext_id"]: c
        for c in lclient.get("/evals/cases?source=catalogue", headers=h(tenant)).json()[
            "items"
        ]
    }
    assert not any(k.startswith("cat-MBA") for k in now)
    assert now["cat-BTECH-CSE-fee-tuition-general"]["expected_facts"] == ["260000"]


def test_catalogue_cases_do_not_count_as_real_questions(lclient, tenant):
    import_catalogue(lclient, tenant)
    item = next(
        i
        for i in lclient.get("/tenant-pack/readiness", headers=h(tenant)).json()[
            "items"
        ]
        if i["key"] == "test_set"
    )
    assert not item["ok"]
    assert (
        item["detail"].startswith("0 questions")
        and "generated from the catalogue" in (item["detail"])
    )


def test_staff_cases_with_the_same_id_are_never_overwritten(lclient, settings, tenant):
    with psycopg.connect(settings.admin_database_url) as conn:
        conn.execute(
            "insert into learn.eval_cases (tenant_id, ext_id, question, kind, source)"
            " values (%s, 'cat-BTECH-CSE-intake', 'Our own wording', 'direct', 'staff')",
            (tenant,),
        )
    import_catalogue(lclient, tenant)
    with psycopg.connect(settings.admin_database_url) as conn:
        q = conn.execute(
            "select question, source from learn.eval_cases where ext_id = 'cat-BTECH-CSE-intake'"
            " and tenant_id = %s",
            (tenant,),
        ).fetchone()
    assert q == ("Our own wording", "staff")


def test_amount_text():
    assert catalogue_cases.amount_text(250000) == "250000"
    assert catalogue_cases.amount_text("250000.00") == "250000"
    assert catalogue_cases.amount_text("1250.50") == "1250.5"


def test_generated_cases_run_through_the_suite(lclient, assistant, tenant):
    import_catalogue(lclient, tenant)
    approved_doc(
        lclient,
        tenant,
        "fees.md",
        "# Fees\n\nThe fee for B.Tech Computer Science is 250000 per year.",
    )
    r = lclient.post("/evals/runs", headers=h(tenant), json={"label": "launch"})
    run = lclient.get(f"/evals/runs/{r.json()['id']}", headers=h(tenant)).json()
    assert run["status"] == "done"
    ids = {x["id"] for x in run["results"]}
    assert "cat-BTECH-CSE-fee-tuition-general" in ids
    # The fake Module B reports no timing; the real client adds latency_p50_s / p95 per run.
    assert run["scores"].get("latency_p50_s") is None


# ------------------------------------------------------------------ F2.10 voice vs chat


def eval_case(settings, tenant, ext, question, expected):
    with psycopg.connect(settings.admin_database_url) as conn:
        conn.execute(
            "insert into learn.eval_cases (tenant_id, ext_id, question, kind, expected_refs)"
            " values (%s,%s,%s,'direct',%s)",
            (tenant, ext, question, expected),
        )


def voice_setup(lclient, settings, tenant):
    approved_doc(
        lclient,
        tenant,
        "hostel.md",
        "# Hostel\n\nThe hostel has rooms for first year students and a mess.",
    )
    approved_doc(
        lclient,
        tenant,
        "library.md",
        "# Library\n\nThe library opens at eight in the morning on working days.",
    )
    for i, (q, ref) in enumerate(
        [
            ("Is there a hostel for first year students?", "doc:hostel.md"),
            ("Does the hostel have a mess?", "doc:hostel.md"),
            ("When does the library open in the morning?", "doc:library.md"),
            ("Is the library open on working days?", "doc:library.md"),
        ]
    ):
        eval_case(settings, tenant, f"v{i}", q, [ref])


def test_voice_much_worse_than_chat_moves_voice_to_the_answer_model(
    lclient, settings, assistant, tenant
):
    voice_setup(lclient, settings, tenant)
    assistant.voice_misses = {
        "library"
    }  # the small voice model fails half the questions
    r = lclient.post("/evals/voice", headers=h(tenant), json={})
    assert r.status_code == 202
    got = lclient.get(f"/evals/voice/{r.json()['check_id']}", headers=h(tenant)).json()
    assert got["status"] == "done"
    assert (got["chat_accuracy"], got["voice_accuracy"]) == (1.0, 0.5)
    assert got["gap_points"] == 50.0 and got["sent_to_assistant"]
    assert got["decision"]["use_answer_model"] is True
    assert assistant.voice_results[-1]["cases"] == 4
    assert assistant.channels.count("web_voice") == 4  # asked as the voice channel
    # Voice now runs on the answer model: a new check is recorded but not sent (no flip-flop).
    again = lclient.post("/evals/voice", headers=h(tenant), json={}).json()
    second = lclient.get(f"/evals/voice/{again['check_id']}", headers=h(tenant)).json()
    assert second["sent_to_assistant"] is False and "already runs" in second["note"]
    assert len(assistant.voice_results) == 1


def test_voice_as_good_as_chat_keeps_the_voice_model(
    lclient, settings, assistant, tenant
):
    voice_setup(lclient, settings, tenant)
    r = lclient.post("/evals/voice", headers=h(tenant), json={}).json()
    got = lclient.get(f"/evals/voice/{r['check_id']}", headers=h(tenant)).json()
    assert got["gap_points"] == 0.0 and got["decision"]["use_answer_model"] is False


def test_voice_check_without_cases_sends_nothing(lclient, assistant, tenant):
    r = lclient.post("/evals/voice", headers=h(tenant), json={}).json()
    got = lclient.get(f"/evals/voice/{r['check_id']}", headers=h(tenant)).json()
    assert got["sent_to_assistant"] is False and assistant.voice_results == []
    assert lclient.get("/evals/voice/nope", headers=h(tenant)).status_code == 404


# ------------------------------------------------------------------ PRD 20 judge grading


class FakeJudge:
    def __init__(self, name, verdicts=None, fail=False):
        self.name, self.verdicts, self.fail = name, verdicts or {}, fail
        self.prompts = []

    def grade(self, prompt):
        self.prompts.append(prompt)
        if self.fail:
            raise judge.JudgeError("judge down")
        for word, verdict in self.verdicts.items():
            if word in prompt:
                return {"verdict": verdict, "reason": "fake"}
        return {"verdict": "correct", "reason": "fake"}


def finished_run(lclient, settings, tenant):
    voice_setup(lclient, settings, tenant)
    r = lclient.post("/evals/runs", headers=h(tenant), json={})
    return r.json()["id"]


def test_parse_verdict_handles_reasoning_models():
    assert judge.parse_verdict(
        '<think>The fee is 250000 ... so it is right</think>\n{"verdict": "correct", "reason": "ok"}'
    ) == {"verdict": "correct", "reason": "ok"}
    assert judge.parse_verdict("Verdict: Incorrect, the date is wrong")["verdict"] == (
        "incorrect"
    )
    try:
        judge.parse_verdict("<think>hmm</think> I cannot tell")
    except judge.JudgeError:
        pass
    else:
        raise AssertionError("a reply without a verdict must be an error")


def test_scheduled_window():
    from datetime import timezone

    utc = timezone.utc
    assert judge.in_window(
        "22:00-06:00", "UTC", datetime(2026, 11, 2, 23, 0, tzinfo=utc)
    )
    assert judge.in_window(
        "22:00-06:00", "UTC", datetime(2026, 11, 2, 5, 59, tzinfo=utc)
    )
    assert not judge.in_window(
        "22:00-06:00", "UTC", datetime(2026, 11, 2, 12, 0, tzinfo=utc)
    )
    assert judge.in_window(
        "09:00-17:00", "UTC", datetime(2026, 11, 2, 10, 0, tzinfo=utc)
    )
    assert judge.in_window("", "UTC")  # no window configured: always open


def test_judges_grade_a_run_disagreements_go_to_a_person(lclient, settings, tenant):
    run_id = finished_run(lclient, settings, tenant)
    primary = FakeJudge("deepseek-14b", {"Does the hostel have a mess": "partly"})
    check = FakeJudge("deepseek-8b", {"Does the hostel have a mess": "incorrect"})
    out = judge.judge_run(settings, tenant, run_id, primary, check)
    assert out["judged"] == 4 and out["judge_disputed"] == 1
    assert (
        out["judge_accuracy"] == 1.0
    )  # 3 agreed "correct"; the disputed one has no verdict yet
    assert out["judge_agreement"] == 0.75
    assert "Expected facts" in primary.prompts[0] and "Answer:" in primary.prompts[0]
    run = lclient.get(f"/evals/runs/{run_id}", headers=h(tenant)).json()
    disputed = next(r for r in run["results"] if r["judge"]["disputed"])
    assert disputed["judge"]["spot_check"] and disputed["judge"]["final"] is None
    assert run["scores"]["judge_models"] == ["deepseek-14b", "deepseek-8b"]
    assert run["scores"]["accuracy"] is not None  # the deterministic scores stay

    r = lclient.post(
        f"/evals/runs/{run_id}/human-grade",
        headers=h(tenant),
        json={"case_id": disputed["id"], "verdict": "incorrect", "grader": "officer"},
    )
    assert r.status_code == 200
    assert r.json()["final"] == "incorrect" and r.json()["judge_accuracy"] == 0.75
    bad = lclient.post(
        f"/evals/runs/{run_id}/human-grade",
        headers=h(tenant),
        json={"case_id": "nope", "verdict": "correct", "grader": "officer"},
    )
    assert bad.status_code == 409


def test_a_judge_that_fails_is_reported_not_hidden(lclient, settings, tenant):
    run_id = finished_run(lclient, settings, tenant)
    out = judge.judge_run(
        settings,
        tenant,
        run_id,
        FakeJudge("deepseek-14b"),
        FakeJudge("deepseek-8b", fail=True),
    )
    assert out["judge_errors"] == 4 and out["judge_agreement"] is None
    assert out["judge_accuracy"] == 1.0  # from the judge that answered


def test_judging_is_refused_outside_the_window(lclient, settings, tenant):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from conftest import OPS
    from knowledge_api.main import create_app

    run_id = finished_run(lclient, settings, tenant)
    closed = replace(settings, judge_window="00:00-00:01")
    c = TestClient(
        create_app(closed, lclient.app.state.embedder, lclient.app.state.assistant),
        headers=OPS,
    )
    c.app.state.judges = (FakeJudge("a"), FakeJudge("b"))
    r = c.post(f"/evals/runs/{run_id}/judge", headers=h(tenant), json={})
    assert r.status_code == 409 and "scheduled window" in r.json()["detail"]
    # Production never forces the window.
    c.app.state.settings = replace(closed, app_env="production")
    r = c.post(f"/evals/runs/{run_id}/judge", headers=h(tenant), json={"force": True})
    assert r.status_code == 409
    # In development an operator may force it (here with fake judges).
    c.app.state.settings = closed
    r = c.post(f"/evals/runs/{run_id}/judge", headers=h(tenant), json={"force": True})
    assert r.status_code == 202
    run = lclient.get(f"/evals/runs/{run_id}", headers=h(tenant)).json()
    assert run["scores"]["judged"] == 4


# ------------------------------------------------------------------ PRD 20 model comparison


def test_model_comparison_runs_each_model_on_a_sandbox(
    lclient, settings, assistant, tenant
):
    voice_setup(lclient, settings, tenant)
    assistant.put_settings(
        tenant, {"sensitive_topics": [], "model_policy": {"primary": "x"}}
    )
    r = lclient.post(
        "/evals/comparisons",
        headers=h(tenant),
        json={
            "models": ["local/qwen2.5-14b-instruct", "local/llama3.1-8b"],
            "requested_by": "guide",
        },
    )
    assert r.status_code == 202
    cmp_ = lclient.get(f"/evals/comparisons/{r.json()['id']}", headers=h(tenant)).json()
    assert cmp_["status"] == "done"
    rows = {m["model"]: m for m in cmp_["report"]["models"]}
    assert set(rows) == {"local/qwen2.5-14b-instruct", "local/llama3.1-8b"}
    assert all(m["accuracy"] == 1.0 and m["cases"] == 4 for m in rows.values())
    assert all(m["answered_by_requested_model"] == 1.0 for m in rows.values())
    assert (
        cmp_["report"]["gpu_memory_measured"] is False
    )  # not claimed when not measured
    # The real college's settings are untouched and the sandboxes are gone.
    assert assistant.config[tenant]["model_policy"] == {"primary": "x"}
    assert [t for t, c in assistant.config.items() if c.get("sandbox")] == []
    with psycopg.connect(settings.admin_database_url) as conn:
        left = conn.execute("select count(*) from kb.sandboxes").fetchone()[0]
    assert left == 0
    listed = lclient.get("/evals/comparisons", headers=h(tenant)).json()["items"]
    assert listed[0]["status"] == "done"


def test_comparison_needs_two_different_models(lclient, tenant):
    for models in (["local/llama3.1-8b"], ["a", "a"], ["a", "b", "c", "d", "e", "f"]):
        r = lclient.post(
            "/evals/comparisons", headers=h(tenant), json={"models": models}
        )
        assert r.status_code == 422, models
