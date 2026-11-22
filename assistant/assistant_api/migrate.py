"""Apply assistant/db/migrations/*.sql in order (schema `chat` only).

python -m assistant_api.migrate            # uses CHAT_ADMIN_DATABASE_URL
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

MIGRATIONS = Path(__file__).resolve().parent.parent / "db" / "migrations"


def migrate(admin_url: str) -> list[str]:
    applied: list[str] = []
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute("create schema if not exists chat")
        conn.execute(
            "create table if not exists chat.schema_migrations (name text primary key, applied_at timestamptz default now())"
        )
        done = {r[0] for r in conn.execute("select name from chat.schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute(
                    "insert into chat.schema_migrations (name) values (%s)",
                    (path.name,),
                )
            applied.append(path.name)
    return applied


if __name__ == "__main__":
    from .config import Settings

    url = Settings().admin_database_url
    if not url:
        sys.exit("Set CHAT_ADMIN_DATABASE_URL (source local.env.sh)")
    names = migrate(url)
    print("applied:", ", ".join(names) if names else "nothing new")
