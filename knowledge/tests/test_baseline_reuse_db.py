"""The gate reuses a baseline when nothing that can change an answer has changed (PRD 8.4)."""

from datetime import datetime, timedelta, timezone

import psycopg
from conftest import needs_db
from test_learning_db import case, doc, h, proposals

pytestmark = needs_db


def _gap(assistant, n, question):
    assistant.turns.append(
        {
            "id": f"t{n}",
            "conversation_id": f"c{n}",
            "question": question,
            "answer": "I don't know.",
            "outcome": "no_answer",
            "sources": [],
            "trace_id": None,
            "created_at": datetime.now(timezone.utc) - timedelta(minutes=5 - n),
        }
    )


def test_a_second_gate_starts_from_the_first_ones_measurement(
    lclient, assistant, settings, tenant
):
    assistant.versions = lambda t: {"version": "1", "agent": "a/1", "prompt": "p1"}
    doc(lclient, tenant, "hostel.md", "Hostel gates close at 21:30 every day.")
    case(settings, tenant, "c-hostel", "When do hostel gates close?")
    # (No out-of-scope case: the tests' word-hash search would match it to the new answers.)
    _gap(assistant, 1, "Is there an ATM on campus?")
    _gap(assistant, 2, "Is there a bus to the railway station?")
    lclient.post("/improvements/analyse", headers=h(tenant))
    first, second = sorted(proposals(lclient, tenant), key=lambda p: p["title"])

    def approve(p, note):
        before = len(assistant.asked)
        r = lclient.post(
            f"/improvements/{p['id']}/approve",
            headers=h(tenant),
            json={"decided_by": "officer", "note": note},
        )
        assert r.status_code == 200, r.text
        got = lclient.get(f"/improvements/{p['id']}", headers=h(tenant)).json()
        return got, len(assistant.asked) - before

    one, asked_first = approve(first, "Yes, there is an ATM beside the main gate.")
    g = (one["eval"] or {}).get("gate") or {}
    assert one["status"] == "deployed", {
        k: v for k, v in g.items() if k not in ("baseline", "candidate")
    }
    assert one["eval"]["gate"]["baseline_reused_from"] is None  # nothing to reuse yet
    with psycopg.connect(settings.admin_database_url) as conn:
        regular = conn.execute(
            "select count(*) from learn.eval_cases where tenant_id = %s and active",
            (tenant,),
        ).fetchone()[0]
    two, asked_second = approve(
        second, "Yes, a college bus leaves the main gate at 8:00."
    )
    assert two["status"] == "deployed"
    # The first gate's candidate measured the college as it then was, so it is the baseline.
    assert two["eval"]["gate"]["baseline_reused_from"] == one["eval"]["id"]
    # Only the candidate was asked (the regular cases plus this proposal's new one); without
    # reuse the baseline would have asked the regular cases again first.
    assert asked_second == regular + 1 < 2 * regular + 1

    # Module B changed (a new prompt): answers may differ, so nothing is reused.
    assistant.versions = lambda t: {"version": "1", "agent": "a/1", "prompt": "p2"}
    _gap(assistant, 3, "Is there a canteen?")
    lclient.post("/improvements/analyse", headers=h(tenant))
    third = next(p for p in proposals(lclient, tenant) if "canteen" in p["title"])
    three, _ = approve(third, "Yes, the canteen is open from 8:00 to 20:00.")
    assert three["eval"]["gate"]["baseline_reused_from"] is None
