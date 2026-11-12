"""API + database tests: documents, curated Q&A, catalogue, search filters, tenant isolation, events."""

from datetime import date, timedelta

import httpx
import psycopg
import pytest

from conftest import TEST_URL, needs_db

pytestmark = needs_db

PROGRAMS = (
    "code,name,aliases,department,level,duration_months,intake,eligibility,academic_year\n"
    "BTECH-CSE,B.Tech Computer Science and Engineering,CSE|Computer Science,Computer Science,UG,48,120,"
    '"10+2 with PCM, 50%",2027-28\n'
    "MBA,Master of Business Administration,,Management,PG,24,60,Any degree with 50%,2027-28\n"
)
FEES = (
    "program_code,category,component,amount,currency,period\n"
    "BTECH-CSE,general,tuition,250000,INR,per year\n"
    "BTECH-CSE,management,tuition,412500.50,INR,per year\n"
    "BTECH-CSE,general,hostel,90000,INR,per year\n"
)
DATES = "program_code,label,date,academic_year\n,Last date to apply,2027-06-30,2027-28\nMBA,Interview,2027-05-10,2027-28\n"


def h(tenant):
    return {"X-Tenant-Id": tenant}


def upload(client, tenant, name, text, **meta):
    r = client.post(
        "/kb/documents",
        headers=h(tenant),
        files={"file": (name, text.encode())},
        data={k: str(v) for k, v in meta.items()},
    )
    assert r.status_code == 202, r.text
    return r.json()


def search(client, tenant, query, **kw):
    r = client.post("/kb/search", headers=h(tenant), json={"query": query, **kw})
    assert r.status_code == 200, r.text
    return r.json()["items"]


def approve(client, tenant, doc_id, **kw):
    r = client.patch(
        f"/kb/documents/{doc_id}",
        headers=h(tenant),
        json={"action": "approve", "actor": "admin", **kw},
    )
    assert r.status_code == 200, r.text
    return r.json()


HOSTEL = "# Hostel\n\nSeparate hostels for men and women. Hostel rooms are allotted after admission."


def test_upload_is_draft_until_approved_then_cited(client, tenant):
    doc = upload(
        client,
        tenant,
        "hostel.md",
        HOSTEL,
        academic_year="2027-28",
        owner="Student Affairs",
    )
    assert doc["status"] == "processing"
    assert (
        search(client, tenant, "hostel rooms") == []
    )  # drafts are never served (F4.6)
    listed = client.get("/kb/documents", headers=h(tenant)).json()["items"][0]
    assert (
        listed["status"] == "draft"
        and listed["chunks"] == 1
        and listed["owner"] == "Student Affairs"
    )
    approve(client, tenant, doc["id"])
    items = search(client, tenant, "Is there a hostel?")
    assert items[0]["citation"]["title"] == "hostel.md"
    assert items[0]["citation"]["heading_path"] == "Hostel"
    assert items[0]["citation"]["academic_year"] == "2027-28"


def test_expired_and_out_of_date_documents_are_never_cited(client, tenant):
    old = upload(
        client,
        tenant,
        "fees-old.md",
        "# Fees\n\nTuition fee is 200000 per year.",
        valid_to=date.today() - timedelta(days=1),
    )
    r = client.patch(
        f"/kb/documents/{old['id']}", headers=h(tenant), json={"action": "approve"}
    )
    assert r.status_code == 409 and "valid_to" in r.json()["detail"]
    doc = upload(client, tenant, "fees.md", "# Fees\n\nTuition fee is 250000 per year.")
    approve(client, tenant, doc["id"])
    assert search(client, tenant, "tuition fee")[0]["content"].endswith(
        "250000 per year."
    )
    client.patch(
        f"/kb/documents/{doc['id']}",
        headers=h(tenant),
        json={"action": "expire", "actor": "officer"},
    )
    assert search(client, tenant, "tuition fee") == []


def test_academic_year_and_department_filters(client, tenant):
    a = upload(
        client,
        tenant,
        "cse.md",
        "# Labs\n\nThe AI lab has 60 GPUs.",
        academic_year="2027-28",
        department="Computer Science",
    )
    b = upload(
        client,
        tenant,
        "mech.md",
        "# Labs\n\nThe workshop lab has lathes.",
        academic_year="2026-27",
        department="Mechanical",
    )
    approve(client, tenant, a["id"])
    approve(client, tenant, b["id"])
    assert {i["citation"]["title"] for i in search(client, tenant, "labs")} == {
        "cse.md",
        "mech.md",
    }
    assert [
        i["citation"]["title"]
        for i in search(client, tenant, "labs", academic_year="2027-28")
    ] == ["cse.md"]
    assert [
        i["citation"]["title"]
        for i in search(client, tenant, "labs", department="Mechanical")
    ] == ["mech.md"]


