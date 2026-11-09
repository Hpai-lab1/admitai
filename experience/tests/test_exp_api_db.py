"""End-to-end API tests against a real Postgres with RLS. Skipped unless TEST_DATABASE_URL (as the
admitai_app role) and TEST_ADMIN_DATABASE_URL (as an admin) point at a throw-away database."""

from __future__ import annotations

import os
import re
from dataclasses import replace

import psycopg
import pytest

from experience_api import config  # noqa: F401  (loads experience/.env)

TEST_URL = os.environ.get("TEST_DATABASE_URL")
TEST_ADMIN_URL = os.environ.get("TEST_ADMIN_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not (TEST_URL and TEST_ADMIN_URL),
    reason="needs TEST_DATABASE_URL and TEST_ADMIN_DATABASE_URL",
)

PASSWORD = "Test-Password-2026"
PK = "pk_demo_college_0001"


@pytest.fixture(scope="module")
def settings():
    from experience_api.config import Settings
    from experience_api.migrate import migrate
    from experience_api.seed import seed

    with psycopg.connect(TEST_ADMIN_URL, autocommit=True) as conn:
        conn.execute("drop schema if exists app cascade")
    migrate(TEST_ADMIN_URL)
    os.environ["DEMO_PASSWORD"] = PASSWORD
    seed(TEST_ADMIN_URL)
    return replace(
        Settings(),
        database_url=TEST_URL,
        admin_database_url=TEST_ADMIN_URL,
        jwt_secret="test-secret-" + "y" * 40,
        service_key="test-service-key-0123456789",
        knowledge_api_url="http://127.0.0.1:9/a",  # nothing listens: delivery fails and stays queued
        dev_echo_codes=True,
    )


@pytest.fixture(scope="module")
def client(settings):
    from fastapi.testclient import TestClient

    from experience_api.deps import settings_dep
    from experience_api.main import create_app

    app = create_app(settings)
    app.dependency_overrides[settings_dep] = lambda: settings
    return TestClient(app)


