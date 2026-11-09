"""Who is calling: staff (JWT), widget visitors (session token) or Module B (service key)."""

from __future__ import annotations

import hmac
from typing import Callable
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status

from .config import Settings, token_keys, get_settings
from .db import Context, tx
from .security import read_token

STAFF_ROLES = (
    "super_admin",
    "college_admin",
    "admission_officer",
    "faculty",
    "marketing_manager",
)


def settings_dep() -> Settings:
    return get_settings()


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def staff_context(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(settings_dep),
) -> Context:
    """A signed-in staff member. Role and departments are re-read from the database on every
    request, so switching an account off or changing its role takes effect immediately."""
    claims = read_token(token_keys(settings), _bearer(authorization) or "", "access")
    if not claims:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in again")
    tenant_id, profile_id = UUID(claims["tid"]), UUID(claims["sub"])
    with tx(
        settings.database_url, Context(tenant_id, "login", profile_id=profile_id)
    ) as cur:
        cur.execute(
            "select role, is_active from app.profiles where id = %s", (profile_id,)
        )
        row = cur.fetchone()
        if not row or not row["is_active"]:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "This account is switched off"
            )
        cur.execute(
            "select department from app.profile_departments where profile_id = %s order by department",
            (profile_id,),
        )
        departments = tuple(r["department"] for r in cur.fetchall())
    return Context(
        tenant_id, row["role"], profile_id=profile_id, departments=departments
    )


def require_staff(*roles: str) -> Callable[..., Context]:
    """Dependency: a staff member with one of these roles (any staff role if none given)."""
    allowed = roles or STAFF_ROLES

    def dep(ctx: Context = Depends(staff_context)) -> Context:
        if ctx.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role can't do this")
        return ctx

    return dep


def visitor_context(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(settings_dep),
) -> Context:
    claims = read_token(token_keys(settings), _bearer(authorization) or "", "visitor")
    if not claims:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Session expired; reload the chat"
        )
    return Context(UUID(claims["tid"]), "visitor", session_id=UUID(claims["sid"]))


def service_context(
    x_service_key: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    settings: Settings = Depends(settings_dep),
) -> Context:
    """Module B calling on behalf of a tenant (PRD 21.4: B records decisions, C owns the data)."""
    if not x_service_key or not any(
        k and hmac.compare_digest(x_service_key, k)
        for k in (settings.service_key, settings.ops_key)
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown service")
    try:
        tenant_id = UUID(x_tenant_id or "")
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "X-Tenant-Id is required"
        ) from None
    return Context(tenant_id, "service")


def visitor_or_service(
    authorization: str | None = Header(default=None),
    x_service_key: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_session_id: str | None = Header(default=None),
    settings: Settings = Depends(settings_dep),
) -> Context:
    """Endpoints that both the widget (visitor) and Module B (service) call."""
    if x_service_key:
        ctx = service_context(x_service_key, x_tenant_id, settings)
        if x_session_id:
            try:
                sctx = Context(ctx.tenant_id, "service", session_id=UUID(x_session_id))
            except ValueError:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "X-Session-Id is not a valid id"
                ) from None
            # A session Module B remembers may be gone here (erased, or reset); say so plainly
            # so B can open a new one, instead of failing later on a foreign key.
            with tx(settings.database_url, sctx) as cur:
                cur.execute(
                    "select 1 from app.visitor_sessions where id = %s and erased_at is null",
                    (sctx.session_id,),
                )
                if cur.fetchone() is None:
                    raise HTTPException(
                        status.HTTP_404_NOT_FOUND, "Unknown visitor session"
                    )
            return sctx
        return ctx
    return visitor_context(authorization, settings)


def any_caller(
    authorization: str | None = Header(default=None),
    x_service_key: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_session_id: str | None = Header(default=None),
    settings: Settings = Depends(settings_dep),
) -> Context:
    """Visitor, service or staff (feedback can come from any of them)."""
    token = _bearer(authorization)
    if token and read_token(token_keys(settings), token, "access"):
        return staff_context(authorization, settings)
    return visitor_or_service(
        authorization, x_service_key, x_tenant_id, x_session_id, settings
    )
