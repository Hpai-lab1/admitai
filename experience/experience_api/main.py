"""Module C API: run with  uvicorn experience_api.main:app --host 127.0.0.1 --port 8200"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__, problems
from .config import Settings, check_production
from .routes import (
    admin,
    analytics,
    approvals,
    auth,
    consents_leads,
    feedback,
    handoffs,
    notifications,
    platform,
    privacy,
    signals,
    webhooks,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    check_production(settings)
    app = FastAPI(
        title="AdmitAI Experience API (Module C)",
        version=__version__,
        description="Widget sessions, consent and leads, human handoff, feedback and corrections, approvals, "
        "analytics and college administration. Proposed for contracts/experience.openapi.yaml.",
    )
    problems.install(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.web_origin.split(",") if o.strip()],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Session-Id"],
        expose_headers=["X-Trace-Id", "Retry-After"],
    )
    for module in (
        auth,
        handoffs,
        consents_leads,
        feedback,
        approvals,
        analytics,
        admin,
        notifications,
        platform,
        signals,
        privacy,
        webhooks,
    ):
        app.include_router(module.router)

    @app.get("/health", tags=["ops"])
    def health() -> dict:
        return {"status": "ok", "module": "experience", "version": __version__}

    return app


app = create_app()
