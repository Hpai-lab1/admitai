"""Who is calling: a widget visitor (token issued by Module C) or a trusted service with its own key.

Visitor tokens are verified with Module C's public key in production (RS256), so Module B can check
them but never mint one. Each calling service has its own key and may reach only the endpoints it
needs: a leaked key exposes one caller's slice, not the whole API.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from uuid import UUID

import jwt
from fastapi import Depends, Header, HTTPException, Request, status

from .config import Settings

# (method, path prefix) each calling service may use; "ops" (operator tooling) may use all.
ALLOWED: dict[str, list[tuple[str, str]]] = {
    "experience": [
        ("GET", "/chat/"),
        ("POST", "/tenant-pack"),
        ("GET", "/tenant-settings"),
        ("POST", "/privacy/erase"),
        ("GET", "/analytics/"),
        ("POST", "/channels/whatsapp/guardian-code"),
        ("POST", "/channels/whatsapp/notify"),
        ("POST", "/messages/lookup"),  # the console's review queue (questions masked)
    ],
    "knowledge": [
        ("POST", "/chat/"),
        ("GET", "/signals"),
        ("GET", "/messages/"),
        ("GET", "/reviews/"),
        ("POST", "/reviews/run"),
        ("POST", "/reviews/messages"),
        ("GET", "/tenant-settings"),
        ("PUT", "/tenant-settings"),
        ("DELETE", "/tenant-data"),
        ("POST", "/voice/eval-result"),
    ],
}


@dataclass(frozen=True)
class Caller:
    tenant_id: str
    session_id: str | None
    kind: str  # visitor | service
    service: str | None = None  # experience | knowledge | ops


def settings_dep(request: Request) -> Settings:
    return request.app.state.settings


@lru_cache(maxsize=4)
def _public_key(path: str) -> bytes:
    return Path(path).read_bytes()


def visitor_keys(settings: Settings) -> tuple[str | bytes, str]:
    """(key, algorithm) for checking Module C's visitor tokens."""
    if settings.jwt_public_key_file:
        return _public_key(settings.jwt_public_key_file), "RS256"
    return (
        settings.jwt_secret,
        "HS256",
    )  # development and tests only (see check_production)


def _service(settings: Settings, key: str) -> str | None:
    for name, expected in (
        ("experience", settings.key_from_experience),
        ("knowledge", settings.key_from_knowledge),
        ("ops", settings.service_key),
    ):
        if expected and hmac.compare_digest(key, expected):
            return name
    return None


def caller(
    request: Request,
    authorization: str | None = Header(default=None),
    x_service_key: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_session_id: str | None = Header(default=None),
    settings: Settings = Depends(settings_dep),
) -> Caller:
    if x_service_key:
        name = _service(settings, x_service_key)
        if name is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown service")
        if name != "ops" and not any(
            request.method == m and request.url.path.startswith(p)
            for m, p in ALLOWED[name]
        ):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"The {name} service may not call this"
            )
        try:
            tenant = str(UUID(x_tenant_id or ""))
            session = str(UUID(x_session_id)) if x_session_id else None
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "X-Tenant-Id / X-Session-Id must be uuids"
            ) from None
        return Caller(tenant, session, "service", name)
    token = (authorization or "").removeprefix("Bearer ").strip()
    key, alg = visitor_keys(settings)
    try:
        claims = jwt.decode(token, key, algorithms=[alg])
    except jwt.PyJWTError:
        claims = None
    if not claims or claims.get("typ") != "visitor":
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Session expired; reload the chat"
        )
    return Caller(claims["tid"], claims["sid"], "visitor")


def service_only(c: Caller = Depends(caller)) -> Caller:
    if c.kind != "service":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Service callers only")
    return c


def client_ip(request: Request, settings: Settings) -> str:
    if settings.trust_proxy_headers:
        sent = (
            request.headers.get("cf-connecting-ip")
            or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        )
        if sent:
            return sent
    return request.client.host if request.client else "unknown"


def visitor_limits(request: Request, tenant: str) -> None:
    """PRD 12.3: per-IP and per-tenant limits for visitor traffic, as 429 with Retry-After.
    (Services such as Module A's evaluation runs are not visitors and are not limited here.)"""
    settings, limits = request.app.state.settings, request.app.state.limits
    for name, key in (("ip", client_ip(request, settings)), ("tenant", tenant)):
        if not limits[name].allow(key):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many questions at once. Please wait a moment and try again.",
                headers={"Retry-After": str(limits[name].retry_after(key))},
            )
