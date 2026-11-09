"""Database access. Every query runs inside a transaction that first sets the RLS context."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

__all__ = ["Context", "Jsonb", "audit", "set_profile", "tx"]


@dataclass(frozen=True)
class Context:
    """Who is acting. Becomes app.* settings that the RLS policies read."""

    tenant_id: UUID
    role: str  # college_admin | admission_officer | faculty | marketing_manager | visitor | service | login | system
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


@contextmanager
def tx(database_url: str, ctx: Context | None) -> Iterator[psycopg.Cursor]:
    """Open a connection, start a transaction, set the RLS context (transaction-local), yield a cursor."""
    with psycopg.connect(database_url, row_factory=dict_row, autocommit=False) as conn:
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
