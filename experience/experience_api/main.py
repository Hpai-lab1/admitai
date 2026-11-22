"""Module C API: run with  uvicorn experience_api.main:app --host 127.0.0.1 --port 8200"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__, problems
from .config import Settings, check_production
from .routes import (
    admin,
    analytics,
    approvals,
    auth,
    catalogue,
    consents_leads,
    fallback,
    feedback,
    handoffs,
    notifications,
    photos,
    platform,
    privacy,
    programmes,
    signals,
    today,
    webhooks,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    check_production(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        """Events left in the outbox are retried every minute (a correction saved while Module A
        was restarting would otherwise wait for the next staff action)."""
        stop = threading.Event()

        def retry():
            from .clients import deliver_waiting

            while not stop.wait(settings.outbox_retry_s):
                try:
                    deliver_waiting(settings)
                except Exception:  # noqa: BLE001 - try again next minute
                    logging.getLogger("admitai.outbox").exception("outbox retry")

        if settings.worker_database_url and settings.outbox_retry_s > 0:
            threading.Thread(target=retry, name="outbox-retry", daemon=True).start()

        def retention():
            from .privacy import sweep_all

            log = logging.getLogger("admitai.retention")
            # first run a minute after start-up, then every RETENTION_SWEEP_HOURS
            wait = 60.0
            while not stop.wait(wait):
                wait = settings.retention_sweep_hours * 3600
                from datetime import datetime, timezone

                from .privacy import LAST_SWEEP

                try:
                    done = sweep_all(settings)
                    LAST_SWEEP.update(
                        at=datetime.now(timezone.utc).isoformat(),
                        ok=True,
                        colleges=len(done["tenants"]),
                        audit_rows_removed=done["audit_rows_removed"],
                    )
                    log.info("retention sweep: %s", done)
                except Exception as exc:  # noqa: BLE001 - try again at the next sweep
                    LAST_SWEEP.update(
                        at=datetime.now(timezone.utc).isoformat(),
                        ok=False,
                        error=type(exc).__name__,
                    )
                    log.exception("retention sweep")

        if settings.worker_database_url and settings.retention_sweep_hours > 0:
            threading.Thread(target=retention, name="retention", daemon=True).start()
        yield
        stop.set()

    app = FastAPI(
        lifespan=lifespan,
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
        fallback,
        programmes,
        approvals,
        catalogue,
        analytics,
        admin,
        notifications,
        photos,
        platform,
        signals,
        privacy,
        today,
        webhooks,
    ):
        app.include_router(module.router)

    @app.get("/health", tags=["ops"])
    def health() -> dict:
        from .privacy import LAST_SWEEP

        return {
            "status": "ok",
            "module": "experience",
            "version": __version__,
            "retention": dict(LAST_SWEEP) or None,
            # Without an SMTP server no e-mail leaves (acknowledgements, alerts, guardian codes).
            "email": "configured"
            if settings.smtp_host
            else "not configured (SMTP_HOST)",
        }

    return app


app = create_app()
