"""Staff sign-in (PRD 5.4): e-mail + password, mandatory TOTP MFA for admins, 15-minute access
tokens and 7-day rotating refresh tokens. Visitor sessions for the widget."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..ratelimit import limit_sessions
from ..config import Settings, token_keys
from ..db import Context, Jsonb, audit, set_profile, tx
from ..deps import service_context, settings_dep, staff_context
from ..schemas import (
    LoginIn,
    Me,
    MfaChallenge,
    MfaIn,
    PasswordChangeIn,
    RefreshIn,
    TenantConfig,
    Tokens,
    WidgetSession,
    WidgetSessionIn,
)
from ..security import (
    hash_password,
    hash_token,
    make_token,
    new_totp_secret,
    password_problem,
    read_token,
    totp_uri,
    verify_password,
    verify_totp,
)

router = APIRouter(tags=["auth"])
MFA_REQUIRED_ROLES = ("super_admin", "college_admin")  # PRD 5.4
BAD_LOGIN = "Wrong college, e-mail or password, or the account is switched off"


def _tenant(cur, slug: str) -> dict | None:
    cur.execute("select * from app.tenant_by_slug(%s)", (slug,))
    return cur.fetchone()


def _issue_tokens(cur, settings: Settings, tenant_id: UUID, profile: dict) -> Tokens:
    cur.execute(
        "select department from app.profile_departments where profile_id = %s order by department",
        (profile["id"],),
    )
    departments = [r["department"] for r in cur.fetchall()]
    access = make_token(
        token_keys(settings),
        "access",
        settings.access_minutes,
        sub=profile["id"],
        tid=tenant_id,
        role=profile["role"],
    )
    refresh = f"{tenant_id}.{profile['id']}.{secrets.token_urlsafe(32)}"
    cur.execute(
        "insert into app.refresh_tokens (tenant_id, profile_id, token_hash, expires_at) values (%s, %s, %s, %s)",
        (
            tenant_id,
            profile["id"],
            hash_token(refresh),
            datetime.now(timezone.utc) + timedelta(days=settings.refresh_days),
        ),
    )
    return Tokens(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_minutes * 60,
        role=profile["role"],
        name=profile["full_name"],
        departments=departments,
    )


@router.post(
    "/auth/login",
    response_model=Tokens | MfaChallenge,
    summary="Staff sign-in; admins must complete MFA (PRD 5.4)",
)
def login(body: LoginIn, settings: Settings = Depends(settings_dep)):
    with tx(settings.database_url, None) as cur:
        tenant = _tenant(cur, body.tenant.lower())
    if not tenant:
        verify_password(body.password, "scrypt$16384$8$1$00$00")  # same work either way
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN)
    ctx = Context(tenant["id"], "login")
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "select * from app.profiles where lower(email) = lower(%s)", (body.email,)
        )
        p = cur.fetchone()
        if (
            not p
            or not p["is_active"]
            or not verify_password(body.password, p["password_hash"])
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN)
        set_profile(
            cur, p["id"]
        )  # lets the RLS policies see this as the person's own row
        if p["mfa_enabled"] or p["role"] in MFA_REQUIRED_ROLES:
            enrol = not p["mfa_enabled"]
            secret = None
            if enrol:
                secret = new_totp_secret()
                cur.execute(
                    "update app.profiles set mfa_secret = %s, updated_at = now() where id = %s",
                    (secret, p["id"]),
                )
            mfa_token = make_token(
                token_keys(settings), "mfa", 5, sub=p["id"], tid=tenant["id"]
            )
            return MfaChallenge(
                status="mfa_enroll" if enrol else "mfa_required",
                mfa_token=mfa_token,
                otpauth_uri=totp_uri(secret, p["email"]) if secret else None,
                secret=secret,
            )
        tokens = _issue_tokens(cur, settings, tenant["id"], p)
        audit(
            cur,
            Context(tenant["id"], p["role"], profile_id=p["id"]),
            "auth.login",
            "profile",
            p["id"],
        )
        return tokens


def _dev_code(settings: Settings, code: str) -> bool:
    """Local development only (DEV_ECHO_CODES=1 and DEV_MFA_CODE set): a fixed sign-in code."""
    import hmac

    fixed = settings.dev_mfa_code
    return bool(settings.dev_echo_codes and fixed) and hmac.compare_digest(code, fixed)


@router.post(
    "/auth/mfa",
    response_model=Tokens,
    summary="Second step: TOTP code from an authenticator app",
)
def mfa(body: MfaIn, settings: Settings = Depends(settings_dep)):
    claims = read_token(token_keys(settings), body.mfa_token, "mfa")
    if not claims:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "The sign-in took too long; start again"
        )
    tenant_id, profile_id = UUID(claims["tid"]), UUID(claims["sub"])
    with tx(
        settings.database_url, Context(tenant_id, "login", profile_id=profile_id)
    ) as cur:
        cur.execute("select * from app.profiles where id = %s", (profile_id,))
        p = cur.fetchone()
        if (
            not p
            or not p["is_active"]
            or not p["mfa_secret"]
            or not (
                verify_totp(p["mfa_secret"], body.code)
                or _dev_code(settings, body.code)
            )
        ):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "That code is not right; check the time on your phone",
            )
        if not p["mfa_enabled"]:
            cur.execute(
                "update app.profiles set mfa_enabled = true, updated_at = now() where id = %s",
                (profile_id,),
            )
            audit(
                cur,
                Context(tenant_id, p["role"], profile_id=profile_id),
                "auth.mfa_enrolled",
                "profile",
                profile_id,
            )
        tokens = _issue_tokens(cur, settings, tenant_id, p)
        audit(
            cur,
            Context(tenant_id, p["role"], profile_id=profile_id),
            "auth.login",
            "profile",
            profile_id,
        )
        return tokens


def _split_refresh(token: str) -> tuple[UUID, UUID]:
    try:
        tid, pid, _ = token.split(".", 2)
        return UUID(tid), UUID(pid)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in again") from None


@router.post(
    "/auth/refresh",
    response_model=Tokens,
    summary="Rotate the refresh token for a new access token",
)
def refresh(body: RefreshIn, settings: Settings = Depends(settings_dep)):
    tenant_id, profile_id = _split_refresh(body.refresh_token)
    with tx(
        settings.database_url, Context(tenant_id, "login", profile_id=profile_id)
    ) as cur:
        cur.execute(
            "select * from app.refresh_tokens where token_hash = %s and revoked_at is null"
            " and expires_at > now()",
            (hash_token(body.refresh_token),),
        )
        row = cur.fetchone()
        cur.execute("select * from app.profiles where id = %s", (profile_id,))
        p = cur.fetchone()
        if not row or not p or not p["is_active"]:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in again")
        cur.execute(
            "update app.refresh_tokens set revoked_at = now() where id = %s",
            (row["id"],),
        )
        return _issue_tokens(cur, settings, tenant_id, p)


@router.post("/auth/logout", status_code=204, summary="Revoke a refresh token")
def logout(body: RefreshIn, settings: Settings = Depends(settings_dep)):
    tenant_id, profile_id = _split_refresh(body.refresh_token)
    with tx(
        settings.database_url, Context(tenant_id, "login", profile_id=profile_id)
    ) as cur:
        cur.execute(
            "update app.refresh_tokens set revoked_at = now() where token_hash = %s and revoked_at is null",
            (hash_token(body.refresh_token),),
        )


def _theme(config: dict | None) -> dict:
    cfg = TenantConfig(
        **{k: v for k, v in (config or {}).items() if k in TenantConfig.model_fields}
    )
    keys = ("primary_color", "secondary_color", "accent_color", "font", "logo_url")
    return {k: getattr(cfg, k) for k in keys}


@router.get("/auth/me", response_model=Me)
def me(
    ctx: Context = Depends(staff_context), settings: Settings = Depends(settings_dep)
):
    with tx(settings.database_url, ctx) as cur:
        cur.execute("select * from app.profiles where id = %s", (ctx.profile_id,))
        p = cur.fetchone()
        cur.execute(
            "select id, slug, name, public_key, config from app.tenants where id = %s",
            (ctx.tenant_id,),
        )
        t = cur.fetchone()
    return Me(
        id=p["id"],
        email=p["email"],
        full_name=p["full_name"],
        role=p["role"],
        departments=list(ctx.departments),
        mfa_enabled=p["mfa_enabled"],
        tenant={
            "id": str(t["id"]),
            "slug": t["slug"],
            "name": t["name"],
            "public_key": t["public_key"],
            "theme": _theme(t["config"]),
        },
    )


@router.post("/auth/password", status_code=204, summary="Change your own password")
def change_password(
    body: PasswordChangeIn,
    ctx: Context = Depends(staff_context),
    settings: Settings = Depends(settings_dep),
):
    problem = password_problem(body.new_password)
    if problem:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, problem)
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "select password_hash from app.profiles where id = %s", (ctx.profile_id,)
        )
        if not verify_password(body.current_password, cur.fetchone()["password_hash"]):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Your current password is not right"
            )
        cur.execute(
            "update app.profiles set password_hash = %s, updated_at = now() where id = %s",
            (hash_password(body.new_password), ctx.profile_id),
        )
        cur.execute(
            "update app.refresh_tokens set revoked_at = now() where profile_id = %s and revoked_at is null",
            (ctx.profile_id,),
        )
        audit(cur, ctx, "auth.password_changed", "profile", ctx.profile_id)


# ------------------------------------------------------------------ channel sessions (Module B)
class ChannelSessionIn(BaseModel):
    channel: Literal["whatsapp", "web_voice"]
    utm: dict[str, str] = Field(default_factory=dict)


@router.post(
    "/channels/sessions",
    status_code=201,
    tags=["channels"],
    summary="Module B opens a visitor session for a non-browser channel (WhatsApp), so consent, "
    "leads and callbacks have a session to belong to",
)
def channel_session(
    body: ChannelSessionIn,
    ctx: Context = Depends(service_context),
    settings: Settings = Depends(settings_dep),
):
    session_id = uuid4()
    expires = datetime.now(timezone.utc) + timedelta(days=30)
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "insert into app.visitor_sessions (id, tenant_id, channel, utm, expires_at) values (%s, %s, %s, %s, %s)",
            (session_id, ctx.tenant_id, body.channel, Jsonb(body.utm), expires),
        )
    return {"session_id": str(session_id), "expires_at": expires.isoformat()}


# ------------------------------------------------------------------ widget visitor session
@router.post(
    "/widget/session",
    response_model=WidgetSession,
    tags=["widget"],
    summary="Start an anonymous visitor session for a college's widget (PRD 5.4, F1.1)",
)
def widget_session(
    body: WidgetSessionIn,
    request: Request,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(settings_dep),
):
    limit_sessions(request, settings)
    with tx(settings.database_url, None) as cur:
        cur.execute("select * from app.tenant_by_public_key(%s)", (body.public_key,))
        t = cur.fetchone()
        if t:
            cur.execute("select app.tenant_status(%s) as s", (t["id"],))
            t_status = cur.fetchone()["s"]
    if not t:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown widget key")
    if t_status == "onboarding":
        from .platform import onboarding_preview_allowed

        # F10.4 sandbox preview: before launch, only the college's own staff can open the widget.
        if not onboarding_preview_allowed(settings, t["id"], authorization):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "This college's assistant is not live yet"
            )
    session_id = uuid4()
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.visitor_hours)
    with tx(
        settings.database_url, Context(t["id"], "visitor", session_id=session_id)
    ) as cur:
        cur.execute(
            "insert into app.visitor_sessions (id, tenant_id, channel, utm, referrer, expires_at)"
            " values (%s, %s, %s, %s, %s, %s)",
            (
                session_id,
                t["id"],
                body.channel,
                Jsonb(body.utm),
                body.referrer,
                expires,
            ),
        )
    cfg = TenantConfig(
        **{
            k: v
            for k, v in (t["config"] or {}).items()
            if k in TenantConfig.model_fields
        }
    )
    token = make_token(
        token_keys(settings),
        "visitor",
        settings.visitor_hours * 60,
        sid=session_id,
        tid=t["id"],
    )
    return WidgetSession(
        session_id=session_id,
        session_token=token,
        expires_at=expires,
        tenant={
            "id": str(t["id"]),
            "name": t["name"],
            **cfg.model_dump(exclude={"departments"}),
            "departments": cfg.departments,
        },
    )
