"""Apply knowledge/db/migrations/*.sql in order (schemas `kb` and `learn` only).

python -m knowledge_api.migrate            # uses KB_ADMIN_DATABASE_URL
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg

MIGRATIONS = Path(__file__).resolve().parent.parent / "db" / "migrations"


# PRD 9.4 / F4.7: pgvector HNSW in the same database. Added only where the extension is installed
# (the lab's PostgreSQL), so laptops and CI without it keep the in-memory vector search. The
# vector columns are generated from the stored real[] embeddings, so nothing that writes
# embeddings changes.
PGVECTOR = [
    ("kb.chunks", "embedding_v", "embedding", 768),
    ("kb.curated_qa", "embedding_v", "embedding", 768),
    ("kb.chunks", "embedding_ml_v", "embedding_ml", 1024),
    ("kb.curated_qa", "embedding_ml_v", "embedding_ml", 1024),
]


def ensure_pgvector(conn) -> str:
    """Create the pgvector columns and HNSW indexes if the extension is available. Idempotent."""
    if os.environ.get("KB_PGVECTOR", "auto") == "off":
        return "pgvector: switched off (KB_PGVECTOR=off)"
    available = conn.execute(
        "select 1 from pg_available_extensions where name = 'vector'"
    ).fetchone()
    if not available:
        return "pgvector: not installed on this server; vector search runs in the API"
    conn.execute("create extension if not exists vector")
    added = []
    for table, col, source, dim in PGVECTOR:
        schema, name = table.split(".")
        exists = conn.execute(
            "select 1 from information_schema.columns where table_schema = %s and table_name = %s"
            " and column_name = %s",
            (schema, name, col),
        ).fetchone()
        if not exists:
            conn.execute(
                f"alter table {table} add column {col} vector({dim})"
                f" generated always as ({source}::vector({dim})) stored"
            )
            added.append(f"{table}.{col}")
        conn.execute(
            f"create index if not exists {name}_{col}_hnsw on {table}"
            f" using hnsw ({col} vector_cosine_ops)"
        )
    return "pgvector: HNSW ready" + (f" (added {', '.join(added)})" if added else "")


def migrate(admin_url: str) -> list[str]:
    applied: list[str] = []
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute("create schema if not exists kb")
        conn.execute(
            "create table if not exists kb.schema_migrations (name text primary key, applied_at timestamptz default now())"
        )
        done = {r[0] for r in conn.execute("select name from kb.schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute(
                    "insert into kb.schema_migrations (name) values (%s)", (path.name,)
                )
            applied.append(path.name)
        with conn.transaction():
            note = ensure_pgvector(conn)
        if "added" in note:
            applied.append(note)
    return applied


if __name__ == "__main__":
    from .config import Settings

    url = Settings().admin_database_url
    if not url:
        sys.exit("Set KB_ADMIN_DATABASE_URL (source local.env.sh)")
    names = migrate(url)
    print("applied:", ", ".join(names) if names else "nothing new")
