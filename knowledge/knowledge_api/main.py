"""Module A API: run with  uvicorn knowledge_api.main:app --host 127.0.0.1 --port 8100"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__, problems
from .config import Settings, check_production
from .embedder import Embedder, make_embedder
from .rerank import make_reranker
from .service_auth import ServiceAuth
from .assistant_client import Assistant, AssistantClient
from .routes import catalogue, improve, kb, learn


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
        if settings.admin_database_url:
            from .gate import recover_interrupted

            try:
                recover_interrupted(settings)
            except Exception:  # noqa: BLE001 - never block start-up; the nightly run retries
                pass
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
    for module in (kb, catalogue, learn, improve):
        app.include_router(module.router)

    @app.get("/health", tags=["ops"])
    def health() -> dict:
        return {"status": "ok", "module": "knowledge", "version": __version__}

    return app


app = create_app()
