"""Apply experience/db/migrations/*.sql in order (schema `app` only).

python -m experience_api.migrate            # uses ADMIN_DATABASE_URL from experience/.env
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

MIGRATIONS = Path(__file__).resolve().parent.parent / "db" / "migrations"
BOOTSTRAP = Path(__file__).resolve().parent.parent / "db" / "bootstrap_role.sql"


def migrate(admin_url: str, bootstrap_role: bool = False) -> list[str]:
    applied: list[str] = []
    with psycopg.connect(admin_url, autocommit=True) as conn:
        if bootstrap_role:
            conn.execute(BOOTSTRAP.read_text(encoding="utf-8"))
        conn.execute("create schema if not exists app")
        conn.execute(
            "create table if not exists app.schema_migrations (name text primary key, applied_at timestamptz default now())"
        )
        done = {r[0] for r in conn.execute("select name from app.schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute(
                    "insert into app.schema_migrations (name) values (%s)", (path.name,)
                )
            applied.append(path.name)
    return applied


if __name__ == "__main__":
    from .config import Settings

    url = Settings().admin_database_url
    if not url:
        sys.exit("Set ADMIN_DATABASE_URL in experience/.env")
    names = migrate(url, bootstrap_role="--bootstrap-role" in sys.argv)
    print("applied:", ", ".join(names) if names else "nothing new")
