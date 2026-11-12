"""Every call into Module A needs a service key (PRD 9.1 least privilege). Each caller has its own
key and may reach only what it needs: Module B searches and reads the catalogue; Module C runs the
admin, knowledge and learning screens; operator tooling may do anything. Only /health is open."""

from __future__ import annotations

import hmac

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from .config import Settings
from .problems import problem

OPEN = {"/health"}
DEV_OPEN = {"/docs", "/openapi.json", "/redoc"}
ALLOWED = {
    "assistant": [("POST", "/kb/search"), ("GET", "/catalogue/")],
    "experience": None,  # all endpoints
    "ops": None,
}


def service_for(settings: Settings, key: str | None) -> str | None:
    if not key:
        return None
    for name, expected in (
        ("experience", settings.key_from_experience),
        ("assistant", settings.key_from_assistant),
        ("ops", settings.ops_key),
    ):
        if expected and hmac.compare_digest(key, expected):
            return name
    return None


class ServiceAuth(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings):
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (
            request.method == "OPTIONS"
            or path in OPEN
            or (self.settings.app_env == "development" and path in DEV_OPEN)
        ):
            return await call_next(request)
        name = service_for(self.settings, request.headers.get("X-Service-Key"))
        if name is None:
            return problem(request, 401, "Unknown service")
        rules = ALLOWED[name]
        if rules is not None and not any(
            request.method == m and path.startswith(p) for m, p in rules
        ):
            return problem(request, 403, f"The {name} service may not call this")
        request.state.service = name
        return await call_next(request)