def test_duplicate_upload_links_to_existing(client, tenant):
    first = upload(client, tenant, "hostel.md", HOSTEL)
    again = upload(client, tenant, "hostel-copy.md", HOSTEL)
    assert again["duplicate"] is True and again["id"] == first["id"]


def test_corrupt_file_is_rejected_and_nothing_indexed(client, tenant):
    doc = upload(client, tenant, "broken.pdf", "%PDF-1.4 garbage")
    row = client.get(f"/kb/documents/{doc['id']}", headers=h(tenant)).json()
    assert row["status"] == "rejected" and "could not be read" in row["status_reason"]
    assert row["sections"] == []
    r = client.patch(
        f"/kb/documents/{doc['id']}", headers=h(tenant), json={"action": "approve"}
    )
    assert r.status_code == 409


def test_new_version_diff_approve_and_rollback(client, tenant):
    v1 = upload(
        client,
        tenant,
        "fees.md",
        "# Fees\n\nTuition fee is 200000 per year.",
        academic_year="2027-28",
    )
    approve(client, tenant, v1["id"])
    v2 = upload(
        client,
        tenant,
        "fees.md",
        "# Fees\n\nTuition fee is 250000 per year.",
        replaces=v1["id"],
    )
    row = client.get(f"/kb/documents/{v2['id']}", headers=h(tenant)).json()
    assert (
        row["version"] == 2 and row["academic_year"] == "2027-28"
    )  # metadata inherited
    assert "+Tuition fee is 250000 per year." in row["diff_summary"]
    assert (
        "200000" in search(client, tenant, "tuition fee")[0]["content"]
    )  # v2 still a draft
    approve(client, tenant, v2["id"])
    assert "250000" in search(client, tenant, "tuition fee")[0]["content"]
    old = client.get(f"/kb/documents/{v1['id']}", headers=h(tenant)).json()
    assert (
        old["status"] == "expired" and "replaced by version 2" in old["status_reason"]
    )
    r = client.patch(
        f"/kb/documents/{v2['id']}",
        headers=h(tenant),
        json={"action": "rollback", "actor": "admin"},
    )
    assert (
        r.status_code == 200
        and r.json()["id"] == v1["id"]
        and r.json()["status"] == "approved"
    )
    assert "200000" in search(client, tenant, "tuition fee")[0]["content"]


def test_curated_answer_lifecycle_and_rollback(client, tenant):
    body = {
        "question": "Is there a hostel for first-year students?",
        "variants": ["hostel for freshers"],
        "answer": "Yes, separate hostels for men and women.",
        "academic_year": "2027-28",
        "valid_to": str(date.today() + timedelta(days=200)),
        "actor": "officer",
    }
    assert (
        client.post(
            "/curated-qa", headers=h(tenant), json={**body, "academic_year": ""}
        ).status_code
        == 422
    )
    qa = client.post("/curated-qa", headers=h(tenant), json=body).json()
    assert qa["status"] == "draft" and qa["version"] == 1
    assert search(client, tenant, "hostel freshers") == []
    client.post(
        f"/curated-qa/{qa['id']}/approve", headers=h(tenant), json={"actor": "officer"}
    )
    hit = search(client, tenant, "hostel freshers")[0]
    assert hit["kind"] == "curated_qa" and hit["citation"]["qa_id"] == qa["id"]
    edited = client.patch(
        f"/curated-qa/{qa['id']}",
        headers=h(tenant),
        json={"answer": "No hostel this year.", "actor": "officer"},
    ).json()
    assert edited["version"] == 2 and edited["status"] == "draft"
    assert search(client, tenant, "hostel freshers") == []
    restored = client.post(
        f"/curated-qa/{qa['id']}/rollback",
        headers=h(tenant),
        json={"version": 1, "actor": "admin"},
    ).json()
    assert restored["answer"] == body["answer"]
    detail = client.get(f"/curated-qa/{qa['id']}", headers=h(tenant)).json()
    assert [x["action"] for x in detail["history"]][:2] == ["create", "approved"]


