"""Background jobs connect as admitai_app_worker (migration 015), not as the superuser: it works
across colleges in the app schema and can do nothing else (review, 18 Nov 2026)."""

import os

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

TEST_ADMIN_URL = os.environ.get("TEST_ADMIN_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not TEST_ADMIN_URL, reason="needs TEST_ADMIN_DATABASE_URL"
)


def _as(role: str) -> str:
    info = conninfo_to_dict(TEST_ADMIN_URL)
    info["user"] = role
    info.pop("password", None)
    return make_conninfo(**info)


@pytest.fixture(scope="module")
def migrated():
    from experience_api.migrate import migrate

    migrate(TEST_ADMIN_URL)


def test_the_worker_role_reads_every_college_but_is_no_superuser(migrated):
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        row = conn.execute(
            "select rolsuper, rolbypassrls, rolcreatedb, rolcreaterole from pg_roles"
            " where rolname = 'admitai_app_worker'"
        ).fetchone()
    assert row == (False, True, False, False)
    with psycopg.connect(_as("admitai_app_worker")) as conn:
        conn.execute("select count(*) from app.tenants").fetchone()
        conn.execute("select count(*) from app.outbox_events").fetchone()


def test_the_worker_role_cannot_change_the_schema_or_reach_other_modules(migrated):
    with psycopg.connect(_as("admitai_app_worker"), autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("create table app.not_allowed (x int)")
        # another module's schema: no rights (or, in a database without it, nothing to reach)
        with pytest.raises(
            (psycopg.errors.InsufficientPrivilege, psycopg.errors.UndefinedTable)
        ):
            conn.execute("select count(*) from chat.messages")
