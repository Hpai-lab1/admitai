"""Module B API: run with  uvicorn assistant_api.main:app --host 127.0.0.1 --port 8150"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__, problems
from .clients import OpenAICompatibleLLM
from .config import Settings, check_production
from .engine import Deps
from .guard import RateLimiter
from .knowledge import KnowledgeClient
from .experience_client import Experience, ExperienceClient
from .routes import channels, chat, voice as voice_routes
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
        OpenAICompatibleLLM(settings),
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
    app.state.speech = speech or Speech(
        settings.voice_model_dir,
        settings.whisper_size,
        settings.piper_voice,
        whisper_size_indic=settings.whisper_size_indic,
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

    @app.get("/health", tags=["ops"])
    def health() -> dict:
        from .prompts import AGENT_VERSION, PROMPT_VERSION

        # The prompt and agent versions let Module A's gate know whether answers may have changed.
        return {
            "status": "ok",
            "module": "assistant",
            "version": __version__,
            "agent": AGENT_VERSION,
            "prompt": PROMPT_VERSION,
        }

    return app


app = create_app()