def test_curated_version_numbers(client, tenant):
    body = {
        "question": "What is Q?",
        "answer": "A.",
        "academic_year": "2027-28",
        "valid_to": "2099-01-01",
    }
    qa = client.post("/curated-qa", headers=h(tenant), json=body).json()
    edited = client.patch(
        f"/curated-qa/{qa['id']}", headers=h(tenant), json={"answer": "B."}
    ).json()
    assert edited["version"] == 2


def test_catalogue_import_and_exact_facts(client, tenant):
    files = {
        "programs_csv": ("programs.csv", PROGRAMS),
        "fees_csv": ("fees.csv", FEES),
        "dates_csv": ("dates.csv", DATES),
    }
    r = client.post("/catalogue/import", headers=h(tenant), files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    generated = body.pop(
        "eval_cases"
    )  # F10.3 cases from the catalogue (test_level1_kb_db.py)
    assert generated["generated"] > 0
    assert body == {
        "programs": 2,
        "fees": 3,
        "dates": 2,
        "academic_years": ["2027-28"],
        "curated_suspended": 0,
    }
    facts = client.get("/catalogue/programs/btech-cse/facts", headers=h(tenant)).json()
    amounts = {(f["category"], f["component"]): f["amount"] for f in facts["fees"]}
    assert amounts == {
        ("general", "tuition"): "250000",
        ("management", "tuition"): "412500.50",
        ("general", "hostel"): "90000",
    }
    assert facts["duration_months"] == 48 and facts["intake"] == 120
    assert facts["aliases"] == ["CSE", "Computer Science"]
    assert facts["dates"] == [{"label": "Last date to apply", "date": "2027-06-30"}]
    mba = client.get("/catalogue/programs/MBA/facts", headers=h(tenant)).json()
    by_alias = client.get(
        "/catalogue/programs/computer science/facts", headers=h(tenant)
    )
    assert by_alias.json()["code"] == "BTECH-CSE"
    assert {d["label"] for d in mba["dates"]} == {"Interview", "Last date to apply"}
    # re-import is idempotent
    assert (
        client.post("/catalogue/import", headers=h(tenant), files=files).status_code
        == 200
    )
    assert (
        len(client.get("/catalogue/programs", headers=h(tenant)).json()["items"]) == 2
    )
    bad = client.post(
        "/catalogue/import",
        headers=h(tenant),
        files={"programs_csv": ("p.csv", "code,name\n")},
    )
    assert bad.status_code == 422 and "missing column" in bad.json()["problems"][0]


def test_tenants_are_isolated(client, tenant, other_tenant):
    doc = upload(client, tenant, "hostel.md", HOSTEL)
    approve(client, tenant, doc["id"])
    client.post(
        "/curated-qa",
        headers=h(tenant),
        json={
            "question": "Secret?",
            "answer": "Yes.",
            "academic_year": "2027-28",
            "valid_to": "2099-01-01",
        },
    )
    assert search(client, other_tenant, "hostel") == []
    assert client.get("/kb/documents", headers=h(other_tenant)).json()["items"] == []
    assert client.get("/curated-qa", headers=h(other_tenant)).json()["items"] == []
    assert (
        client.get(f"/kb/documents/{doc['id']}", headers=h(other_tenant)).status_code
        == 404
    )
    r = client.patch(
        f"/kb/documents/{doc['id']}", headers=h(other_tenant), json={"action": "expire"}
    )
    assert r.status_code == 404
    # Even raw SQL as the API role sees nothing without a tenant, and cannot write into another tenant.
    with psycopg.connect(TEST_URL) as conn:
        assert conn.execute("select count(*) from kb.documents").fetchone()[0] == 0
        conn.execute("select set_config('app.tenant_id', %s, false)", (other_tenant,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "insert into kb.sources (tenant_id, kind, name) values (%s, 'upload', 'x')",
                (tenant,),
            )


def test_bad_tenant_header(client):
    assert (
        client.get("/kb/documents", headers={"X-Tenant-Id": "nope"}).status_code == 400
    )
    assert client.get("/kb/documents").status_code == 422


def test_events_are_deduplicated(client, tenant):
    ev = {
        "event_id": 7,
        "type": "feedback.created",
        "tenant_id": tenant,
        "data": {"message_id": "m1", "value": -1},
    }
    assert client.post("/events", json=ev).json() == {"ok": True, "duplicate": False}
    assert client.post("/events", json=ev).json() == {"ok": True, "duplicate": True}
    assert client.post("/events", json={**ev, "type": "weird"}).status_code == 422
    assert len(client.get("/events", headers=h(tenant)).json()["items"]) == 1


SITE = {
    "/robots.txt": "User-agent: *\nDisallow: /private/",
    "/": "<html><title>Home</title><body><h1>Welcome</h1><p>Admissions are open.</p>"
    '<a href="/admissions/fees">Fees</a><a href="/private/x">P</a><a href="/news/1">News</a>'
    '<a href="https://other.example/">Elsewhere</a></body></html>',
    "/admissions/fees": "<html><title>Fees</title><body><h1>Fees</h1><p>Tuition is 250000.</p>"
    '<a href="/admissions/deep">Deep</a></body></html>',
    "/admissions/deep": "<html><title>Deep</title><body><p>Too deep to crawl.</p></body></html>",
    "/private/x": "<html><body><p>Secret</p></body></html>",
    "/news/1": "<html><body><p>News item</p></body></html>",
}


def site_client(pages):
    def handler(request: httpx.Request):
        body = pages.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_crawl_respects_robots_patterns_depth_and_detects_changes(
    settings, embedder, tenant
):
    from knowledge_api import crawl
    from knowledge_api.db import Jsonb, tx

    cfg = {
        "start_url": "https://college.example/",
        "include": [],
        "exclude": ["*/news/*"],
        "max_depth": 1,
        "defaults": {"academic_year": "2027-28"},
    }
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "insert into kb.sources (tenant_id, kind, name, config) values (%s,'crawl','site',%s) returning *",
            (tenant, Jsonb(cfg)),
        )
        source = cur.fetchone()
    stats = crawl.crawl(
        settings,
        embedder,
        tenant,
        source,
        client=site_client(SITE),
        sleep=lambda s: None,
    )
    assert stats["fetched"] == 2 and stats["new"] == 2 and stats["skipped_robots"] == 1
    with tx(settings.database_url, tenant) as cur:
        cur.execute("select url, status, academic_year from kb.documents order by url")
        rows = cur.fetchall()
    assert [r["url"] for r in rows] == [
        "https://college.example/",
        "https://college.example/admissions/fees",
    ]
    assert all(r["status"] == "draft" and r["academic_year"] == "2027-28" for r in rows)

    changed = {
        **SITE,
        "/admissions/fees": SITE["/admissions/fees"].replace("250000", "275000"),
    }
    stats = crawl.crawl(
        settings,
        embedder,
        tenant,
        source,
        client=site_client(changed),
        sleep=lambda s: None,
    )
    assert stats["unchanged"] == 1 and stats["updated"] == 1


def test_crawl_records_error_when_site_is_down(settings, embedder, tenant):
    from knowledge_api import crawl
    from knowledge_api.db import Jsonb, tx

    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "insert into kb.sources (tenant_id, kind, name, config) values (%s,'crawl','down',%s) returning *",
            (tenant, Jsonb({"start_url": "https://down.example/"})),
        )
        source = cur.fetchone()
    down = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    sleeps = []
    stats = crawl.crawl(
        settings, embedder, tenant, source, client=down, sleep=sleeps.append
    )
    assert "after 3 attempts" in stats["error"] and sleeps[:3] == [1, 2, 4]


