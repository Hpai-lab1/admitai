"""Errors are RFC 9457 problem details with a user-safe message and a trace id (PRD 12.3)."""

from fastapi.testclient import TestClient

from conftest import OPS, needs_db

pytestmark = needs_db


def _app(settings, embedder):
    from knowledge_api.main import create_app

    app = create_app(settings, embedder)

    @app.get("/boom")
    def boom():
        raise RuntimeError("password=hunter2 in SELECT * FROM kb.secret_table")

    return app


def test_errors_are_problem_details_with_a_trace_id(settings, embedder, tenant):
    c = TestClient(_app(settings, embedder), headers=OPS)
    r = c.get(
        "/kb/documents/00000000-0000-0000-0000-000000000000",
        headers={"X-Tenant-Id": tenant, "X-Trace-Id": "support-ticket-42"},
    )
    assert r.status_code == 404
    assert r.headers["content-type"] == "application/problem+json"
    body = r.json()
    assert body["status"] == 404 and body["title"] == "Not found" and body["detail"]
    # The caller's trace id is kept, so one id follows a request across modules.
    assert body["trace_id"] == "support-ticket-42" == r.headers["x-trace-id"]
    # Without one, a fresh id is made; a malformed one is replaced.
    fresh = c.get("/health", headers={"X-Trace-Id": "bad id; drop table"})
    assert fresh.headers["x-trace-id"] != "bad id; drop table"


def test_missing_key_is_a_problem_too(settings, embedder):
    r = TestClient(_app(settings, embedder)).post("/kb/search", json={})
    assert (
        r.status_code == 401 and r.headers["content-type"] == "application/problem+json"
    )
    assert r.json()["detail"] == "Unknown service"


def test_validation_errors_name_the_field_but_never_echo_the_value(
    settings, embedder, tenant
):
    c = TestClient(_app(settings, embedder), headers=OPS)
    r = c.post(
        "/kb/search",
        headers={"X-Tenant-Id": tenant},
        json={"query": {"phone": "+919876543210"}, "top_k": "many"},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["errors"] and all(
        {"field", "message"} == set(e) for e in body["errors"]
    )
    assert "9876543210" not in r.text and isinstance(body["detail"], str)


def test_a_crash_says_nothing_internal(settings, embedder, caplog):
    c = TestClient(_app(settings, embedder), headers=OPS, raise_server_exceptions=False)
    r = c.get("/boom")
    assert (
        r.status_code == 500 and r.headers["content-type"] == "application/problem+json"
    )
    assert "hunter2" not in r.text and "SELECT" not in r.text
    assert (
        r.json()["trace_id"] in caplog.text
    )  # the log has the details, under the same id
