"""Module B API: run with  uvicorn assistant_api.main:app --host 127.0.0.1 --port 8150"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__, problems
from .clients import make_llm
from .config import Settings, check_production
from .engine import Deps
from .guard import RateLimiter
from .knowledge import KnowledgeClient
from .experience_client import Experience, ExperienceClient
from .routes import channels, chat, voice as voice_routes
from .speech import MultilingualSpeech
from .voice import Speech
from .whatsapp import OutboxSender, Sender


def create_app(
    settings: Settings | None = None,
    deps: Deps | None = None,
    experience: Experience | None = None,
    wa_sender: Sender | None = None,
    speech: Speech | None = None,
) -> FastAPI:
    settings = settings or Settings()
    check_production(settings)
    app = FastAPI(
        title="AdmitAI Assistant API (Module B)",
        version=__version__,
        description="Router, Answer agent with role packs, sentence-checked streaming, lead rules and "
        "channels. Proposed for contracts/assistant.openapi.yaml.",
    )
    app.state.settings = settings
    app.state.deps = deps or Deps(
        settings,
        KnowledgeClient(settings),
        make_llm(settings),  # local gateway; Gemini only for colleges that opt in
        RateLimiter(settings.rate_limit_per_minute, 60),
    )
    problems.install(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.web_origin.split(",") if o.strip()],
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-Trace-Id", "Retry-After"],
    )
    app.state.limits = {
        "ip": RateLimiter(settings.rate_limit_per_ip_minute, 60),
        "tenant": RateLimiter(settings.rate_limit_per_tenant_minute, 60),
    }
    app.state.experience = experience or ExperienceClient(settings)
    app.state.wa_sender = wa_sender or OutboxSender(settings.database_url)
    app.include_router(chat.router)
    app.include_router(channels.router)
    # Local Whisper/Piper, with Sarvam for Hindi, Kannada and code-mixed speech when configured
    # (speech.py). A speech object passed in (tests) is used as it is.
    app.state.speech = speech or MultilingualSpeech.from_settings(
        settings,
        Speech(
            settings.voice_model_dir,
            settings.whisper_size,
            settings.piper_voice,
            whisper_size_indic=settings.whisper_size_indic,
        ),
    )
    if speech is None and settings.voice_preload:
        import threading

        def preload() -> None:
            try:  # a missing model only means voice loads on first use, as before
                app.state.speech.preload()
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=preload, name="voice-preload", daemon=True).start()
    app.include_router(voice_routes.router)
    if settings.retention_sweep_hours > 0 and settings.worker_database_url:
        import logging
        import threading

        def retention() -> None:
            from .privacy import sweep_all

            log = logging.getLogger("admitai.retention")
            stop = threading.Event()
            wait = 60.0  # first run a minute after start-up
            while not stop.wait(wait):
                wait = settings.retention_sweep_hours * 3600
                from datetime import datetime, timezone

                from .privacy import LAST_SWEEP

                try:
                    done = sweep_all(settings)
                    erased = sum(len(r["conversations"]) for r in done.values())
                    LAST_SWEEP.update(
                        at=datetime.now(timezone.utc).isoformat(),
                        ok=True,
                        colleges=len(done),
                        conversations_erased=erased,
                    )
                    log.info(
                        "retention sweep: %s colleges, %s erased", len(done), erased
                    )
                except Exception as exc:  # noqa: BLE001 - try again at the next sweep
                    LAST_SWEEP.update(
                        at=datetime.now(timezone.utc).isoformat(),
                        ok=False,
                        error=type(exc).__name__,
                    )
                    log.exception("retention sweep")

        # On start-up only (uvicorn), never on import or in a test client: `app = create_app()`
        # at the bottom of this file must not start a sweep of the database in a test run.
        app.router.on_startup.append(
            lambda: threading.Thread(
                target=retention, name="retention", daemon=True
            ).start()
        )
    from .routes import retell as retell_routes

    app.include_router(retell_routes.router)
    # Vapi phone calls (vapi.py), English; separate from Retell.
    from . import vapi as vapi_client
    from .routes import vapi as vapi_routes

    app.state.vapi = vapi_client.VapiClient.from_settings(settings)
    app.include_router(vapi_routes.router)

    @app.get("/health", tags=["ops"])
    def health() -> dict:
        from .privacy import LAST_SWEEP
        from .prompts import AGENT_VERSION, PROMPT_VERSION

        # The prompt and agent versions let Module A's gate know whether answers may have changed.
        return {
            "status": "ok",
            "module": "assistant",
            "version": __version__,
            "agent": AGENT_VERSION,
            "prompt": PROMPT_VERSION,
            "retention": dict(LAST_SWEEP) or None,
        }

    return app


app = create_app()
