"""Read-only view of the self-learning layer (docs/self-learning/README.md)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..config import Settings
from ..deps import settings_dep, tenant_dep
from ..selflearn import report

router = APIRouter(tags=["learning"])


@router.get(
    "/learning/self/report",
    summary="Self-learning: feedback by class, lessons by status, gate results, rollbacks and "
    "the trust-score distribution for this college (read-only)",
)
def self_learning_report(
    limit: int = Query(50, ge=1, le=500),
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    return report.build(settings, tenant, limit)
