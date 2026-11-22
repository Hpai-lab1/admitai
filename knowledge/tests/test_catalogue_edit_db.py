"""An admin who does not know the catalogue: a correction that disagrees with it is flagged, and
the fee itself can be changed from the console."""

import psycopg

from conftest import needs_db
from test_level1_evals_db import h, import_catalogue

pytestmark = needs_db


def fees(client, tenant):
    [p] = [
        p
        for p in client.get("/catalogue/programs?facts=true", headers=h(tenant)).json()[
            "items"
        ]
        if p["code"] == "BTECH-CSE"
    ]
    return {(f["category"], f["component"]): f for f in p["fees"]}


def test_a_correction_with_a_fee_the_catalogue_does_not_have_is_flagged(
    lclient, tenant
):
    import_catalogue(lclient, tenant)
    r = lclient.post(
        "/catalogue/check",
        headers=h(tenant),
        json={"text": "What is the CSE fee? The B.Tech CSE tuition fee is 4,00,000."},
    )
    [c] = r.json()["conflicts"]
    assert c["code"] == "BTECH-CSE" and c["quoted"] == ["400000"]
    assert {"general/tuition": "250000"}.items() <= {
        f"{f['category']}/{f['component']}": f["amount"] for f in c["fees"]
    }.items()
    agree = "The B.Tech CSE tuition fee is 250000 and the hostel 90000."
    assert (
        lclient.post(
            "/catalogue/check", headers=h(tenant), json={"text": agree}
        ).json()["conflicts"]
        == []
    )


def test_the_admin_changes_a_fee_and_everything_follows(lclient, settings, tenant):
    import_catalogue(lclient, tenant)
    with psycopg.connect(settings.admin_database_url, autocommit=True) as conn:
        # a hand-written test question that expects the old fee
        conn.execute(
            "insert into learn.eval_cases (tenant_id, ext_id, question, kind, expected_refs,"
            " expected_facts, critical, source) values (%s, 'd26', 'B.Tech CSE fee?', 'direct',"
            " '{}', '{250000}', true, 'pack')",
            (tenant,),
        )
    fee = fees(lclient, tenant)[("general", "tuition")]
    r = lclient.patch(
        f"/catalogue/fees/{fee['id']}",
        headers=h(tenant),
        json={"amount": "400000", "changed_by": "admin"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["old_amount"] == "250000" and r.json()["amount"] == "400000"
    assert fees(lclient, tenant)[("general", "tuition")]["amount"] == "400000"
    with psycopg.connect(settings.admin_database_url) as conn:
        facts = dict(
            conn.execute(
                "select ext_id, expected_facts from learn.eval_cases where tenant_id = %s"
                " and (ext_id = 'd26' or ext_id like '%%BTECH-CSE-fee-tuition-general')",
                (tenant,),
            ).fetchall()
        )
    assert all(v == ["400000"] for v in facts.values()) and len(facts) == 2
    assert (
        lclient.patch(
            "/catalogue/fees/00000000-0000-0000-0000-000000000000",
            headers=h(tenant),
            json={"amount": "1"},
        ).status_code
        == 404
    )


def test_a_fee_correction_changes_the_official_fee_and_can_be_undone(
    lclient, assistant, settings, tenant
):
    from knowledge_api import gate

    import_catalogue(lclient, tenant)
    text = (
        "For B.Tech CSE (2027-28), the tuition fee (general category) is 400000 INR"
        " per year; the hostel fee is 90000 INR per year."
    )
    c = lclient.post("/catalogue/check", headers=h(tenant), json={"text": text}).json()
    [ch] = c["fee_changes"]
    assert (ch["component"], ch["category"], ch["old"], ch["new"]) == (
        "tuition",
        "general",
        "250000",
        "400000",
    )
    # the candidate run expects the new fee where the old one was expected
    cases = [
        {"question": "B.Tech CSE tuition fee?", "expected_facts": ["250000"]},
        {"question": "MBA tuition fee?", "expected_facts": ["250000"]},
    ]
    names = {"BTECH-CSE": ["BTECH-CSE", "B.Tech Computer Science", "CSE"]}
    assert [c["expected_facts"] for c in gate._follow_fees(cases, [ch], names)] == [
        ["400000"],
        ["250000"],
    ]
    # deploying it changes the official fee, with no competing curated answer
    p = {
        "id": "x",
        "kind": "curated_answer",
        "diff": {"question": "B.Tech CSE fee?", "answer": text, "fee_changes": [ch]},
    }
    done = gate.apply_change(settings, None, assistant, tenant, p, None)
    assert done == {"fee_changes": [ch]}
    assert fees(lclient, tenant)[("general", "tuition")]["amount"] == "400000"
    with psycopg.connect(settings.admin_database_url) as conn:
        assert (
            conn.execute(
                "select count(*) from kb.curated_qa where tenant_id = %s", (tenant,)
            ).fetchone()[0]
            == 0
        )
    # rolling it back (or a failed test after "publish now") restores the fee
    gate.rollback_change(settings, assistant, tenant, done, "admin")
    assert fees(lclient, tenant)[("general", "tuition")]["amount"] == "250000"


def test_a_figure_that_cannot_be_tied_to_one_fee_changes_nothing(lclient, tenant):
    import_catalogue(lclient, tenant)
    r = lclient.post(
        "/catalogue/check",
        headers=h(tenant),
        json={"text": "B.Tech CSE costs 400000 INR in total."},
    ).json()
    assert r["conflicts"] and r["fee_changes"] == []


def test_a_paused_answer_comes_back_when_the_fee_changes_back(
    lclient, settings, tenant
):
    from decimal import Decimal

    from knowledge_api import catalogue, health
    from knowledge_api.db import tx

    import_catalogue(lclient, tenant)
    from test_learning_db import qa

    qa(
        lclient,
        tenant,
        "What is the B.Tech CSE tuition fee?",
        "The B.Tech CSE tuition fee is 250000 INR per year.",
    )
    fee = fees(lclient, tenant)[("general", "tuition")]
    with tx(settings.database_url, tenant) as cur:
        catalogue.set_fee(cur, fee["id"], Decimal("400000"))
    assert health.check_catalogue_conflicts(settings, tenant) == 1
    items = lclient.get("/improvements", headers=h(tenant)).json()["items"]
    [conflict] = [p for p in items if p["kind"] == "curated_conflict"]
    # staff read it: Indian grouping, programme names, no code dictionaries or 4E+5
    assert "2,50,000" in conflict["proposal"] and "4,00,000" in conflict["proposal"]
    assert "{" not in conflict["proposal"] and "E+" not in conflict["proposal"]
    # the fee goes back (a rollback): the answer is approved again, the proposal closed
    with tx(settings.database_url, tenant) as cur:
        catalogue.set_fee(cur, fee["id"], Decimal("250000"))
    health.check_catalogue_conflicts(settings, tenant)
    with psycopg.connect(settings.admin_database_url) as conn:
        assert conn.execute(
            "select status from kb.curated_qa where tenant_id = %s", (tenant,)
        ).fetchone() == ("approved",)
    assert not [
        p
        for p in lclient.get("/improvements?status=pending", headers=h(tenant)).json()[
            "items"
        ]
        if p["kind"] == "curated_conflict"
    ]
