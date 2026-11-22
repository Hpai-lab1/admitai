"""Background jobs connect as admitai_kb_worker (migration 011), not as the superuser: it reads and
writes across colleges in kb and learn, and can do nothing else (review, 18 Nov 2026)."""

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from conftest import TEST_ADMIN_URL, needs_db

pytestmark = needs_db


def _as(role: str) -> str:
    info = conninfo_to_dict(TEST_ADMIN_URL)
    info["user"] = role
    info.pop("password", None)  # local trust; on a server the role has its own password
    return make_conninfo(**info)


def test_the_worker_role_is_no_superuser_but_reads_across_colleges(migrated):
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        row = conn.execute(
            "select rolsuper, rolbypassrls, rolcreatedb, rolcreaterole from pg_roles"
            " where rolname = 'admitai_kb_worker'"
        ).fetchone()
    assert row == (False, True, False, False)
    with psycopg.connect(_as("admitai_kb_worker")) as conn:
        # no app.tenant_id set: only a role that bypasses RLS sees rows of every college
        conn.execute("select count(*) from kb.documents").fetchone()
        conn.execute("select count(*) from learn.proposals").fetchone()


def test_the_worker_role_can_do_nothing_else(migrated):
    with psycopg.connect(_as("admitai_kb_worker"), autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("create table kb.not_allowed (x int)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("drop table kb.documents")
