"""College admin screens (PRD 5.3, F10.1): staff accounts, college settings, audit log."""

from __future__ import annotations

import secrets
import string
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from psycopg.errors import UniqueViolation

from ..config import Settings
from ..db import Context, Jsonb, audit, tx
from ..deps import require_staff, settings_dep
from ..schemas import TenantConfig, User, UserCreated, UserIn, UserPatch
from ..security import hash_password, password_problem

router = APIRouter(tags=["admin"])
ADMIN = require_staff("college_admin")

USERS_SQL = """
select p.id, p.email, p.full_name, p.role, p.is_active, p.mfa_enabled, p.created_at,
       coalesce(array_agg(d.department order by d.department) filter (where d.department is not null), '{}')
         as departments
from app.profiles p left join app.profile_departments d on d.profile_id = p.id
"""


def temporary_password() -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(16))
        if not password_problem(pw):
            return pw


def _user(cur, user_id: UUID) -> dict:
    cur.execute(USERS_SQL + " where p.id = %s group by p.id", (user_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Staff member not found")
    return row


def _check_departments(cur, role: str, departments: list[str]) -> None:
    if role == "faculty" and not departments:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Faculty need at least one department"
        )
    cur.execute(
        "select config->'departments' as d from app.tenants where id = app.current_tenant()"
    )
    known = (cur.fetchone() or {}).get("d") or []
    unknown = [d for d in departments if known and d not in known]
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unknown department: {', '.join(unknown)}",
        )


def _set_departments(cur, ctx: Context, user_id: UUID, departments: list[str]) -> None:
    cur.execute("delete from app.profile_departments where profile_id = %s", (user_id,))
    for d in dict.fromkeys(departments):
        cur.execute(
            "insert into app.profile_departments (tenant_id, profile_id, department) values (%s, %s, %s)",
            (ctx.tenant_id, user_id, d),
        )


@router.get(
    "/admin/users", response_model=list[User], summary="Staff accounts of this college"
)
def list_users(
    ctx: Context = Depends(ADMIN), settings: Settings = Depends(settings_dep)
):
    with tx(settings.database_url, ctx) as cur:
        cur.execute(USERS_SQL + " group by p.id order by p.role, p.full_name")
        return cur.fetchall()


@router.post(
    "/admin/users",
    response_model=UserCreated,
    status_code=201,
    summary="Create a staff account; the temporary password is shown once",
)
def create_user(
    body: UserIn,
    ctx: Context = Depends(ADMIN),
    settings: Settings = Depends(settings_dep),
):
    pw = temporary_password()
    try:
        with tx(settings.database_url, ctx) as cur:
            _check_departments(cur, body.role, body.departments)
            cur.execute(
                "insert into app.profiles (tenant_id, email, full_name, role, password_hash)"
                " values (%s, %s, %s, %s, %s) returning id",
                (
                    ctx.tenant_id,
                    body.email,
                    body.full_name,
                    body.role,
                    hash_password(pw),
                ),
            )
            uid = cur.fetchone()["id"]
            _set_departments(cur, ctx, uid, body.departments)
            audit(
                cur,
                ctx,
                "user.created",
                "profile",
                uid,
                after={"role": body.role, "departments": body.departments},
            )
            row = _user(cur, uid)
    except UniqueViolation:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Someone with that e-mail already has an account"
        ) from None
    return UserCreated(**row, temporary_password=pw)


@router.patch(
    "/admin/users/{user_id}",
    response_model=User,
    summary="Change role, departments, or switch off",
)
def update_user(
    user_id: UUID,
    body: UserPatch,
    ctx: Context = Depends(ADMIN),
    settings: Settings = Depends(settings_dep),
):
    changes = body.model_dump(exclude_unset=True)
    with tx(settings.database_url, ctx) as cur:
        before = _user(cur, user_id)
        if before["role"] == "super_admin":
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Platform accounts are managed by the operator",
            )
        if user_id == ctx.profile_id and (
            changes.get("is_active") is False
            or changes.get("role", "college_admin") != "college_admin"
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "You can't switch off or demote your own account",
            )
        role = changes.get("role", before["role"])
        departments = changes.get("departments", before["departments"])
        _check_departments(cur, role, departments)
        cols = {
            k: changes[k]
            for k in ("full_name", "role", "is_active")
            if k in changes and changes[k] is not None
        }
        if body.reset_mfa:
            cols.update(mfa_enabled=False, mfa_secret=None)
        if cols:
            sets = ", ".join(f"{k} = %s" for k in cols)
            cur.execute(
                f"update app.profiles set {sets}, updated_at = now() where id = %s",
                (*cols.values(), user_id),
            )
        if "departments" in changes:
            _set_departments(cur, ctx, user_id, departments)
        if {"role", "is_active", "departments"} & changes.keys() or body.reset_mfa:
            # sign the person out everywhere so the new permissions apply now
            cur.execute(
                "update app.refresh_tokens set revoked_at = now() where profile_id = %s and revoked_at is null",
                (user_id,),
            )
        after = _user(cur, user_id)
        audit(
            cur,
            ctx,
            "user.updated",
            "profile",
            user_id,
            before={
                k: before[k]
                for k in ("role", "is_active", "departments", "mfa_enabled")
            },
            after={
                k: after[k] for k in ("role", "is_active", "departments", "mfa_enabled")
            },
        )
        return after


@router.get(
    "/admin/tenant", response_model=TenantConfig, summary="This college's settings"
)
def get_tenant(
    ctx: Context = Depends(ADMIN), settings: Settings = Depends(settings_dep)
):
    with tx(settings.database_url, ctx) as cur:
        cur.execute("select config from app.tenants where id = %s", (ctx.tenant_id,))
        cfg = cur.fetchone()["config"] or {}
    return TenantConfig(
        **{k: v for k, v in cfg.items() if k in TenantConfig.model_fields}
    )


@router.put(
    "/admin/tenant",
    response_model=TenantConfig,
    summary="Update branding, starter questions, SLA",
)
def put_tenant(
    body: TenantConfig,
    ctx: Context = Depends(ADMIN),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "select config from app.tenants where id = %s for update", (ctx.tenant_id,)
        )
        before = cur.fetchone()["config"] or {}
        after = {**before, **body.model_dump()}
        cur.execute(
            "update app.tenants set config = %s, updated_at = now() where id = %s",
            (Jsonb(after), ctx.tenant_id),
        )
        audit(
            cur,
            ctx,
            "tenant.updated",
            "tenant",
            ctx.tenant_id,
            before=before,
            after=after,
        )
    return body


@router.get("/audit", summary="Audit log (college admin only)")
def audit_log(
    action: str | None = Query(None, max_length=60),
    since: datetime | None = None,
    limit: int = Query(200, ge=1, le=1000),
    ctx: Context = Depends(ADMIN),
    settings: Settings = Depends(settings_dep),
):
    where, params = ["true"], []
    if action:
        where.append("a.action like %s")
        params.append(f"{action}%")
    if since:
        where.append("a.at >= %s")
        params.append(since)
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "select a.id, a.at, a.actor_kind, n.full_name as actor, a.action, a.entity, a.entity_id,"
            " a.before, a.after from app.audit_logs a left join app.staff_names() n on n.id = a.actor_id"
            f" where {' and '.join(where)} order by a.id desc limit %s",
            (*params, limit),
        )
        return cur.fetchall()
