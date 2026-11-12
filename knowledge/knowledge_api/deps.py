"""Request dependencies: settings, embedder and the calling tenant."""

from __future__ import annotations

from uuid import UUID

from fastapi import Header, HTTPException, Request, status

from .config import Settings
from .embedder import Embedder


def settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def embedder_dep(request: Request) -> Embedder:
    return request.app.state.embedder


def tenant_dep(
    x_tenant_id: str = Header(..., description="Tenant id (uuid) the call acts for"),
) -> str:
    try:
        return str(UUID(x_tenant_id))
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "X-Tenant-Id must be a uuid"
        ) from None


def reranker_dep(request: Request):
    return getattr(request.app.state, "reranker", None)