def login(client, email, tenant="demo-college", password=PASSWORD):
    from experience_api.security import totp_code

    r = client.post(
        "/auth/login", json={"tenant": tenant, "email": email, "password": password}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    if body["status"] != "ok":
        secret = body.get("secret") or _mfa_secret(email, tenant)
        r = client.post(
            "/auth/mfa",
            json={"mfa_token": body["mfa_token"], "code": totp_code(secret)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body


def _mfa_secret(email, tenant):
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        return conn.execute(
            "select p.mfa_secret from app.profiles p join app.tenants t on t.id = p.tenant_id"
            " where p.email = %s and t.slug = %s",
            (email, tenant),
        ).fetchone()[0]


def visitor(client, key=PK):
    r = client.post(
        "/widget/session", json={"public_key": key, "utm": {"utm_source": "test"}}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['session_token']}"}


def consent(client, h, purpose="contact"):
    r = client.post(
        "/consents",
        headers=h,
        json={"purpose": purpose, "granted": True, "text_version": "t1"},
    )
    assert r.status_code == 201, r.text


# ------------------------------------------------------------------ sign-in
def test_wrong_password_and_unknown_college(client):
    for tenant, pw in [
        ("demo-college", "Wrong-Password-1"),
        ("no-such-college", PASSWORD),
    ]:
        r = client.post(
            "/auth/login",
            json={"tenant": tenant, "email": "officer@example.com", "password": pw},
        )
        assert r.status_code == 401


def test_admin_must_enrol_mfa_then_is_challenged(client):
    r = client.post(
        "/auth/login",
        json={
            "tenant": "demo-college",
            "email": "admin@example.com",
            "password": PASSWORD,
        },
    )
    assert r.json()["status"] in ("mfa_enroll", "mfa_required")
    login(client, "admin@example.com")
    r = client.post(
        "/auth/login",
        json={
            "tenant": "demo-college",
            "email": "admin@example.com",
            "password": PASSWORD,
        },
    )
    assert r.json()["status"] == "mfa_required" and r.json()["secret"] is None


def test_refresh_token_rotates_and_old_one_dies(client):
    _, tokens = login(client, "officer@example.com")
    r = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    assert (
        client.post(
            "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        ).status_code
        == 401
    )


def test_unknown_widget_key(client):
    assert (
        client.post("/widget/session", json={"public_key": "pk_nope_00000"}).status_code
        == 404
    )


# ------------------------------------------------------------------ tenant isolation (RLS)
def test_other_college_sees_nothing_of_demo(client):
    demo_admin, _ = login(client, "admin@example.com")
    other_admin, _ = login(client, "admin@example.org", tenant="other-college")
    demo_handoffs = client.get("/handoffs", headers=demo_admin).json()
    assert demo_handoffs
    assert client.get("/handoffs", headers=other_admin).json() == []
    assert client.get("/leads", headers=other_admin).json()["total"] == 0
    assert (
        client.get(
            f"/handoffs/{demo_handoffs[0]['id']}", headers=other_admin
        ).status_code
        == 404
    )
    emails = {
        u["email"] for u in client.get("/admin/users", headers=other_admin).json()
    }
    assert emails == {"admin@example.org"}


def test_database_role_without_context_sees_no_rows():
    with psycopg.connect(TEST_URL) as conn:
        for table in (
            "tenants",
            "profiles",
            "leads",
            "handoffs",
            "consents",
            "audit_logs",
        ):
            assert (
                conn.execute(f"select count(*) from app.{table}").fetchone()[0] == 0
            ), table


def test_database_role_with_other_tenant_context_sees_no_demo_rows():
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        other = conn.execute(
            "select id from app.tenants where slug = 'other-college'"
        ).fetchone()[0]
    with psycopg.connect(TEST_URL) as conn:
        conn.execute(
            "select set_config('app.tenant_id', %s, false), set_config('app.role', 'college_admin', false)",
            (str(other),),
        )
        assert conn.execute("select count(*) from app.leads").fetchone()[0] == 0
        assert conn.execute("select count(*) from app.handoffs").fetchone()[0] == 0


# ------------------------------------------------------------------ roles and departments
def test_faculty_sees_only_their_department(client):
    cs, _ = login(client, "cs.faculty@example.com")
    mech, _ = login(client, "mech.faculty@example.com")
    cs_items = client.get("/handoffs", headers=cs).json()
    assert cs_items and {h["department"] for h in cs_items} == {"Computer Science"}
    assert (
        client.post(f"/handoffs/{cs_items[0]['id']}/claim", headers=mech).status_code
        == 404
    )


def test_officer_sees_admission_queue_only(client):
    officer, _ = login(client, "officer@example.com")
    assert {h["queue"] for h in client.get("/handoffs", headers=officer).json()} == {
        "admission"
    }


def test_marketing_cannot_open_queue_or_admin(client):
    mkt, _ = login(client, "marketing@example.com")
    assert client.get("/handoffs", headers=mkt).status_code == 403
    assert client.get("/admin/users", headers=mkt).status_code == 403
    assert client.get("/audit", headers=mkt).status_code == 403
    assert "handoffs" not in client.get("/analytics/overview", headers=mkt).json()


def test_claim_reply_resolve_and_correction(client):
    officer, _ = login(client, "officer@example.com")
    hid = client.get("/handoffs", headers=officer).json()[0]["id"]
    assert (
        client.post(f"/handoffs/{hid}/claim", headers=officer).json()["status"]
        == "claimed"
    )
    r = client.post(
        f"/handoffs/{hid}/reply",
        headers=officer,
        json={"body": "Yes, hostels open in July.", "as_correction": True},
    )
    assert r.status_code == 200
    detail = client.get(f"/handoffs/{hid}", headers=officer).json()
    assert [e["kind"] for e in detail["events"]][-1] == "replied"
    assert detail["assigned_name"] == "Omar Officer"
    assert (
        client.post(f"/handoffs/{hid}/resolve", headers=officer).json()["status"]
        == "resolved"
    )
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        n = conn.execute(
            "select count(*) from app.outbox_events where type = 'correction.submitted'"
            " and delivered_at is null"
        ).fetchone()[0]
    assert n >= 1  # Module A unreachable in tests: the event waits in the outbox


# ------------------------------------------------------------------ service calls from Module B
def test_service_key_required_and_sla_from_tenant(client, settings):
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        tid = str(
            conn.execute(
                "select id from app.tenants where slug = 'demo-college'"
            ).fetchone()[0]
        )
    body = {
        "conversation_id": "conv-b-1",
        "queue": "faculty",
        "department": "Civil",
        "kind": "handoff",
        "reason": "Low confidence",
        "context": {"ai_draft": "Not sure", "confidence": 0.2},
    }
    bad = {"X-Service-Key": "wrong", "X-Tenant-Id": tid}
    assert client.post("/handoffs", headers=bad, json=body).status_code == 401
    good = {"X-Service-Key": settings.service_key, "X-Tenant-Id": tid}
    r = client.post("/handoffs", headers=good, json=body)
    assert r.status_code == 201, r.text
    assert r.json()["queue"] == "faculty" and not r.json()["overdue"]
    body["department"] = "Astrology"
    assert client.post("/handoffs", headers=good, json=body).status_code == 422


# ------------------------------------------------------------------ consent, leads, minors
def test_no_lead_without_consent(client):
    v = visitor(client)
    r = client.post(
        "/leads", headers=v, json={"name": "Test Person", "phone": "+15550100099"}
    )
    assert r.status_code == 409


def test_adult_lead_then_callback_reaches_officer(client):
    v = visitor(client)
    consent(client, v)
    r = client.post(
        "/leads",
        headers=v,
        json={
            "name": "Test Adult",
            "phone": "+15550100010",
            "program_interest": ["B.Tech Civil"],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["source"]["utm"] == {"utm_source": "test"}
    r = client.post(
        "/callbacks",
        headers=v,
        json={"conversation_id": "conv-cb", "reason": "Call me about fees"},
    )
    assert r.status_code == 201, r.text
    officer, _ = login(client, "officer@example.com")
    queue = client.get("/handoffs", headers=officer).json()
    mine = [h for h in queue if h["conversation_id"] == "conv-cb"]
    assert mine and mine[0]["lead"]["phone"] == "+15550100010"
    assert (
        client.get("/notifications?unread_only=true", headers=officer).json()["unread"]
        >= 1
    )


def test_minor_contact_never_stored_and_guardian_required(client):
    v = visitor(client)
    consent(client, v)
    r = client.post(
        "/leads",
        headers=v,
        json={"name": "Young", "phone": "+15550100020", "is_minor": True},
    )
    assert r.status_code == 422
    assert (
        client.post(
            "/leads", headers=v, json={"name": "Young", "is_minor": True}
        ).status_code
        == 409
    )

    start = client.post(
        "/consents/guardian/start",
        headers=v,
        json={"channel": "email", "destination": "parent@example.com"},
    ).json()
    wrong = client.post(
        "/consents/guardian/verify",
        headers=v,
        json={
            "verification_id": start["verification_id"],
            "code": "000000" if start["dev_code"] != "000000" else "111111",
            "text_version": "t1",
        },
    )
    assert wrong.status_code == 401 and "4 tries left" in wrong.json()["detail"]
    ok = client.post(
        "/consents/guardian/verify",
        headers=v,
        json={
            "verification_id": start["verification_id"],
            "code": start["dev_code"],
            "text_version": "t1",
        },
    )
    assert ok.status_code == 201 and ok.json()[0]["given_by"] == "guardian"

    r = client.post(
        "/leads",
        headers=v,
        json={"name": "Young", "is_minor": True, "program_interest": ["B.Tech"]},
    )
    assert r.status_code == 201, r.text
    lead = r.json()
    assert lead["phone"] is None and lead["email"] is None
    assert lead["guardian_contact"] == {
        "channel": "email",
        "destination": "parent@example.com",
    }


def test_database_blocks_minor_lead_without_guardian():
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        tid, sid = conn.execute(
            "select tenant_id, id from app.visitor_sessions limit 1"
        ).fetchone()
        # the guardian trigger or the no-contact-for-minors check, whichever fires first
        with pytest.raises(
            (psycopg.errors.CheckViolation, psycopg.errors.RaiseException)
        ):
            conn.execute(
                "insert into app.leads (tenant_id, session_id, is_minor, phone) values (%s, %s, true, %s)",
                (tid, sid, "+15550100030"),
            )
        conn.rollback()
        conn.execute(
            "delete from app.consents where session_id = %s and given_by = 'guardian'",
            (sid,),
        )
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute(
                "insert into app.leads (tenant_id, session_id, is_minor) values (%s, %s, true)",
                (tid, sid),
            )
        conn.rollback()


def test_withdraw_anonymises_and_export_skips(client):
    v = visitor(client)
    consent(client, v)
    lead = client.post(
        "/leads", headers=v, json={"name": "Withdrawer", "email": "w@example.com"}
    ).json()
    r = client.post("/consents/withdraw", headers=v, json={"purposes": ["contact"]})
    assert r.json()["leads_anonymised"] == 1
    admin, _ = login(client, "admin@example.com")
    rows = client.get("/leads?include_anonymised=true", headers=admin).json()["items"]
    gone = next(x for x in rows if x["id"] == lead["id"])
    assert gone["anonymised"] and gone["name"] is None and gone["email"] is None
    csv_text = client.get("/leads/export", headers=admin).text
    assert lead["id"] not in csv_text and "Withdrawer" not in csv_text
    assert (
        client.patch(
            f"/leads/{lead['id']}", headers=admin, json={"stage": "contacted"}
        ).status_code
        == 409
    )


def test_lead_stage_change_is_audited(client):
    officer, _ = login(client, "officer@example.com")
    lead = client.get("/leads", headers=officer).json()["items"][0]
    r = client.patch(
        f"/leads/{lead['id']}", headers=officer, json={"stage": "contacted"}
    )
    assert r.status_code == 200 and r.json()["stage"] == "contacted"
    admin, _ = login(client, "admin@example.com")
    actions = [
        a["action"] for a in client.get("/audit?action=lead.", headers=admin).json()
    ]
    assert "lead.updated" in actions


# ------------------------------------------------------------------ feedback, audit
def test_feedback_goes_to_outbox(client):
    v = visitor(client)
    r = client.post(
        "/feedback",
        headers=v,
        json={
            "conversation_id": "c1",
            "message_id": "m1",
            "kind": "thumb",
            "value": -1,
            "reason": "outdated",
        },
    )
    assert r.status_code == 201
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        row = conn.execute(
            "select attempts, last_error from app.outbox_events where payload->>'feedback_id' = %s",
            (r.json()["id"],),
        ).fetchone()
    assert row[0] >= 1 and row[1]  # tried once, Module A unreachable, kept for retry


def test_audit_log_is_append_only():
    with psycopg.connect(TEST_URL) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("update app.audit_logs set action = 'x'")
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("update app.audit_logs set action = 'x'")


# ------------------------------------------------------------------ admin
def test_admin_creates_and_switches_off_staff(client):
    admin, _ = login(client, "admin@example.com")
    r = client.post(
        "/admin/users",
        headers=admin,
        json={
            "email": "new.faculty@example.com",
            "full_name": "New F",
            "role": "faculty",
            "departments": ["Civil"],
        },
    )
    assert r.status_code == 201, r.text
    user = r.json()
    h, _ = login(client, "new.faculty@example.com", password=user["temporary_password"])
    assert client.get("/auth/me", headers=h).json()["departments"] == ["Civil"]
    assert (
        client.patch(
            f"/admin/users/{user['id']}", headers=admin, json={"is_active": False}
        ).status_code
        == 200
    )
    assert client.get("/auth/me", headers=h).status_code == 401
    dup = client.post(
        "/admin/users",
        headers=admin,
        json={
            "email": "new.faculty@example.com",
            "full_name": "X",
            "role": "faculty",
            "departments": ["Civil"],
        },
    )
    assert dup.status_code == 409


def test_admin_cannot_lock_themselves_out(client):
    admin, tokens = login(client, "admin@example.com")
    me = client.get("/auth/me", headers=admin).json()
    r = client.patch(
        f"/admin/users/{me['id']}",
        headers=admin,
        json={"role": "faculty", "departments": ["Civil"]},
    )
    assert r.status_code == 409


def test_tenant_settings_round_trip(client):
    admin, _ = login(client, "admin@example.com")
    cfg = client.get("/admin/tenant", headers=admin).json()
    cfg["greeting"] = "Welcome to the demo college!"
    assert client.put("/admin/tenant", headers=admin, json=cfg).status_code == 200
    v = client.post("/widget/session", json={"public_key": PK}).json()
    assert v["tenant"]["greeting"] == "Welcome to the demo college!"


def test_approvals_filtered_by_role(client, monkeypatch):
    from experience_api.routes import approvals

    proposals = [
        {"id": "p1", "department": None, "impact_score": 0.5},
        {"id": "p2", "department": "Computer Science", "impact_score": 0.9},
        {"id": "p3", "department": "Mechanical", "impact_score": 0.7},
    ]
    decided = []
    monkeypatch.setattr(approvals, "knowledge_get", lambda *a: {"items": proposals})
    monkeypatch.setattr(
        approvals, "knowledge_post", lambda s, path, t, b: decided.append(path) or {}
    )
    cs, _ = login(client, "cs.faculty@example.com")
    assert [p["id"] for p in client.get("/approvals", headers=cs).json()["items"]] == [
        "p2"
    ]
    admin, _ = login(client, "admin@example.com")
    assert [
        p["id"] for p in client.get("/approvals", headers=admin).json()["items"]
    ] == ["p2", "p3", "p1"]
    r = client.post(
        "/approvals/decide",
        headers=cs,
        json={"ids": ["p2", "p3"], "decision": "approve"},
    ).json()
    assert [x["ok"] for x in r["results"]] == [True, False]
    assert decided == ["/improvements/p2/approve"]


def test_approvals_report_upstream_down(client):
    admin, _ = login(client, "admin@example.com")
    assert client.get("/approvals", headers=admin).status_code == 502


# ------------------------------------------------------------------ added: F8.3, F7.4, F9.1, 5.4
def test_conversation_events_and_prd_metrics(client):
    v = visitor(client)
    for kind, mid in [
        ("user_message", None),
        ("answer", "x-a1"),
        ("user_message", None),
        ("answer", "x-a2"),
    ]:
        r = client.post(
            "/conversation-events",
            headers=v,
            json={"conversation_id": "conv-metrics", "kind": kind, "message_id": mid},
        )
        assert r.status_code == 204, r.text
    admin, _ = login(client, "admin@example.com")
    c = client.get("/analytics/overview", headers=admin).json()["conversations"]
    assert c["qualifying"] >= 1 and c["resolved"] >= 1
    assert 0 <= c["resolution_rate"] <= 1
    # seeded data: conv-2 asked for a human, conv-3 rephrased at the end, conv-6 thumbs-down at the end
    assert c["handed_off"] >= 1 and c["abandoned"] >= 1


def test_visitor_cannot_log_events_for_another_tenant(client):
    with psycopg.connect(TEST_URL) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "insert into app.conversation_events (tenant_id, conversation_id, kind)"
                " values (gen_random_uuid(), 'x', 'answer')"
            )


def test_review_queue_is_scoped_by_department(client):
    cs, _ = login(client, "cs.faculty@example.com")
    mech, _ = login(client, "mech.faculty@example.com")
    officer, _ = login(client, "officer@example.com")
    cs_rows = client.get("/feedback", headers=cs).json()
    assert cs_rows and {r["department"] for r in cs_rows} == {"Computer Science"}
    assert client.get("/feedback", headers=mech).json() == []
    assert all(
        r["department"] is None for r in client.get("/feedback", headers=officer).json()
    )


def test_handoff_detail_shows_feedback(client):
    officer, _ = login(client, "officer@example.com")
    detail = next(
        h
        for h in client.get("/handoffs?status=active", headers=officer).json()
        if h["conversation_id"] == "demo-conv-1"
    )
    full = client.get(f"/handoffs/{detail['id']}", headers=officer).json()
    assert full["feedback"], "feedback for demo-conv-1 should be listed"


def test_break_glass_gates_personal_data(client):
    sa, _ = login(client, "platform@example.com")
    assert (
        client.get("/leads", headers=sa).json()["total"] == 0
    )  # no access without a reason
    assert (
        client.post("/break-glass", headers=sa, json={"reason": "short"}).status_code
        == 422
    )
    r = client.post(
        "/break-glass",
        headers=sa,
        json={"reason": "Investigating a complaint ticket 42"},
    )
    assert r.status_code == 201
    assert client.get("/leads", headers=sa).json()["total"] > 0
    admin, _ = login(client, "admin@example.com")
    kinds = [
        n["kind"] for n in client.get("/notifications", headers=admin).json()["items"]
    ]
    assert "break_glass.started" in kinds
    assert client.post("/break-glass/end", headers=sa).status_code == 204
    assert client.get("/leads", headers=sa).json()["total"] == 0
    officer, _ = login(client, "officer@example.com")
    assert (
        client.post(
            "/break-glass", headers=officer, json={"reason": "x" * 20}
        ).status_code
        == 403
    )


def test_callback_package_comes_from_module_b_for_own_session_only(client, monkeypatch):
    """PRD F3.3: the callback carries B's transcript, AI draft, sources, trace and confidence."""
    import jwt as pyjwt

    from experience_api.routes import handoffs

    v = visitor(client)
    sid = pyjwt.decode(v["Authorization"][7:], options={"verify_signature": False})[
        "sid"
    ]
    consent(client, v)
    assert (
        client.post(
            "/leads", headers=v, json={"name": "B Pack", "phone": "+15550100099"}
        ).status_code
        == 201
    )
    view = {
        "session_id": sid,
        "language": "hi",
        "messages": [
            {"sender": "user", "content": "Which labs does CSE have?"},
            {
                "sender": "assistant",
                "content": "I'm not certain about that detail.",
                "trace_id": "t-1",
                "confidence": 0.1,
                "sources": [
                    {
                        "title": "CSE curriculum",
                        "heading_path": "Labs",
                        "academic_year": "2027-28",
                        "url": None,
                    }
                ],
            },
        ],
    }
    monkeypatch.setattr(handoffs, "assistant_conversation", lambda s, t, c: view)
    r = client.post(
        "/callbacks",
        headers=v,
        json={
            "conversation_id": "conv-b",
            "queue": "faculty",
            "department": "Computer Science",
            "transcript": [{"role": "user", "text": "widget copy"}],
        },
    )
    assert r.status_code == 201, r.text
    faculty, _ = login(client, "cs.faculty@example.com")
    h = next(
        x
        for x in client.get("/handoffs", headers=faculty).json()
        if x["conversation_id"] == "conv-b"
    )
    assert h["language"] == "hi"
    assert h["context"]["ai_draft"] == "I'm not certain about that detail."
    assert (
        h["context"]["sources"][0]["section"] == "Labs"
        and h["context"]["trace_id"] == "t-1"
    )
    assert [t["text"] for t in h["context"]["transcript"]][
        0
    ] == "Which labs does CSE have?"

    # Someone else's conversation id: B's record is ignored, the widget's transcript is used.
    monkeypatch.setattr(
        handoffs,
        "assistant_conversation",
        lambda s, t, c: {**view, "session_id": "other"},
    )
    r = client.post(
        "/callbacks",
        headers=v,
        json={
            "conversation_id": "conv-b2",
            "transcript": [{"role": "user", "text": "widget copy"}],
        },
    )
    officer, _ = login(client, "officer@example.com")
    h = next(
        x
        for x in client.get("/handoffs", headers=officer).json()
        if x["conversation_id"] == "conv-b2"
    )
    assert (
        "ai_draft" not in h["context"]
        and h["context"]["transcript"][0]["text"] == "widget copy"
    )


def test_approval_history_and_admin_rollback(client, monkeypatch):
    from experience_api.routes import approvals

    items = [
        {
            "id": "p1",
            "status": "deployed",
            "department": None,
            "impact_score": 0.5,
            "created_at": "2026-10-27",
            "eval_run_id": "r1",
        },
        {
            "id": "p2",
            "status": "failed_gate",
            "department": "Mechanical",
            "impact_score": 0.4,
            "created_at": "2026-10-26",
        },
        {
            "id": "p3",
            "status": "pending",
            "department": None,
            "impact_score": 0.9,
            "created_at": "2026-10-28",
        },
    ]
    calls = []

    def fake_get(settings, path, tenant_id, params):
        if path == "/improvements/p1":
            return {"eval": {"passed": True, "gate": {"reasons": []}}}
        return {"items": items}

    def fake_post(settings, path, tenant_id, body):
        calls.append(path)
        return {"id": "p1", "status": "rolled_back"}

    monkeypatch.setattr(approvals, "knowledge_get", fake_get)
    monkeypatch.setattr(approvals, "knowledge_post", fake_post)
    cs, _ = login(client, "cs.faculty@example.com")
    assert (
        client.get("/approvals/history", headers=cs).json()["items"] == []
    )  # no CS items decided
    officer, _ = login(client, "officer@example.com")
    hist = client.get("/approvals/history", headers=officer).json()["items"]
    assert [p["id"] for p in hist] == ["p1"] and hist[0]["eval"]["passed"]
    assert client.post("/approvals/p1/rollback", headers=officer).status_code == 403
    admin, _ = login(client, "admin@example.com")
    r = client.post("/approvals/p1/rollback", headers=admin)
    assert r.status_code == 200 and calls == ["/improvements/p1/rollback"]


def test_module_b_opens_whatsapp_session_then_consent_lead_and_callback(
    client, settings
):
    svc = {
        "X-Service-Key": settings.service_key,
        "X-Tenant-Id": _tenant_id("demo-college"),
    }
    assert (
        client.post("/channels/sessions", json={"channel": "whatsapp"}).status_code
        == 401
    )
    r = client.post("/channels/sessions", headers=svc, json={"channel": "whatsapp"})
    assert r.status_code == 201, r.text
    s = {**svc, "X-Session-Id": r.json()["session_id"]}
    assert (
        client.post(
            "/consents",
            headers=s,
            json={
                "purpose": "whatsapp",
                "granted": True,
                "text_version": "wa-1",
                "channel": "whatsapp",
            },
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/consents",
            headers=s,
            json={
                "purpose": "contact",
                "granted": True,
                "text_version": "wa-1",
                "channel": "whatsapp",
            },
        ).status_code
        == 201
    )
    lead = client.post(
        "/leads", headers=s, json={"phone": "+15550109045", "conversation_id": "wa-x"}
    )
    assert lead.status_code == 201, lead.text
    h = client.post(
        "/handoffs",
        headers=s,
        json={
            "conversation_id": "wa-x",
            "session_id": r.json()["session_id"],
            "queue": "admission",
            "kind": "callback",
            "reason": "Asked for a person on WhatsApp",
            "preferred_channel": "whatsapp",
            "lead_id": lead.json()["id"],
        },
    )
    assert h.status_code == 201, h.text


def _tenant_id(slug):
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        return str(
            conn.execute(
                "select id from app.tenants where slug = %s", (slug,)
            ).fetchone()[0]
        )


# ------------------------------------------------------------------ platform onboarding (F10)
def _pack_zip(slug="riverside", staff=None, departments=("Commerce",)):
    import io as _io
    import zipfile as _zip

    import yaml as _yaml

    pack = {
        "college": {
            "slug": slug,
            "name": "Riverside College",
            "academic_year": "2027-28",
        },
        "branding": {
            "assistant_name": "Riverside Helper",
            "greeting": "Hi from Riverside!",
            "primary_color": "#225577",
        },
        "departments": list(departments),
        "launch": {"min_test_questions": 10},
        "staff": staff
        if staff is not None
        else [
            {
                "email": "head@riverside.example",
                "name": "Head",
                "role": "college_admin",
            },
            {
                "email": "office@riverside.example",
                "name": "Office",
                "role": "admission_officer",
            },
            {
                "email": "commerce@riverside.example",
                "name": "Prof",
                "role": "faculty",
                "departments": ["Commerce"],
            },
        ],
    }
    buf = _io.BytesIO()
    with _zip.ZipFile(buf, "w") as zf:
        zf.writestr("riverside/pack.yaml", _yaml.safe_dump(pack))
    return buf.getvalue()


class _FakeA:
    def __init__(self, readiness_ok=True, problems=None, run_passed=True):
        self.readiness_ok, self.problems, self.run_passed = (
            readiness_ok,
            problems,
            run_passed,
        )
        self.calls = []

    def __call__(self, settings, method, path, tenant, **kw):
        import httpx as _httpx

        self.calls.append((method, path))
        if path == "/tenant-pack":
            if self.problems:
                return _httpx.Response(
                    422,
                    json={
                        "detail": "; ".join(self.problems),
                        "problems": self.problems,
                    },
                )
            return _httpx.Response(
                200, json={"loaded": {"documents": 1}, "readiness": self._items()}
            )
        if path == "/tenant-pack/readiness":
            return _httpx.Response(200, json={"items": self._items()})
        if path == "/evals/runs":
            return _httpx.Response(202, json={"id": "run-1"})
        if path.startswith("/tenant-pack/launch/"):
            return _httpx.Response(
                200,
                json={
                    "status": "done",
                    "passed": self.run_passed,
                    "scores": {"accuracy": 0.9 if self.run_passed else 0.5},
                    "reasons": [] if self.run_passed else ["accuracy 50% is below 85%"],
                },
            )
        raise AssertionError(path)

    def _items(self):
        return [
            {
                "key": "fees",
                "label": "Fees for every program",
                "ok": self.readiness_ok,
                "detail": "" if self.readiness_ok else "BCom",
                "blocking": True,
            }
        ]


def _platform(client, monkeypatch, fake):
    from experience_api.routes import platform

    monkeypatch.setattr(platform, "_a", fake)
    monkeypatch.setattr(platform, "_b", lambda *a, **k: None)
    monkeypatch.setattr(platform, "_b_problems", lambda *a, **k: [])
    sa, _ = login(client, "platform@example.com")
    return sa


def _upload(client, h, data):
    return client.post(
        "/platform/tenants",
        headers=h,
        files={"file": ("pack.zip", data, "application/zip")},
    )


def test_only_the_super_admin_onboards_colleges(client, monkeypatch):
    _platform(client, monkeypatch, _FakeA())
    admin, _ = login(client, "admin@example.com")
    assert _upload(client, admin, _pack_zip("nope")).status_code == 403
    assert client.get("/platform/tenants", headers=admin).status_code == 403


def test_onboarding_a_college_from_its_pack_then_launch(client, monkeypatch):
    fake = _FakeA(readiness_ok=False)
    sa = _platform(client, monkeypatch, fake)
    r = _upload(client, sa, _pack_zip())
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["status"] == "onboarding" and t["public_key"].startswith("pk_riverside_")
    assert {s["email"] for s in t["staff"]} == {
        "head@riverside.example",
        "office@riverside.example",
        "commerce@riverside.example",
    }
    assert all(len(s["temporary_password"]) >= 12 for s in t["staff"])
    assert {i["key"]: i["ok"] for i in t["readiness"]} == {
        "fees": False,
        "officer": True,
        "faculty": True,
    }
    assert _upload(client, sa, _pack_zip()).status_code == 422  # slug taken

    # Sandbox preview (F10.4): the widget opens only for the college's own staff.
    assert (
        client.post("/widget/session", json={"public_key": t["public_key"]}).status_code
        == 403
    )
    officer, _ = login(client, "officer@example.com")  # demo-college staff
    assert (
        client.post(
            "/widget/session", json={"public_key": t["public_key"]}, headers=officer
        ).status_code
        == 403
    )
    pw = next(
        s["temporary_password"] for s in t["staff"] if s["role"] == "admission_officer"
    )
    own, _ = login(client, "office@riverside.example", tenant="riverside", password=pw)
    preview = client.post(
        "/widget/session", json={"public_key": t["public_key"]}, headers=own
    )
    assert (
        preview.status_code == 200
        and preview.json()["tenant"]["assistant_name"] == "Riverside Helper"
    )

    # Launch is blocked while a blocking readiness item is open, then gated by the test set.
    blocked = client.post(f"/platform/tenants/{t['id']}/launch", headers=sa)
    assert blocked.status_code == 409 and "Fees for every program: BCom" in str(
        blocked.json()
    )
    fake.readiness_ok, fake.run_passed = True, False
    assert (
        client.post(f"/platform/tenants/{t['id']}/launch", headers=sa).status_code
        == 202
    )
    failed = client.get(f"/platform/tenants/{t['id']}/launch", headers=sa).json()
    assert failed["status"] == "onboarding" and failed["reasons"] == [
        "accuracy 50% is below 85%"
    ]
    fake.run_passed = True
    assert (
        client.get(f"/platform/tenants/{t['id']}/launch", headers=sa).json()["status"]
        == "live"
    )
    assert (
        client.post("/widget/session", json={"public_key": t["public_key"]}).status_code
        == 200
    )  # public now
    listed = {
        x["slug"]: x["status"]
        for x in client.get("/platform/tenants", headers=sa).json()["items"]
    }
    assert listed["riverside"] == "live"


def test_bad_packs_create_nothing(client, monkeypatch):
    sa = _platform(
        client,
        monkeypatch,
        _FakeA(problems=["fees.csv line 2: unknown program_code 'X'"]),
    )
    r = _upload(client, sa, _pack_zip("broken"))
    assert r.status_code == 422 and "unknown program_code" in str(r.json())
    bad_staff = _pack_zip(
        "bad-staff",
        staff=[
            {"email": "x", "role": "wizard"},
            {"email": "f@x.example", "role": "faculty", "departments": ["Art"]},
        ],
    )
    problems = str(_upload(client, sa, bad_staff).json())
    assert (
        "role must be one of" in problems
        and "valid e-mail" in problems
        and "unknown department(s) Art" in problems
    )
    assert _upload(client, sa, b"not a zip").status_code == 422
    slugs = {
        x["slug"] for x in client.get("/platform/tenants", headers=sa).json()["items"]
    }
    assert not {"broken", "bad-staff"} & slugs


def test_super_admin_cannot_add_staff_to_a_live_college():
    demo = _tenant_id("demo-college")  # a live college
    with psycopg.connect(TEST_URL) as conn:
        conn.execute(
            "select set_config('app.tenant_id', %s, false), set_config('app.role', 'super_admin', false)",
            (demo,),
        )
        assert (
            conn.execute("select count(*) from app.tenants").fetchone()[0] >= 2
        )  # may list colleges
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "insert into app.profiles (tenant_id, email, full_name, role, password_hash)"
                " values (app.current_tenant(), 'sneaky@example.com', 'S', 'college_admin', 'x')"
            )


# ------------------------------------------------------------------ hardening (Day 7)
def test_knowledge_service_down_gives_a_readable_error(client):
    officer, _ = login(client, "officer@example.com")
    r = client.get(
        "/approvals", headers=officer
    )  # nothing listens on the test's Module A address
    assert (
        r.status_code == 502 and "Knowledge service unavailable" in r.json()["detail"]
    )


def test_suspended_college_widget_is_closed(client):
    with psycopg.connect(TEST_ADMIN_URL, autocommit=True) as conn:
        conn.execute(
            "update app.tenants set status = 'suspended' where slug = 'other-college'"
        )
    try:
        r = client.post("/widget/session", json={"public_key": "pk_demo_college_0002"})
        assert r.status_code == 404
    finally:
        with psycopg.connect(TEST_ADMIN_URL, autocommit=True) as conn:
            conn.execute(
                "update app.tenants set status = 'live' where slug = 'other-college'"
            )


def test_oversized_pack_is_refused(client, monkeypatch):
    from experience_api.routes import platform

    monkeypatch.setattr(platform, "MAX_PACK_BYTES", 1000)
    sa = _platform(client, monkeypatch, _FakeA())
    r = _upload(client, sa, b"x" * 2000)
    assert r.status_code == 413


def test_fixed_dev_sign_in_code_only_in_development(client, settings):
    from dataclasses import replace

    from experience_api.deps import settings_dep

    body = client.post(
        "/auth/login",
        json={
            "tenant": "demo-college",
            "email": "admin@example.com",
            "password": PASSWORD,
        },
    ).json()
    assert body["status"] in ("mfa_enroll", "mfa_required")
    app = client.app
    try:
        app.dependency_overrides[settings_dep] = lambda: replace(
            settings, dev_echo_codes=False, dev_mfa_code="123456"
        )
        r = client.post(
            "/auth/mfa", json={"mfa_token": body["mfa_token"], "code": "123456"}
        )
        assert r.status_code == 401  # not in development mode: refused
        app.dependency_overrides[settings_dep] = lambda: replace(
            settings, dev_echo_codes=True, dev_mfa_code="123456"
        )
        assert (
            client.post(
                "/auth/mfa", json={"mfa_token": body["mfa_token"], "code": "654321"}
            ).status_code
            == 401
        )
        r = client.post(
            "/auth/mfa", json={"mfa_token": body["mfa_token"], "code": "123456"}
        )
        assert r.status_code == 200 and r.json()["access_token"]
    finally:
        app.dependency_overrides[settings_dep] = lambda: settings


# ------------------------------------------------------------------ erasure on request (PRD 11.4)
class FakeModules:
    """Module B erases and names the conversations; Module A can fail the first erasure delivery."""

    def __init__(self, a_fails=0):
        self.a_fails, self.calls = a_fails, []

    def __call__(self, url, json=None, headers=None, timeout=None):
        import httpx as _httpx

        req = _httpx.Request("POST", url)
        self.calls.append((url.rsplit("/", 2)[-1], json))
        if url.endswith("/privacy/erase"):
            return _httpx.Response(
                200, json={"conversations": ["conv-1"], "messages": 4}, request=req
            )
        if self.a_fails and json["type"] == "erasure.requested":
            self.a_fails -= 1
            return _httpx.Response(503, request=req)
        return _httpx.Response(200, json={"ok": True}, request=req)


def _erasure_rows(session_id):
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        return conn.execute(
            "select payload, delivered_at is not null, attempts from app.outbox_events"
            " where type = 'erasure.requested' and payload->>'session_id' = %s",
            (session_id,),
        ).fetchall()


def test_visitor_erasure_clears_c_and_reaches_b_then_a(client, settings, monkeypatch):
    from experience_api import clients

    v = visitor(client)
    consent(client, v)
    lead = client.post(
        "/leads", headers=v, json={"name": "Eraser", "phone": "+15550100077"}
    ).json()
    client.post(
        "/feedback",
        headers=v,
        json={
            "conversation_id": "conv-1",
            "message_id": "m1",
            "kind": "thumb",
            "value": -1,
            "comment": "call me on 5550100123",
        },
    )
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        sid = str(
            conn.execute(
                "select session_id from app.leads where id = %s", (lead["id"],)
            ).fetchone()[0]
        )
    fake = FakeModules(a_fails=1)
    monkeypatch.setattr(clients.httpx, "post", fake)
    r = client.post("/privacy/erase", headers=v)
    assert r.status_code == 200, r.text
    assert r.json()["leads_anonymised"] == 1 and r.json()["queued"] is True
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        assert conn.execute(
            "select name, phone from app.leads where id = %s", (lead["id"],)
        ).fetchone() == (None, None)
        assert (
            conn.execute(
                "select count(*) from app.consents where session_id = %s"
                " and withdrawn_at is null",
                (sid,),
            ).fetchone()[0]
            == 0
        )
        assert conn.execute(
            "select comment from app.feedback where conversation_id = 'conv-1'"
        ).fetchall() == [(None,)]
    ((payload, delivered, attempts),) = _erasure_rows(sid)
    # Module B did its part and the ids were kept; Module A was down, so it waits for a retry.
    assert payload == {"session_id": sid, "conversation_ids": ["conv-1"]}
    assert not delivered
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        tenant_id = conn.execute(
            "select tenant_id from app.visitor_sessions where id = %s", (sid,)
        ).fetchone()[0]
    clients.dispatch_events(settings, tenant_id)
    ((_, delivered, _),) = _erasure_rows(sid)
    assert delivered
    # Module B was asked once; Module A got the conversation ids it needs.
    assert [c for c, _ in fake.calls].count("erase") == 1
    assert fake.calls[-1][1]["data"] == {
        "session_id": sid,
        "conversation_ids": ["conv-1"],
    }
    # A second request is harmless and queues nothing new.
    again = client.post("/privacy/erase", headers=v).json()
    assert again["queued"] is False and len(_erasure_rows(sid)) == 1


def test_admin_erases_a_lead_received_by_phone_and_others_may_not(client, monkeypatch):
    from experience_api import clients

    monkeypatch.setattr(clients.httpx, "post", FakeModules())
    v = visitor(client)
    consent(client, v)
    lead = client.post(
        "/leads", headers=v, json={"name": "Caller", "email": "caller@example.com"}
    ).json()
    officer, _ = login(client, "officer@example.com")
    assert client.post(f"/leads/{lead['id']}/erase", headers=officer).status_code == 403
    admin, _ = login(client, "admin@example.com")
    r = client.post(f"/leads/{lead['id']}/erase", headers=admin)
    assert r.status_code == 200 and r.json()["leads_anonymised"] == 1
    actions = [
        a["action"] for a in client.get("/audit?action=privacy.", headers=admin).json()
    ]
    assert "privacy.erasure_requested" in actions


def test_old_audit_rows_may_be_purged_but_recent_ones_stay():
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute(
                "delete from app.audit_logs where at > now() - interval '1 day'"
            )
    from experience_api.privacy import purge_old_audit

    assert purge_old_audit(TEST_ADMIN_URL) == 0


def test_service_call_with_an_unknown_session_is_a_clear_404(client, settings):
    import uuid

    r = client.post(
        "/consents",
        headers={
            "X-Service-Key": settings.service_key,
            "X-Tenant-Id": _demo_tenant(),
            "X-Session-Id": str(uuid.uuid4()),
        },
        json={"purpose": "contact", "granted": True, "text_version": "t1"},
    )
    assert r.status_code == 404 and r.json()["detail"] == "Unknown visitor session"


def _demo_tenant():
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        return str(
            conn.execute(
                "select id from app.tenants where slug = 'demo-college'"
            ).fetchone()[0]
        )


def test_whatsapp_conversations_count_in_the_dashboard(client, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from experience_api.routes import analytics

    now = datetime.now(timezone.utc)
    wa = {
        "conversation_id": "wa-metrics",
        "channel": "whatsapp",
        "user_msgs": 3,
        "last_user_at": (now - timedelta(minutes=30)).isoformat(),
        "last_answer_at": (now - timedelta(minutes=29)).isoformat(),
        "second_last_user_at": (now - timedelta(minutes=40)).isoformat(),
        "final_answer": "b-msg-9",
        "asked_human": False,
    }
    by_channel = [{"channel": "whatsapp", "answers": 3, "answered": 3, "no_answer": 0}]
    admin, _ = login(client, "admin@example.com")
    before = client.get("/analytics/overview", headers=admin).json()

    monkeypatch.setattr(
        analytics,
        "assistant_analytics",
        lambda s, t, since: {"conversations": [wa], "by_channel": by_channel},
    )
    after = client.get("/analytics/overview", headers=admin).json()
    c = after["conversations"]
    assert c["whatsapp"] == 1 and c["total"] == before["conversations"]["total"] + 1
    assert c["qualifying"] == before["conversations"]["qualifying"] + 1
    assert c["resolved"] == before["conversations"]["resolved"] + 1
    assert after["assistant"] == {"by_channel": by_channel}

    # Faculty keep the widget-only, department-scoped view.
    cs, _ = login(client, "cs.faculty@example.com")
    assert "assistant" not in client.get("/analytics/overview", headers=cs).json()

    # Module B down: said plainly, not shown as zero.
    monkeypatch.setattr(analytics, "assistant_analytics", lambda s, t, since: None)
    assert client.get("/analytics/overview", headers=admin).json()["assistant"] is None


def test_starting_chats_is_limited_per_ip_with_retry_after(settings):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from experience_api.deps import settings_dep
    from experience_api.main import create_app

    s = replace(settings, sessions_per_ip_minute=2)
    app = create_app(s)
    app.dependency_overrides[settings_dep] = lambda: s
    c = TestClient(app)
    codes = [
        c.post("/widget/session", json={"public_key": PK}).status_code for _ in range(3)
    ]
    assert codes == [200, 200, 429]
    r = c.post("/widget/session", json={"public_key": PK})
    assert r.headers["content-type"] == "application/problem+json"
    assert int(r.headers["retry-after"]) >= 1


# ------------------------------------------------------------------ outbound webhooks (PRD 12.2)
class Receiver:
    """Stands in for a college's CRM: records what arrives, answers with the given statuses."""

    def __init__(self, *statuses):
        self.statuses, self.got = list(statuses), []

    def __call__(
        self, url, content=None, headers=None, timeout=None, follow_redirects=True
    ):
        import httpx as _httpx

        assert follow_redirects is False
        self.got.append((url, content, headers))
        code = self.statuses.pop(0) if self.statuses else 200
        return _httpx.Response(code, request=_httpx.Request("POST", url))


def _deliver(settings, monkeypatch, receiver):
    from experience_api import webhooks

    monkeypatch.setattr(webhooks.httpx, "post", receiver)
    return webhooks.deliver(settings, _demo_tenant_uuid())


def _demo_tenant_uuid():
    import uuid as _uuid

    return _uuid.UUID(_demo_tenant())


def _hook(client, admin, events):
    r = client.post(
        "/webhooks",
        headers=admin,
        json={"url": "http://crm.example/hook", "events": events},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_lead_webhook_is_signed_and_retried(client, settings, monkeypatch):
    import hashlib
    import hmac as _hmac
    import json as _json

    admin, _ = login(client, "admin@example.com")
    hook = _hook(client, admin, ["lead.created"])
    assert hook["secret"].startswith("whsec_")
    listed = client.get("/webhooks", headers=admin).json()["items"]
    assert all("secret" not in i for i in listed)  # shown once, never again

    v = visitor(client)
    consent(client, v)
    lead = client.post(
        "/leads", headers=v, json={"name": "Hook Test", "email": "h@example.com"}
    ).json()

    first = Receiver(503)  # the CRM is down: retried later, not lost
    assert _deliver(settings, monkeypatch, first)["retry"] >= 1
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        conn.execute(
            "update app.webhook_deliveries set next_attempt_at = now() where status = 'pending'"
        )
    ok = Receiver()
    _deliver(settings, monkeypatch, ok)
    url, body, headers = next(
        g for g in ok.got if _json.loads(g[1])["type"] == "lead.created"
    )
    ts = headers["X-AdmitAI-Timestamp"]
    expected = _hmac.new(
        hook["secret"].encode(), f"{ts}.".encode() + body, hashlib.sha256
    )
    assert headers["X-AdmitAI-Signature"] == "v1=" + expected.hexdigest()
    event = _json.loads(body)
    assert (
        event["data"]["id"] == lead["id"] and event["data"]["email"] == "h@example.com"
    )
    # The retry carried the same event id, so the CRM can de-duplicate.
    assert headers["X-AdmitAI-Event-Id"] == event["id"]
    first_ids = {_json.loads(b)["id"] for _, b, _ in first.got}
    assert event["id"] in first_ids


def test_erasure_cancels_queued_personal_data_and_tells_the_crm(
    client, settings, monkeypatch
):
    import json as _json

    from experience_api import clients

    monkeypatch.setattr(clients.httpx, "post", FakeModules())
    admin, _ = login(client, "admin@example.com")
    _hook(client, admin, ["lead.created", "lead.updated"])
    v = visitor(client)
    consent(client, v)
    lead = client.post(
        "/leads", headers=v, json={"name": "Forget Me", "email": "f@example.com"}
    ).json()
    client.post("/privacy/erase", headers=v)
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        rows = conn.execute(
            "select type, status, payload from app.webhook_deliveries where lead_id = %s"
            " or payload->'data'->>'id' = %s order by id",
            (lead["id"], lead["id"]),
        ).fetchall()
    created = [r for r in rows if r[0] == "lead.created"]
    assert created and all(r[1] == "cancelled" and r[2] == {} for r in created)
    told = [r for r in rows if r[0] == "lead.updated" and r[1] == "pending"]
    assert told and told[-1][2]["data"] == {"id": lead["id"], "anonymised": True}
    assert "Forget Me" not in _json.dumps([r[2] for r in rows])


def test_webhook_urls_cannot_reach_the_platforms_own_network(settings):
    from dataclasses import replace

    from experience_api.webhooks import UnsafeUrl, check_url

    prod = replace(settings, app_env="production")
    for url in (
        "http://crm.example.com/hook",  # not https
        "https://127.0.0.1/hook",
        "https://localhost:8100/kb/search",
        "https://[fd00::5]/x",
        "https://169.254.169.254/latest/meta-data",
        "https://user:pw@example.com/",
    ):
        with pytest.raises(UnsafeUrl):
            check_url(url, prod)


def test_only_the_college_admin_manages_webhooks(client):
    officer, _ = login(client, "officer@example.com")
    assert client.get("/webhooks", headers=officer).status_code == 403
    assert (
        client.post(
            "/webhooks",
            headers=officer,
            json={"url": "http://x.example", "events": ["lead.created"]},
        ).status_code
        == 403
    )


def test_module_a_events_are_relayed_once_with_their_key(client, settings):
    import uuid as _uuid
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from experience_api.deps import settings_dep
    from experience_api.main import create_app

    s = replace(settings, key_from_knowledge="key-a-to-c")
    app = create_app(s)
    app.dependency_overrides[settings_dep] = lambda: s
    c = TestClient(app)
    admin, _ = login(c, "admin@example.com")
    _hook(c, admin, ["improvement.deployed"])
    body = {
        "event_id": str(_uuid.uuid4()),
        "type": "improvement.deployed",
        "data": {"proposal_id": "p1"},
    }
    h = {"X-Tenant-Id": _demo_tenant()}
    assert (
        c.post(
            "/webhooks/events", json=body, headers={**h, "X-Service-Key": "wrong"}
        ).status_code
        == 401
    )
    ok = c.post(
        "/webhooks/events", json=body, headers={**h, "X-Service-Key": "key-a-to-c"}
    )
    again = c.post(
        "/webhooks/events", json=body, headers={**h, "X-Service-Key": "key-a-to-c"}
    )
    assert (
        ok.json()["queued"] >= 1 and again.json()["queued"] == 0
    )  # idempotent by event id


def test_a_delivery_is_given_up_after_24_hours(client, settings, monkeypatch):
    admin, _ = login(client, "admin@example.com")
    hook = _hook(client, admin, ["lead.created"])
    client.post(f"/webhooks/{hook['id']}/test", headers=admin)
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        conn.execute(
            "update app.webhook_deliveries set created_at = now() - interval '25 hours'"
            " where endpoint_id = %s",
            (hook["id"],),
        )
    assert _deliver(settings, monkeypatch, Receiver(500, 500, 500))["failed"] >= 1
    rows = client.get(f"/webhooks/{hook['id']}/deliveries", headers=admin).json()[
        "items"
    ]
    assert rows[0]["status"] == "failed" and rows[0]["last_error"] == "HTTP 500"


def test_a_bad_model_policy_stops_onboarding_before_module_a_loads_anything(
    client, monkeypatch
):
    from experience_api.routes import platform

    fake = _FakeA()
    sa = _platform(client, monkeypatch, fake)
    monkeypatch.setattr(
        platform,
        "_b_problems",
        lambda *a, **k: ["model_policy.external must be 'none'"],
    )
    r = _upload(client, sa, _pack_zip())
    assert r.status_code == 422
    assert "model_policy.external must be 'none'" in r.json()["problems"]
    assert fake.calls == []  # Module A was never asked to load the pack


# ------------------------------------------------------------------ e-mail and overdue callbacks
class Mailbox:
    """Stands in for the SMTP server; `fail` makes the next sends raise."""

    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def __call__(self, settings, to, subject, body):
        if self.fail:
            raise OSError("connection refused")
        self.sent.append({"to": to, "subject": subject, "body": body})

    def to(self, addr):
        return [m for m in self.sent if m["to"] == addr]


def _mail_client(settings, monkeypatch, **changes):
    from fastapi.testclient import TestClient

    from experience_api import messaging
    from experience_api.deps import settings_dep
    from experience_api.main import create_app

    s = replace(
        settings, smtp_host="smtp.test", smtp_from="admissions@example.com", **changes
    )
    box = Mailbox()
    monkeypatch.setattr(messaging, "SEND", box)
    app = create_app(s)
    app.dependency_overrides[settings_dep] = lambda: s
    return TestClient(app), s, box


def _outbox(where="true", params=()):
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        conn.row_factory = psycopg.rows.dict_row
        return conn.execute(
            f"select * from app.message_outbox where {where} order by id", params
        ).fetchall()


def _adult_with_callback(client, email, conv):
    v = visitor(client)
    consent(client, v)
    r = client.post(
        "/leads",
        headers=v,
        json={"name": "Mail Test", "email": email, "program_interest": ["B.Tech"]},
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/callbacks",
        headers=v,
        json={"conversation_id": conv, "reason": "Fees", "preferred_time": "evenings"},
    )
    assert r.status_code == 201, r.text
    return v, r.json()["handoff_id"]


def test_guardian_code_is_emailed_and_stored_nowhere(settings, monkeypatch):
    client, _, box = _mail_client(settings, monkeypatch, dev_echo_codes=False)
    v = visitor(client)
    r = client.post(
        "/consents/guardian/start",
        headers=v,
        json={"channel": "email", "destination": "mailparent@example.com"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["dev_code"] is None and "by e-mail" in r.json()["message"]
    (mail,) = box.to("mailparent@example.com")
    code = re.search(r"\b(\d{6})\b", mail["body"]).group(1)
    assert not _outbox("destination = %s", ("mailparent@example.com",))
    ok = client.post(
        "/consents/guardian/verify",
        headers=v,
        json={
            "verification_id": r.json()["verification_id"],
            "code": code,
            "text_version": "t1",
        },
    )
    assert ok.status_code == 201, ok.text


def test_guardian_code_is_refused_when_nothing_can_send_it(settings, monkeypatch):
    from fastapi.testclient import TestClient

    from experience_api.deps import settings_dep
    from experience_api.main import create_app

    s = replace(settings, dev_echo_codes=False)
    app = create_app(s)
    app.dependency_overrides[settings_dep] = lambda: s
    plain = TestClient(app)
    v = visitor(plain)
    for channel, dest in (("email", "p@example.com"), ("whatsapp", "+15550100099")):
        r = plain.post(
            "/consents/guardian/start",
            headers=v,
            json={"channel": channel, "destination": dest},
        )
        assert r.status_code == 503, r.text
    # an SMTP failure is reported, not hidden
    client, _, box = _mail_client(settings, monkeypatch, dev_echo_codes=False)
    box.fail = True
    r = client.post(
        "/consents/guardian/start",
        headers=visitor(client),
        json={"channel": "email", "destination": "p@example.com"},
    )
    assert r.status_code == 503 and "try again" in r.json()["detail"]


def test_callback_acknowledged_staff_told_and_queue_forgets_after_sending(
    settings, monkeypatch
):
    from experience_api import messaging

    client, s, box = _mail_client(settings, monkeypatch)
    _, hid = _adult_with_callback(client, "ack.adult@example.com", "conv-mail-ack")
    queued = _outbox("handoff_id = %s", (hid,))
    assert {m["template"] for m in queued} == {"callback_ack", "callback_created"}
    staff_mail = next(m for m in queued if m["template"] == "callback_created")
    assert staff_mail["audience"] == "admission" and "Fees" not in staff_mail["body"]

    messaging.deliver(s, _demo_tenant_uuid())
    (ack,) = box.to("ack.adult@example.com")
    assert "evenings" in ack["body"] and "UTC" in ack["body"]
    assert box.to("officer@example.com")
    for m in _outbox("handoff_id = %s", (hid,)):
        assert m["status"] == "sent" and m["body"] is None and m["destination"] is None


def test_smtp_failure_is_retried_later(settings, monkeypatch):
    from experience_api import messaging

    client, s, box = _mail_client(settings, monkeypatch)
    _, hid = _adult_with_callback(client, "retry.adult@example.com", "conv-mail-retry")
    box.fail = True
    assert messaging.deliver(s, _demo_tenant_uuid())["retry"] >= 1
    rows = _outbox("handoff_id = %s", (hid,))
    assert all(m["status"] == "pending" and m["attempts"] == 1 for m in rows)
    assert all(m["last_error"] == "OSError" and m["body"] for m in rows)
    box.fail = False
    assert (
        messaging.deliver(s, _demo_tenant_uuid())["sent"] == 0
    )  # not due yet (backoff)


def test_overdue_callback_is_escalated_once_and_applicant_told(settings, monkeypatch):
    from experience_api import messaging

    client, s, box = _mail_client(settings, monkeypatch)
    _, hid = _adult_with_callback(client, "late.adult@example.com", "conv-mail-late")
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        conn.execute(
            "update app.handoffs set sla_due_at = now() - interval '1 hour' where id = %s",
            (hid,),
        )
    assert messaging.escalate_overdue(s, _demo_tenant_uuid()) >= 1
    assert messaging.escalate_overdue(s, _demo_tenant_uuid()) == 0  # once only
    templates = [m["template"] for m in _outbox("handoff_id = %s", (hid,))]
    assert {"callback_overdue", "callback_escalated", "callback_delayed"} <= set(
        templates
    )

    admin, _ = login(client, "admin@example.com")
    kinds = {
        n["kind"] for n in client.get("/notifications", headers=admin).json()["items"]
    }
    assert "callback.escalated" in kinds
    detail = client.get(f"/handoffs/{hid}", headers=admin).json()
    assert any(e["kind"] == "escalated" for e in detail["events"])

    messaging.deliver(s, _demo_tenant_uuid())
    mails = box.to("late.adult@example.com")  # the acknowledgement, then the apology
    assert any("sorry" in m["body"].lower() for m in mails)
    assert box.to("admin@example.com")


def test_staff_reply_is_emailed_only_when_asked_and_possible(settings, monkeypatch):
    client, _, _ = _mail_client(settings, monkeypatch)
    _, hid = _adult_with_callback(client, "reply.adult@example.com", "conv-mail-reply")
    officer, _ = login(client, "officer@example.com")
    r = client.post(
        f"/handoffs/{hid}/reply", headers=officer, json={"body": "Notes only"}
    )
    assert r.status_code == 200 and not _outbox(
        "template = 'staff_reply' and handoff_id = %s", (hid,)
    )
    r = client.post(
        f"/handoffs/{hid}/reply",
        headers=officer,
        json={"body": "The hostel fee is in the circular.", "send_email": True},
    )
    assert r.status_code == 200, r.text
    (m,) = _outbox("template = 'staff_reply' and handoff_id = %s", (hid,))
    assert m["destination"] == "reply.adult@example.com" and "hostel fee" in m["body"]
    events = client.get(f"/handoffs/{hid}", headers=officer).json()["events"]
    assert any(e["kind"] == "emailed" for e in events)

    # a phone-only applicant cannot be e-mailed: say so, save nothing
    v = visitor(client)
    consent(client, v)
    client.post(
        "/leads", headers=v, json={"name": "Phone Only", "phone": "+15550100031"}
    )
    hid2 = client.post(
        "/callbacks",
        headers=v,
        json={"conversation_id": "conv-mail-phone", "reason": "Fees"},
    ).json()["handoff_id"]
    r = client.post(
        f"/handoffs/{hid2}/reply",
        headers=officer,
        json={"body": "x", "send_email": True},
    )
    assert r.status_code == 409 and "phone" in r.json()["detail"]


def test_withdrawing_consent_cancels_queued_mail(settings, monkeypatch):
    client, _, _ = _mail_client(settings, monkeypatch)
    v, hid = _adult_with_callback(client, "gone.adult@example.com", "conv-mail-gone")
    r = client.post("/consents/withdraw", headers=v, json={"purposes": ["contact"]})
    assert r.status_code == 200, r.text
    (ack,) = _outbox("template = 'callback_ack' and handoff_id = %s", (hid,))
    assert (
        ack["status"] == "cancelled"
        and ack["destination"] is None
        and ack["body"] is None
    )


def test_only_the_college_admin_switches_peak_season(client, monkeypatch):
    from experience_api.routes import approvals

    calls = []
    monkeypatch.setattr(
        approvals,
        "knowledge_get",
        lambda *a: {"items": [], "peak_season": True, "held": 4},
    )
    monkeypatch.setattr(
        approvals,
        "knowledge_post",
        lambda s, path, t, b, method="POST": calls.append((method, path, b))
        or {"peak_season": b["peak_season"]},
    )
    officer, _ = login(client, "officer@example.com")
    inbox = client.get("/approvals", headers=officer).json()
    assert inbox["peak_season"] is True and inbox["held"] == 4
    r = client.put(
        "/approvals/peak-season", headers=officer, json={"peak_season": False}
    )
    assert r.status_code == 403 and calls == []
    admin, _ = login(client, "admin@example.com")
    r = client.put("/approvals/peak-season", headers=admin, json={"peak_season": False})
    assert r.status_code == 200 and calls[0][:2] == ("PUT", "/learning/settings")
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        assert (
            conn.execute(
                "select count(*) from app.audit_logs where action = 'improvement.peak_season'"
            ).fetchone()[0]
            == 1
        )


# ------------------------------------------------------------------ Level 1 completion (WhatsApp,
# implicit signals, weekly digest, second review, quality and topics dashboards)
class FakeB:
    """Stands in for Module B's service endpoints called by assistant_post."""

    def __init__(self):
        self.calls, self.refuse, self.down = [], None, False

    def __call__(self, settings, path, tenant_id, body):
        from experience_api.clients import AssistantRefused, UpstreamError

        self.calls.append((path, body))
        if self.down:
            raise UpstreamError("Assistant service unavailable (ConnectError)")
        if self.refuse:
            raise AssistantRefused(409, self.refuse)
        if path.endswith("/guardian-code"):
            return {
                "queued": True,
                "display_number": "+15550100000",
                "expires_at": body["expires_at"],
            }
        return {"sent": "text"}


def _wa_client(settings, monkeypatch, **changes):
    from experience_api import clients
    from experience_api.routes import consents_leads

    client, s, box = _mail_client(
        settings, monkeypatch, key_to_assistant="test-key-c-to-b", **changes
    )
    fake = FakeB()
    monkeypatch.setattr(clients, "assistant_post", fake)
    monkeypatch.setattr(consents_leads, "assistant_post", fake)
    return client, s, box, fake


def _wa_callback(client, phone, conv, email=None):
    v = visitor(client)
    consent(client, v)
    lead = {"name": "WhatsApp Test", "phone": phone, "program_interest": ["B.Tech"]}
    if email:
        lead["email"] = email
    r = client.post("/leads", headers=v, json=lead)
    assert r.status_code == 201, r.text
    r = client.post(
        "/callbacks",
        headers=v,
        json={
            "conversation_id": conv,
            "reason": "Fees",
            "preferred_channel": "whatsapp",
        },
    )
    assert r.status_code == 201, r.text
    return v, r.json()["handoff_id"]


def test_guardian_code_by_whatsapp_is_held_by_module_b_and_stored_nowhere(
    settings, monkeypatch
):
    client, _, _, fake = _wa_client(settings, monkeypatch, dev_echo_codes=False)
    v = visitor(client)
    consent(client, v)
    r = client.post(
        "/consents/guardian/start",
        headers=v,
        json={"channel": "whatsapp", "destination": "+15550100041"},
    )
    assert r.status_code == 201, r.text
    start = r.json()
    assert start["dev_code"] is None
    assert "+15550100000" in start["message"] and "+15550100041" in start["message"]
    ((path, sent),) = fake.calls
    assert path == "/channels/whatsapp/guardian-code"
    assert sent["destination"] == "+15550100041" and re.fullmatch(
        r"\d{6}", sent["code"]
    )
    # C keeps only the hash: the code is in no table, and nothing waits in the message outbox
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        h = conn.execute(
            "select code_hash from app.guardian_verifications where id = %s",
            (start["verification_id"],),
        ).fetchone()[0]
    assert h != sent["code"] and sent["code"] not in h
    assert not _outbox("destination = %s", ("+15550100041",))
    ok = client.post(
        "/consents/guardian/verify",
        headers=v,
        json={
            "verification_id": start["verification_id"],
            "code": sent["code"],
            "text_version": "t1",
        },
    )
    assert ok.status_code == 201, ok.text
    lead = client.post(
        "/leads", headers=v, json={"name": "Young WA", "is_minor": True}
    ).json()
    assert lead["guardian_contact"] == {
        "channel": "whatsapp",
        "destination": "+15550100041",
    }


def test_guardian_code_by_whatsapp_is_refused_honestly(settings, monkeypatch):
    client, _, _, fake = _wa_client(settings, monkeypatch, dev_echo_codes=False)
    for refuse, down in (
        ("This college has no WhatsApp number in Module B", False),
        (None, True),
    ):
        fake.refuse, fake.down = refuse, down
        r = client.post(
            "/consents/guardian/start",
            headers=visitor(client),
            json={"channel": "whatsapp", "destination": "+15550100043"},
        )
        assert r.status_code == 503 and "e-mail" in r.json()["detail"], r.text


def test_whatsapp_acknowledgement_is_sent_through_module_b_then_forgotten(
    settings, monkeypatch
):
    from experience_api import messaging

    client, s, _, fake = _wa_client(settings, monkeypatch)
    _, hid = _wa_callback(client, "+15550100044", "conv-wa-ack")
    (ack,) = _outbox("handoff_id = %s and template = 'callback_ack'", (hid,))
    assert ack["channel"] == "whatsapp" and ack["destination"] == "+15550100044"
    assert messaging.deliver_whatsapp(s, _demo_tenant_uuid())["sent"] >= 1
    notify = [b for p, b in fake.calls if p == "/channels/whatsapp/notify"]
    assert {"to": "+15550100044", "kind": "callback_ack"}.items() <= next(
        b for b in notify if b["to"] == "+15550100044"
    ).items()
    (ack,) = _outbox("handoff_id = %s and template = 'callback_ack'", (hid,))
    assert (
        ack["status"] == "sent" and ack["destination"] is None and ack["body"] is None
    )


def test_whatsapp_outage_is_retried_and_a_refusal_is_shown_to_staff(
    settings, monkeypatch
):
    from experience_api import messaging

    client, s, _, fake = _wa_client(settings, monkeypatch)
    _, hid = _wa_callback(client, "+15550100045", "conv-wa-refuse")
    fake.down = True
    messaging.deliver_whatsapp(s, _demo_tenant_uuid())
    (m,) = _outbox("handoff_id = %s and channel = 'whatsapp'", (hid,))
    assert m["status"] == "pending" and m["attempts"] == 1 and m["body"]
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        conn.execute(
            "update app.message_outbox set next_attempt_at = now() where id = %s",
            (m["id"],),
        )
    fake.down, fake.refuse = False, "This person asked not to get WhatsApp messages"
    messaging.deliver_whatsapp(s, _demo_tenant_uuid())
    (m,) = _outbox("handoff_id = %s and channel = 'whatsapp'", (hid,))
    assert m["status"] == "failed" and m["destination"] is None and m["body"] is None
    assert m["last_error"].startswith("refused: This person asked")
    officer, _ = login(client, "officer@example.com")
    events = client.get(f"/handoffs/{hid}", headers=officer).json()["events"]
    assert any(
        e["kind"] == "messaged" and "not sent" in (e["body"] or "") for e in events
    )


def test_staff_reply_on_whatsapp_only_when_there_is_a_number(settings, monkeypatch):
    client, _, _, _ = _wa_client(settings, monkeypatch)
    _, hid = _wa_callback(client, "+15550100046", "conv-wa-reply")
    officer, _ = login(client, "officer@example.com")
    r = client.post(
        f"/handoffs/{hid}/reply",
        headers=officer,
        json={"body": "The hostel fee is in the circular.", "send_whatsapp": True},
    )
    assert r.status_code == 200, r.text
    (m,) = _outbox("template = 'staff_reply' and handoff_id = %s", (hid,))
    assert m["channel"] == "whatsapp" and m["destination"] == "+15550100046"
    assert "hostel fee" in m["body"]
    events = client.get(f"/handoffs/{hid}", headers=officer).json()["events"]
    assert any(e["kind"] == "messaged" for e in events)

    # an e-mail-only applicant has no WhatsApp number: say so, queue nothing
    _, hid2 = _adult_with_callback(client, "wa.none@example.com", "conv-wa-none")
    r = client.post(
        f"/handoffs/{hid2}/reply",
        headers=officer,
        json={"body": "x", "send_whatsapp": True},
    )
    assert r.status_code == 409 and "WhatsApp" in r.json()["detail"]
    assert not _outbox("template = 'staff_reply' and handoff_id = %s", (hid2,))


def test_overdue_whatsapp_callback_gets_an_update_on_whatsapp(settings, monkeypatch):
    from experience_api import messaging

    client, s, _, _ = _wa_client(settings, monkeypatch)
    _, hid = _wa_callback(client, "+15550100047", "conv-wa-late")
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        conn.execute(
            "update app.handoffs set sla_due_at = now() - interval '1 hour' where id = %s",
            (hid,),
        )
    assert messaging.escalate_overdue(s, _demo_tenant_uuid()) >= 1
    (m,) = _outbox("template = 'callback_delayed' and handoff_id = %s", (hid,))
    assert m["channel"] == "whatsapp" and "sorry" in m["body"].lower()
    officer, _ = login(client, "officer@example.com")
    events = client.get(f"/handoffs/{hid}", headers=officer).json()["events"]
    assert any(
        e["kind"] == "escalated" and "WhatsApp" in (e["body"] or "") for e in events
    )


def test_implicit_signals_reach_module_a_without_visitor_text(client):
    v = visitor(client)
    for kind in ("user_message", "rephrase", "answer", "talk_to_human"):
        r = client.post(
            "/conversation-events",
            headers=v,
            json={
                "conversation_id": "conv-signal-1",
                "kind": kind,
                "message_id": "m-1",
            },
        )
        assert r.status_code == 204, r.text
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        rows = conn.execute(
            "select type, payload from app.outbox_events"
            " where payload->>'conversation_id' = 'conv-signal-1' order by id"
        ).fetchall()
    assert [p["kind"] for _, p in rows] == ["rephrase", "talk_to_human"]
    assert {t for t, _ in rows} == {"signal.logged"}
    assert all(
        set(p) == {"kind", "conversation_id", "message_id", "channel"} for _, p in rows
    )


def test_weekly_digest_is_emailed_to_approvers_once_a_week(settings, monkeypatch):
    from experience_api import clients, messaging
    from experience_api.clients import UpstreamError

    client, s, box = _mail_client(settings, monkeypatch)
    calls = []
    digest = {
        "subject": "AdmitAI: 2 proposal(s) to approve",
        "text": "2 improvement proposal(s) are waiting for approval.\n\n1. Hostel fee - impact 3.10",
        "send": True,
    }
    monkeypatch.setattr(
        clients,
        "knowledge_get",
        lambda st, path, t, p=None: calls.append(path) or digest,
    )
    monkeypatch.setattr(
        clients,
        "knowledge_post",
        lambda st, path, t, b, method="POST": calls.append(path) or {},
    )
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        conn.execute("delete from app.message_outbox where template = 'weekly_digest'")
    tenant = _demo_tenant_uuid()
    assert messaging.weekly_digest(s, tenant) == "queued"
    assert calls == ["/improvements/second-reviews/sample", "/reports/digest"]
    assert messaging.weekly_digest(s, tenant) is None and len(calls) == 2  # once a week
    (m,) = _outbox("template = 'weekly_digest'")
    assert m["audience"] == "approvers" and "/console/approvals" in m["body"]
    messaging.deliver(s, tenant)
    for who in ("admin@example.com", "officer@example.com"):
        (mail,) = [x for x in box.to(who) if x["subject"] == digest["subject"]]
        assert "Hostel fee" in mail["body"]

    # nothing to approve: the week is recorded, nothing is sent
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        conn.execute("delete from app.message_outbox where template = 'weekly_digest'")
    digest["send"] = False
    assert messaging.weekly_digest(s, tenant) == "nothing"
    (m,) = _outbox("template = 'weekly_digest'")
    assert m["status"] == "cancelled" and m["body"] is None

    # Module A down: nothing recorded, so the next run tries again
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        conn.execute("delete from app.message_outbox where template = 'weekly_digest'")

    def down(*a, **k):
        raise UpstreamError("Knowledge service unavailable")

    monkeypatch.setattr(clients, "knowledge_get", down)
    monkeypatch.setattr(clients, "knowledge_post", down)
    assert messaging.weekly_digest(s, tenant) is None
    assert not _outbox("template = 'weekly_digest'")


def _profile_id(email):
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        return str(
            conn.execute(
                "select p.id from app.profiles p join app.tenants t on t.id = p.tenant_id"
                " where p.email = %s and t.slug = 'demo-college'",
                (email,),
            ).fetchone()[0]
        )


def test_gate_rejected_change_can_be_approved_again(client, monkeypatch):
    from experience_api.routes import approvals

    rejected = [
        {
            "id": "gap",
            "status": "failed_gate",
            "department": None,
            "note": "Yes. There is an ATM beside the main gate.",
        },
        {
            "id": "crashed",
            "status": "failed_gate",
            "department": None,
            "note": "Evaluation could not run: timeout",
        },
        {"id": "mech", "status": "failed_gate", "department": "Mechanical"},
    ]
    calls = []

    def fake_get(settings, path, tenant_id, params):
        return {"items": rejected if params["status"] == "failed_gate" else []}

    def fake_post(settings, path, tenant_id, body):
        calls.append((path, body["note"]))
        return {"status": "evaluating"}

    monkeypatch.setattr(approvals, "knowledge_get", fake_get)
    monkeypatch.setattr(approvals, "knowledge_post", fake_post)
    officer, _ = login(client, "officer@example.com")
    decide = lambda ids, decision="approve": client.post(  # noqa: E731
        "/approvals/decide", headers=officer, json={"ids": ids, "decision": decision}
    ).json()["results"]

    # The answer the approver wrote is sent again; the error text of a crashed run never is.
    assert decide(["gap"]) == [{"id": "gap", "ok": True}]
    assert calls == [
        ("/improvements/gap/approve", "Yes. There is an ATM beside the main gate.")
    ]
    calls.clear()
    assert decide(["crashed"]) == [{"id": "crashed", "ok": True}]
    assert calls == [("/improvements/crashed/approve", None)]
    calls.clear()
    # Another department's change stays out of reach, and only approving is allowed.
    assert decide(["mech"])[0]["error"] == "Not in your inbox"
    assert decide(["gap"], "reject")[0]["error"] == "Not in your inbox"
    assert calls == []


def test_second_review_is_never_by_the_first_approver(client, monkeypatch):
    from experience_api.routes import approvals

    officer_id = _profile_id("officer@example.com")
    pending = [
        {"id": "p-own", "department": None, "decided_by": officer_id},
        {"id": "p-other", "department": None, "decided_by": "someone-else"},
        {"id": "p-cse", "department": "Computer Science", "decided_by": "x"},
    ]
    gets, posts = [], []
    monkeypatch.setattr(
        approvals,
        "knowledge_get",
        lambda s, path, t, params=None: gets.append((path, params))
        or {"items": pending},
    )
    monkeypatch.setattr(
        approvals,
        "knowledge_post",
        lambda s, path, t, b, method="POST": posts.append((path, b)) or {"ok": True},
    )
    officer, _ = login(client, "officer@example.com")
    items = client.get("/approvals/second-reviews", headers=officer).json()["items"]
    assert [p["id"] for p in items] == [
        "p-other"
    ]  # not their own, not another department
    assert gets[0][1]["second_review"] == "pending"
    r = client.post(
        "/approvals/p-own/second-review", headers=officer, json={"verdict": "agree"}
    )
    assert r.status_code == 404 and posts == []
    r = client.post(
        "/approvals/p-other/second-review",
        headers=officer,
        json={"verdict": "disagree", "note": "The fee changed"},
    )
    assert r.status_code == 200, r.text
    assert posts == [
        (
            "/improvements/p-other/second-review",
            {"verdict": "disagree", "reviewer": officer_id, "note": "The fee changed"},
        )
    ]


def test_only_the_college_admin_names_delegates(client, monkeypatch):
    from experience_api.routes import approvals

    calls = []
    monkeypatch.setattr(
        approvals,
        "knowledge_post",
        lambda s, path, t, b, method="POST": calls.append((method, path, b)) or b,
    )
    officer, _ = login(client, "officer@example.com")
    body = {"delegates": {"default": "Deputy Registrar"}}
    assert (
        client.put("/approvals/delegates", headers=officer, json=body).status_code
        == 403
    )
    admin, _ = login(client, "admin@example.com")
    bad = {"delegates": {"default": "  "}}
    assert (
        client.put("/approvals/delegates", headers=admin, json=bad).status_code == 422
    )
    assert calls == []
    r = client.put("/approvals/delegates", headers=admin, json=body)
    assert r.status_code == 200, r.text
    assert calls[0][:2] == ("PUT", "/learning/settings")
    assert calls[0][2]["delegates"] == {"default": "Deputy Registrar"}


def test_quality_and_topics_dashboards_say_when_module_a_is_down(client, monkeypatch):
    from experience_api.clients import UpstreamError
    from experience_api.routes import analytics

    def down(*a, **k):
        raise UpstreamError("Knowledge service unavailable")

    monkeypatch.setattr(analytics, "knowledge_get", down)
    admin, _ = login(client, "admin@example.com")
    for path in ("/analytics/quality", "/analytics/topics"):
        r = client.get(path, headers=admin)
        assert r.status_code == 200 and r.json() == {"available": False}

    reports = {
        "/reports/quality": {"weeks": [{"week": "2026-W44", "acceptance_rate": 0.8}]},
        "/reports/weekly": {
            "week": {"from": "2026-10-27", "to": "2026-11-03"},
            "knowledge_gaps": [{"question": "Is there a swimming pool?", "n": 4}],
        },
    }
    monkeypatch.setattr(
        analytics, "knowledge_get", lambda s, path, t, p=None: reports[path]
    )
    q = client.get("/analytics/quality", headers=admin).json()
    assert q["available"] and q["weeks"][0]["acceptance_rate"] == 0.8
    t = client.get("/analytics/topics", headers=admin).json()
    assert t["unanswered"] == [{"question": "Is there a swimming pool?", "count": 4}]
