"""pgvector HNSW (PRD 9.4, F4.7). Runs only where the PostgreSQL server has the extension (the
lab's server); elsewhere the in-memory search is used and these tests are skipped."""

import os

import psycopg
import pytest

from conftest import TEST_ADMIN_URL, needs_db
from knowledge_api import search

pytestmark = needs_db


def has_pgvector() -> bool:
    if not TEST_ADMIN_URL or os.environ.get("KB_PGVECTOR") == "off":
        return False
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        return bool(
            conn.execute(
                "select 1 from pg_available_extensions where name = 'vector'"
            ).fetchone()
        )


needs_pgvector = pytest.mark.skipif(not has_pgvector(), reason="pgvector not installed")

DOCS = {
    "fees.md": "# Fees\n\nThe tuition fee for B.Tech is 250000 per year. Hostel fee is 90000.",
    "hostel.md": "# Hostel\n\nSeparate hostels for boys and girls, with a mess and Wi-Fi.",
    "exam.md": "# Entrance test\n\nThe entrance test is online, 120 minutes, no negative marking.",
    "old.md": "# Fees 2025\n\nThe tuition fee for B.Tech was 200000 per year.",
}


def load(client, tenant):
    h = {"X-Tenant-Id": tenant}
    for name, text in DOCS.items():
        d = client.post(
            "/kb/documents", headers=h, files={"file": (name, text.encode())}
        ).json()
        action = "expire" if name == "old.md" else "approve"
        client.patch(
            f"/kb/documents/{d['id']}",
            headers=h,
            json={"action": "approve", "actor": "a"},
        )
        if action == "expire":
            client.patch(
                f"/kb/documents/{d['id']}",
                headers=h,
                json={"action": "expire", "actor": "a", "reason": "old"},
            )
    q = client.post(
        "/curated-qa",
        headers=h,
        json={
            "question": "Is there a hostel for girls?",
            "answer": "Yes, a separate girls' hostel.",
            "academic_year": "2027-28",
            "valid_to": "2099-07-31",
        },
    ).json()
    client.post(f"/curated-qa/{q['id']}/approve", headers=h, json={"actor": "a"})


@needs_pgvector
def test_hnsw_search_matches_the_in_memory_search(
    client, settings, embedder, tenant, other_tenant, monkeypatch
):
    load(client, tenant)
    load(client, other_tenant)  # another college's identical content must never appear
    assert search.uses_pgvector(settings.database_url)
    queries = [
        "What is the tuition fee?",
        "Is there a hostel for girls?",
        "Is there negative marking in the entrance test?",
        "wifi in hostel",
    ]
    in_db = {
        q: search.search(settings.database_url, embedder, tenant, q, top_k=4)
        for q in queries
    }
    monkeypatch.setenv("KB_VECTOR_BACKEND", "memory")
    search.invalidate()
    in_memory = {
        q: search.search(settings.database_url, embedder, tenant, q, top_k=4)
        for q in queries
    }
    for q in queries:
        a, b = in_db[q], in_memory[q]
        assert [i["id"] for i in a["items"]] == [i["id"] for i in b["items"]], q
        for x, y in zip(a["items"], b["items"]):
            assert abs(x["vector_score"] - y["vector_score"]) < 1e-3
        assert abs(a["max_vector_score"] - b["max_vector_score"]) < 1e-3
    with psycopg.connect(settings.admin_database_url) as conn:
        tenants = {
            str(r[0])
            for r in conn.execute(
                "select tenant_id from kb.chunks where id = any(%s::uuid[])",
                ([i["id"] for r in in_db.values() for i in r["items"]],),
            )
        }
    assert tenants <= {tenant}
    # The expired 2025 fee notice is never returned.
    titles = [
        i["citation"]["title"] for i in in_db["What is the tuition fee?"]["items"]
    ]
    assert "old.md" not in titles


@needs_pgvector
def test_hnsw_indexes_exist(settings):
    with psycopg.connect(settings.admin_database_url) as conn:
        names = {
            r[0]
            for r in conn.execute(
                "select indexname from pg_indexes where schemaname = 'kb' and indexname like '%hnsw'"
            )
        }
    assert {"chunks_embedding_v_hnsw", "curated_qa_embedding_v_hnsw"} <= names


def test_memory_backend_can_be_forced(settings, monkeypatch):
    monkeypatch.setenv("KB_VECTOR_BACKEND", "memory")
    assert search.uses_pgvector(settings.database_url) is False
