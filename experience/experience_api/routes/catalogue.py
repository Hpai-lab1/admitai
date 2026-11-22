"""The fee check behind "Correct this answer". The catalogue is what the assistant quotes for fees
and it wins over any correction (PRD 8.3): a correction that names a fee changes the official fee
once approved and tested; one whose figure cannot be tied to a fee is flagged before it is sent."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..clients import UpstreamError, knowledge_get, knowledge_post
from ..config import Settings
from ..db import Context
from ..deps import ADMISSION_DESK, DEPARTMENT_ROLES, require_staff, settings_dep

router = APIRouter(tags=["catalogue"])
CORRECTORS = require_staff("college_admin", *ADMISSION_DESK, *DEPARTMENT_ROLES)


class CheckIn(BaseModel):
    text: str = Field(max_length=8000)


@router.post(
    "/catalogue/check",
    summary="Fees in a correction that differ from the catalogue (shown before it is sent)",
)
def check(
    body: CheckIn,
    ctx: Context = Depends(CORRECTORS),
    settings: Settings = Depends(settings_dep),
):
    try:
        return knowledge_post(
            settings, "/catalogue/check", ctx.tenant_id, {"text": body.text}
        )
    except UpstreamError:
        return {
            "conflicts": [],
            "unavailable": True,
        }  # never block a correction on this


@router.get(
    "/catalogue/programs",
    summary="Programmes, fees and intake as the assistant quotes them (read-only, from Module A)",
)
def programs(
    ctx: Context = Depends(CORRECTORS),
    settings: Settings = Depends(settings_dep),
):
    """Read-only on purpose: staff see the exact values the assistant quotes, but a fee still
    changes only through an approved correction (no edit endpoint here)."""
    try:
        data = knowledge_get(
            settings, "/catalogue/programs", ctx.tenant_id, {"facts": "true"}
        )
    except UpstreamError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
    keep = (
        "code",
        "name",
        "department",
        "level",
        "duration_months",
        "intake",
        "eligibility",
        "contact",
        "academic_year",
        "fees",
    )
    return {"items": [{k: p.get(k) for k in keep} for p in data.get("items", [])]}