def test_demo_pack_loads_idempotently_and_never_cites_expired(
    settings, embedder, tenant
):
    from pathlib import Path

    from knowledge_api import pack, search as s

    root = Path(__file__).resolve().parents[2] / "packs" / "demo-college"
    first = pack.load_knowledge(settings, embedder, tenant, root)
    assert (
        first["documents"] == 5
        and first["curated_qa"] == 7
        and first["catalogue"]["programs"] == 5
    )
    again = pack.load_knowledge(settings, embedder, tenant, root)
    assert again["documents"] == 0 and len(again["skipped"]) == 5
    with psycopg.connect(TEST_URL) as conn:
        conn.execute("select set_config('app.tenant_id', %s, false)", (tenant,))
        assert conn.execute("select count(*) from kb.documents").fetchone()[0] == 5
        assert conn.execute("select count(*) from kb.curated_qa").fetchone()[0] == 7
    hits = s.search(
        settings.database_url, embedder, tenant, "B.Tech tuition fee 2026-27", top_k=8
    )["items"]
    assert hits and all(h["citation"]["title"] != "Fee notice 2026-27" for h in hits)
    assert all(
        h["citation"]["qa_id"] is None or "bus" not in h["content"] for h in hits
    )


def test_indic_script_questions_use_the_multilingual_index(settings, tenant):
    from knowledge_api import curated, pack, search as s
    from knowledge_api.db import tx
    from knowledge_api.embedder import HashEmbedder, uses_local_script

    assert uses_local_script("हॉस्टल फीस") and uses_local_script("ಅರ್ಜಿ ಶುಲ್ಕ")
    assert not uses_local_script("CSE ki fees kitni hai?") and not uses_local_script(
        "Café"
    )
    plain = HashEmbedder()
    qa = {
        "question": "Is there a hostel?",
        "variants": ["हॉस्टल है क्या"],
        "answer": "Yes.",
        "academic_year": "2027-28",
        "valid_to": "2099-01-01",
    }
    with tx(settings.database_url, tenant) as cur:
        row = curated.create(cur, plain, tenant, qa, status="approved")
    assert row["embedding_ml"] is None
    assert (
        s.search(settings.database_url, plain, tenant, "हॉस्टल")["embedder"] == "primary"
    )

    dual = HashEmbedder(ml=HashEmbedder(1024))
    assert pack.reembed_missing(settings, dual, tenant) == {
        "chunks": 0,
        "curated_qa": 1,
    }
    hindi = s.search(settings.database_url, dual, tenant, "हॉस्टल है क्या")
    assert hindi["embedder"] == "multilingual" and hindi["items"][0]["citation"][
        "qa_id"
    ] == str(row["id"])
    assert (
        s.search(settings.database_url, dual, tenant, "hostel")["embedder"] == "primary"
    )


