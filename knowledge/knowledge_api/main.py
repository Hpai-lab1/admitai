"""Module A API: run with  uvicorn knowledge_api.main:app --host 127.0.0.1 --port 8100"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__, problems
from .config import Settings, check_production
from .embedder import Embedder, make_embedder
from .rerank import make_reranker
from .service_auth import ServiceAuth
from .assistant_client import Assistant, AssistantClient
from .routes import catalogue, improve, kb, learn, selflearn


def create_app(
    settings: Settings | None = None,
    embedder: Embedder | None = None,
    assistant: Assistant | None = None,
) -> FastAPI:
    settings = settings or Settings()
    check_production(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        """A restart during an evaluation must not leave a proposal 'evaluating' for ever."""
        if settings.worker_database_url:
            from .gate import recover_interrupted, resume_interrupted, sweep_stale
            from .learning import propose_waiting_corrections

            st = _app.state
            try:
                # an approval cut off by this restart is tested again, not left "being tested"
                resume_interrupted(settings, st.embedder, st.assistant)
                recover_interrupted(settings)
            except Exception:  # noqa: BLE001 - never block start-up; the nightly run retries
                pass

            def recover_in_background():
                try:  # a correction cut off before it became a proposal
                    propose_waiting_corrections(settings, st.embedder, st.assistant)
                except Exception:  # noqa: BLE001 - the nightly run picks it up
                    pass
                while not stop.wait(settings.sweep_interval_s):
                    try:  # uploads and runs cut off by a restart stop showing "processing"
                        sweep_stale(settings)
                    except Exception:  # noqa: BLE001 - try again at the next sweep
                        pass

            stop = threading.Event()
            if settings.sweep_interval_s > 0:
                threading.Thread(
                    target=recover_in_background, name="recovery", daemon=True
                ).start()
            worker = None
            if settings.self_learning_enabled:
                # Self-learning (docs/self-learning): off by default; nothing starts without it,
                # and never without a session salt.
                from .selflearn import log as selflearn_log
                from .selflearn.worker import Worker

                if settings.learning_session_salt:
                    worker = Worker(settings, st.embedder, st.assistant)
                    worker.start()
                else:
                    selflearn_log.error(
                        "self-learning is on but LEARNING_SESSION_SALT is not set: worker not started"
                    )
            yield
            stop.set()
            if worker is not None:
                worker.close()
            return
        yield

    app = FastAPI(
        lifespan=lifespan,
        title="AdmitAI Knowledge API (Module A)",
        version=__version__,
        description="Knowledge base, program catalogue, curated Q&A, hybrid search and the learning loop. "
        "Proposed for contracts/knowledge.openapi.yaml.",
    )
    app.state.settings = settings
    app.state.embedder = embedder or make_embedder(settings)
    app.state.reranker = make_reranker(settings.reranker)
    app.state.assistant = assistant or AssistantClient(settings)
    app.add_middleware(ServiceAuth, settings=settings)
    problems.install(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.web_origin.split(",") if o.strip()],
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type", "X-Tenant-Id"],
        expose_headers=["X-Trace-Id", "Retry-After"],
    )
    for module in (kb, catalogue, learn, improve, selflearn):
        app.include_router(module.router)

    @app.get("/health", tags=["ops"])
    def health() -> dict:
        return {"status": "ok", "module": "knowledge", "version": __version__}

    return app


app = create_app()
