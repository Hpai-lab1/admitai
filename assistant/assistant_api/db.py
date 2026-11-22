"""Database access. Every query runs inside a transaction that first sets the tenant for RLS."""

from __future__ import annotations

import atexit
import os
import threading
from contextlib import contextmanager
from typing import Iterator
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

__all__ = ["Jsonb", "tx"]


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
def tx(database_url: str, tenant_id: UUID | str) -> Iterator[psycopg.Cursor]:
    """Take a pooled connection, start a transaction, set app.tenant_id (transaction-local), yield a cursor."""
    with _connection(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select set_config('app.tenant_id', %s, true)", (str(tenant_id),)
            )
            yield cur
        conn.commit()
