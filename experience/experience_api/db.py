"""Database access. Every query runs inside a transaction that first sets the RLS context."""

from __future__ import annotations

import atexit
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

__all__ = ["Context", "Jsonb", "audit", "set_profile", "tx"]


@dataclass(frozen=True)
class Context:
    """Who is acting. Becomes app.* settings that the RLS policies read."""

    tenant_id: UUID
    role: str  # a staff role (deps.STAFF_ROLES) | visitor | service | login | system
    profile_id: UUID | None = None
    session_id: UUID | None = None
    departments: tuple[str, ...] = field(default_factory=tuple)

    @property
    def actor_kind(self) -> str:
        if self.role in ("visitor",):
            return "visitor"
        if self.role in ("service",):
            return "service"
        if self.role in ("system", "login"):
            return "system"
        return "staff"


# Connection pool (one per database URL and process). Opening a connection costs about 4 ms on a
# laptop, more to a remote database; a request often runs several transactions. RLS settings are
# transaction-local (set_config(..., true)), and every returned connection is also reset, so nothing
# carries over from one request to the next. DB_POOL_MAX=0 turns pooling off.
POOL_MAX = int(os.environ.get("DB_POOL_MAX", "10"))
_pools: dict[str, ConnectionPool] = {}
_pools_lock = threading.Lock()


def _reset(conn: psycopg.Connection) -> None:
    conn.autocommit = True
    try:
        conn.execute("reset all")
    finally:
        conn.autocommit = False


def _pool(database_url: str) -> ConnectionPool | None:
    if POOL_MAX <= 0:
        return None
    pool = _pools.get(database_url)
    if pool is None:
        with _pools_lock:
            pool = _pools.get(database_url)
            if pool is None:
                pool = ConnectionPool(
                    database_url,
                    min_size=1,
                    max_size=POOL_MAX,
                    # No server-side prepared statements: as before, when every request had its own
                    # connection, so a migration cannot leave a stale cached plan behind.
                    kwargs={
                        "row_factory": dict_row,
                        "autocommit": False,
                        "prepare_threshold": None,
                    },
                    check=ConnectionPool.check_connection,  # drops connections a restart broke
                    reset=_reset,
                    timeout=10,
                    open=True,
                )
                _pools[database_url] = pool
    return pool


def _connection(database_url: str):
    pool = _pool(database_url)
    if pool is None:
        return psycopg.connect(database_url, row_factory=dict_row, autocommit=False)
    return pool.connection()


@atexit.register
def close_pools() -> None:
    with _pools_lock:
        for pool in _pools.values():
            pool.close()
        _pools.clear()


@contextmanager
def tx(database_url: str, ctx: Context | None) -> Iterator[psycopg.Cursor]:
    """Take a pooled connection, start a transaction, set the RLS context (transaction-local), yield a cursor."""
    with _connection(database_url) as conn:
        with conn.cursor() as cur:
            if ctx is not None:
                cur.execute(
                    "select set_config('app.tenant_id', %s, true), set_config('app.role', %s, true),"
                    " set_config('app.profile_id', %s, true), set_config('app.session_id', %s, true),"
                    " set_config('app.departments', %s, true)",
                    (
                        str(ctx.tenant_id),
                        ctx.role,
                        str(ctx.profile_id) if ctx.profile_id else "",
                        str(ctx.session_id) if ctx.session_id else "",
                        ",".join(ctx.departments),
                    ),
                )
            yield cur
        conn.commit()


def set_profile(cur: psycopg.Cursor, profile_id: UUID) -> None:
    """Sign-in step: once the password has checked out, act as that profile for the rest of the transaction."""
    cur.execute("select set_config('app.profile_id', %s, true)", (str(profile_id),))


def audit(
    cur: psycopg.Cursor,
    ctx: Context,
    action: str,
    entity: str,
    entity_id: object = None,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    """Append one audit record (PRD 5.4: every admin action is logged)."""
    cur.execute(
        "insert into app.audit_logs (tenant_id, actor_id, actor_kind, action, entity, entity_id, before, after)"
        " values (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            ctx.tenant_id,
            ctx.profile_id,
            ctx.actor_kind,
            action,
            entity,
            str(entity_id) if entity_id is not None else None,
            Jsonb(before) if before is not None else None,
            Jsonb(after) if after is not None else None,
        ),
    )
