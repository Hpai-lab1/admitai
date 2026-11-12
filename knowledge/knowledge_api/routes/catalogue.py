"""Program catalogue (PRD F4.3, 21.4): authoritative facts for Module B's answers and sentence checks."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from .. import catalogue, catalogue_cases, health
from ..config import Settings
from ..db import tx
from ..deps import settings_dep, tenant_dep

router = APIRouter(tags=["catalogue"])


@router.get("/catalogue/programs", summary="Programs (optionally with all their facts)")
def programs(
    academic_year: str | None = None,
    facts: bool = False,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, tenant) as cur:
        items = catalogue.list_programs(cur, academic_year, with_facts=facts)
        dates = catalogue.general_dates(cur, academic_year)
    return {"items": items, "general_dates": dates}


@router.get(
    "/catalogue/programs/{program_id}/facts",
    summary="Exact fees, dates, eligibility and intake for one program",
)
def program_facts(
    program_id: str,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, tenant) as cur:
        facts = catalogue.program_facts(cur, program_id)
    if facts is None:
        raise HTTPException(404, "Program not found")
    return facts


@router.post(
    "/catalogue/import",
    summary="Import the catalogue CSV templates (programs, fees, dates)",
)
async def import_catalogue(
    programs_csv: UploadFile = File(...),
    fees_csv: UploadFile | None = File(None),
    dates_csv: UploadFile | None = File(None),
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    async def text(f: UploadFile | None) -> str:
        return (await f.read()).decode("utf-8-sig") if f is not None else ""

    try:
        data = catalogue.parse_catalogue(
            await text(programs_csv), await text(fees_csv), await text(dates_csv)
        )
    except catalogue.CatalogueError as exc:
        raise HTTPException(422, {"problems": exc.problems}) from None
    with tx(settings.database_url, tenant) as cur:
        result = catalogue.import_catalogue(cur, tenant, data)
    # Catalogue wins (PRD 8.3): curated answers that now disagree are suspended at once.
    result["curated_suspended"] = health.check_catalogue_conflicts(settings, tenant)
    # F10.3: the launch test set follows the catalogue.
    result["eval_cases"] = catalogue_cases.generate(settings, tenant)
    return result