def test_every_call_needs_a_service_key_and_module_b_only_searches(
    settings, embedder, tenant
):
    from fastapi.testclient import TestClient

    from knowledge_api.main import create_app

    app = create_app(settings, embedder)
    anon = TestClient(app)
    assert anon.get("/health").status_code == 200
    assert anon.get("/kb/documents", headers=h(tenant)).status_code == 401
    assert anon.post("/events", json={}).status_code == 401
    b = TestClient(app, headers={"X-Service-Key": "test-key-from-b"})
    assert (
        b.post("/kb/search", headers=h(tenant), json={"query": "hostel"}).status_code
        == 200
    )
    assert b.get("/catalogue/programs", headers=h(tenant)).status_code == 200
    assert b.get("/kb/documents", headers=h(tenant)).status_code == 403
    assert b.get("/improvements", headers=h(tenant)).status_code == 403
    c = TestClient(app, headers={"X-Service-Key": "test-key-from-c"})
    assert c.get("/improvements", headers=h(tenant)).status_code == 200
    assert (
        TestClient(app, headers={"X-Service-Key": "wrong"})
        .get("/kb/documents", headers=h(tenant))
        .status_code
        == 401
    )


class FakeReranker:
    """Prefers passages whose text (with its document title) mentions 'mechanical'."""

    def __init__(self):
        self.seen: list[list[str]] = []

    def scores(self, query, passages):
        self.seen.append(passages)
        return [5.0 if "mechanical" in p.lower() else -5.0 for p in passages]


def test_reranker_reorders_hybrid_results_with_document_context(
    client, settings, embedder, tenant
):
    from knowledge_api import search as kb_search

    for name, text in [
        (
            "cse.md",
            "# Careers\n\nGraduates work as software engineers and data analysts.",
        ),
        ("mechanical.md", "# Careers\n\nGraduates work in manufacturing and energy."),
    ]:
        approve(client, tenant, upload(client, tenant, name, text)["id"])
    r = FakeReranker()
    plain = kb_search.search(
        settings.database_url, embedder, tenant, "graduate jobs", top_k=2
    )
    ranked = kb_search.search(
        settings.database_url, embedder, tenant, "graduate jobs", top_k=2, reranker=r
    )
    assert ranked["items"][0]["citation"]["title"] == "mechanical.md"
    assert ranked["items"][0]["rerank_score"] == 5.0
    # The title travels with the passage: the content alone never says "mechanical".
    assert any(p.startswith("mechanical.md") for p in r.seen[0])
    assert {i["id"] for i in plain["items"]} == {i["id"] for i in ranked["items"]}

    # Not for Indic-script questions (the model is English-only) or dense-only comparisons.
    r.seen.clear()
    kb_search.search(settings.database_url, embedder, tenant, "ग्रेजुएट नौकरी", reranker=r)
    kb_search.search(
        settings.database_url, embedder, tenant, "jobs", mode="dense", reranker=r
    )
    assert r.seen == []
