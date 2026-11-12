"""Database access. Every query runs inside a transaction that first sets the tenant for RLS."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

__all__ = ["Jsonb", "tx"]


@contextmanager
def tx(database_url: str, tenant_id: UUID | str) -> Iterator[psycopg.Cursor]:
    """Open a connection, start a transaction, set app.tenant_id (transaction-local), yield a cursor."""
    with psycopg.connect(database_url, row_factory=dict_row, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select set_config('app.tenant_id', %s, true)", (str(tenant_id),)
            )
            yield cur
        conn.commit()
