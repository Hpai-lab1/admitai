"""Plug-and-play onboarding (PRD F10.1-F10.4, G4): the Platform Super-Admin creates a college from
its Tenant Pack, sees the readiness checklist, and launches it once the test set passes.

Order of work for a new pack: Module A validates and loads the knowledge first (a pack with any
problem is rejected whole); only then does Module C create the college (status 'onboarding') with
its branding and staff roster, and Module B receive the agent settings. While onboarding, the
widget opens only for the college's own signed-in staff (sandbox preview, F10.4).
"""

from __future__ import annotations

import io
import re
import secrets
import zipfile
from uuid import UUID, uuid4

import httpx
import yaml
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from ..config import Settings, token_keys
from ..db import Context, Jsonb, audit, tx
from ..deps import require_staff, settings_dep
from ..schemas import TenantConfig
from ..security import hash_password
from .admin import temporary_password

router = APIRouter(tags=["platform"])
SUPER = require_staff("super_admin")
PACK_TIMEOUT = httpx.Timeout(
    900.0
)  # embedding a large prospectus takes minutes on a laptop
MAX_PACK_BYTES = 200 * 1024 * 1024
STAFF_ROLES = {"college_admin", "admission_officer", "faculty", "marketing_manager"}


def _pack_yaml(data: bytes) -> dict:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        name = next(
            n for n in zf.namelist() if n == "pack.yaml" or n.endswith("/pack.yaml")
        )
        return yaml.safe_load(zf.read(name)) or {}
    except (zipfile.BadZipFile, StopIteration, yaml.YAMLError):
        raise HTTPException(
            422,
            {"problems": ["The upload is not a Tenant Pack (no readable pack.yaml)."]},
        ) from None


def _c_config(pack: dict) -> dict:
    b = pack.get("branding", {}) or {}
    raw = {
        "assistant_name": b.get("assistant_name"),
        "greeting": b.get("greeting"),
        "primary_color": b.get("primary_color"),
        "secondary_color": b.get("secondary_color"),
        "logo_url": b.get("logo_url"),
        "starter_questions": pack.get("starter_questions"),
        "departments": pack.get("departments"),
        "office_hours": pack.get("office_hours"),
        "response_time_hours": pack.get("response_time_hours"),
        "privacy_notice_url": pack.get("privacy_notice_url"),
    }
    cfg = TenantConfig(**{k: v for k, v in raw.items() if v is not None}).model_dump()
    cfg["launch"] = pack.get("launch") or {}
    return cfg


def _staff_problems(pack: dict) -> list[str]:
    problems, depts = [], set(pack.get("departments") or [])
    for i, s in enumerate(pack.get("staff") or [], start=1):
        if s.get("role") not in STAFF_ROLES:
            problems.append(
                f"staff #{i}: role must be one of {', '.join(sorted(STAFF_ROLES))}"
            )
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", str(s.get("email", ""))):
            problems.append(f"staff #{i}: a valid e-mail is required")
        unknown = [d for d in s.get("departments") or [] if d not in depts]
        if unknown:
            problems.append(f"staff #{i}: unknown department(s) {', '.join(unknown)}")
        if s.get("role") == "faculty" and not s.get("departments"):
            problems.append(f"staff #{i}: faculty need at least one department")
    return problems


def _staff_readiness(cur, departments: list[str]) -> list[dict]:
    """C's part of the checklist (F10.2): at least one officer, and faculty for every department."""
    cur.execute(
        "select p.role, coalesce(array_agg(d.department) filter (where d.department is not null), '{}') as depts"
        " from app.profiles p left join app.profile_departments d on d.profile_id = p.id"
        " where p.is_active group by p.id, p.role"
    )
    staff = cur.fetchall()
    officers = sum(
        1 for s in staff if s["role"] in ("admission_officer", "college_admin")
    )
    covered = {d for s in staff if s["role"] == "faculty" for d in s["depts"]}
    missing = [d for d in departments if d not in covered]
    return [
        {
            "key": "officer",
            "label": "At least one admission officer",
            "ok": officers > 0,
            "detail": f"{officers} officer(s) or admins",
            "blocking": True,
        },
        {
            "key": "faculty",
            "label": "A faculty member for every department",
            "ok": not missing,
            "detail": ", ".join(missing),
            "blocking": True,
        },
    ]


def _a(
    settings: Settings, method: str, path: str, tenant: UUID, **kw
) -> httpx.Response:
    try:
        return httpx.request(
            method,
            f"{settings.knowledge_api_url}{path}",
            headers={
                "X-Tenant-Id": str(tenant),
                "X-Service-Key": settings.key_to_knowledge,
            },
            timeout=PACK_TIMEOUT,
            **kw,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            502, f"Knowledge service unavailable ({exc.__class__.__name__})"
        ) from None


def _b_problems(settings: Settings, tenant: UUID, pack: dict) -> list[str]:
    try:
        r = httpx.post(
            f"{settings.assistant_api_url}/tenant-pack/validate",
            json=pack,
            timeout=30,
            headers={
                "X-Service-Key": settings.key_to_assistant,
                "X-Tenant-Id": str(tenant),
            },
        )
        r.raise_for_status()
        return list(r.json().get("problems") or [])
    except httpx.HTTPError as exc:
        raise HTTPException(
            502,
            f"Assistant service could not check the pack ({exc.__class__.__name__})",
        ) from None


def _b(settings: Settings, path: str, tenant: UUID, body: dict) -> None:
    try:
        r = httpx.post(
            f"{settings.assistant_api_url}{path}",
            json=body,
            timeout=30,
            headers={
                "X-Service-Key": settings.key_to_assistant,
                "X-Tenant-Id": str(tenant),
            },
        )
        if r.status_code == 422:
            raise HTTPException(
                422, {"problems": r.json().get("problems") or [r.json().get("detail")]}
            )
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(
            502, f"Assistant service refused the pack ({exc.__class__.__name__})"
        ) from None


def _ctx(ctx: Context, tenant: UUID) -> Context:
    """The Super-Admin acting on the new college (RLS lets them seed it only while onboarding)."""
    return Context(tenant, "super_admin", profile_id=ctx.profile_id)


@router.post(
    "/platform/tenants",
    status_code=201,
    summary="Create a college from its Tenant Pack (zip)",
)
async def create_tenant(
    file: UploadFile = File(...),
    ctx: Context = Depends(SUPER),
    settings: Settings = Depends(settings_dep),
):
    data = await file.read(MAX_PACK_BYTES + 1)
    if len(data) > MAX_PACK_BYTES:
        raise HTTPException(413, "A Tenant Pack may be at most 200 MB")
    pack = _pack_yaml(data)
    college = pack.get("college") or {}
    slug = str(college.get("slug") or "")
    problems = _staff_problems(pack)
    with tx(settings.database_url, ctx) as cur:
        cur.execute("select 1 from app.tenants where slug = %s", (slug,))
        if slug and cur.fetchone():
            problems.append(f"A college with slug '{slug}' already exists.")
    tenant = uuid4()
    # Module B's part (e.g. the model policy) is checked before Module A loads anything, so a
    # bad pack never leaves a half-onboarded college behind.
    problems += _b_problems(settings, tenant, pack)
    if problems:
        raise HTTPException(422, {"problems": problems})
    r = _a(
        settings,
        "POST",
        "/tenant-pack",
        tenant,
        files={"file": ("pack.zip", data, "application/zip")},
    )
    if r.status_code == 422:
        body = r.json()
        raise HTTPException(
            422, {"problems": body.get("problems") or [body.get("detail")]}
        )
    if r.status_code >= 400:
        raise HTTPException(
            502, f"Knowledge service could not load the pack ({r.status_code})"
        )
    knowledge = r.json()
    public_key = f"pk_{re.sub('[^a-z0-9]', '_', slug.lower())}_{secrets.token_hex(6)}"
    created = []
    with tx(settings.database_url, _ctx(ctx, tenant)) as cur:
        cur.execute(
            "insert into app.tenants (id, slug, name, public_key, config, status) values (%s,%s,%s,%s,%s,'onboarding')",
            (tenant, slug, college["name"], public_key, Jsonb(_c_config(pack))),
        )
        for s in pack.get("staff") or []:
            pw = temporary_password()
            cur.execute(
                "insert into app.profiles (tenant_id, email, full_name, role, password_hash) values (%s,%s,%s,%s,%s)"
                " returning id",
                (
                    tenant,
                    s["email"].lower(),
                    s.get("name") or s["email"],
                    s["role"],
                    hash_password(pw),
                ),
            )
            pid = cur.fetchone()["id"]
            for d in dict.fromkeys(s.get("departments") or []):
                cur.execute(
                    "insert into app.profile_departments (tenant_id, profile_id, department)"
                    " values (%s,%s,%s)",
                    (tenant, pid, d),
                )
            created.append(
                {
                    "email": s["email"].lower(),
                    "role": s["role"],
                    "temporary_password": pw,
                }
            )
        audit(
            cur,
            _ctx(ctx, tenant),
            "tenant.created",
            "tenant",
            tenant,
            after={"slug": slug},
        )
        staff_items = _staff_readiness(cur, pack.get("departments") or [])
    _b(settings, "/tenant-pack", tenant, pack)
    return {
        "id": str(tenant),
        "slug": slug,
        "status": "onboarding",
        "public_key": public_key,
        "staff": created,  # temporary passwords are shown once (PRD 5.4: invite-only accounts)
        "loaded": knowledge["loaded"],
        "readiness": knowledge["readiness"] + staff_items,
    }


@router.get(
    "/platform/tenants", summary="All colleges on the platform (no personal data)"
)
def list_tenants(
    ctx: Context = Depends(SUPER), settings: Settings = Depends(settings_dep)
):
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "select id, slug, name, status, created_at from app.tenants order by created_at"
        )
        return {"items": cur.fetchall()}


def _tenant(cur, tenant: UUID) -> dict:
    cur.execute("select * from app.tenants where id = %s", (tenant,))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "College not found")
    return row


def _readiness(
    settings: Settings, ctx: Context, tenant: UUID
) -> tuple[dict, list[dict]]:
    with tx(settings.database_url, _ctx(ctx, tenant)) as cur:
        t = _tenant(cur, tenant)
        staff_items = _staff_readiness(
            cur, (t["config"] or {}).get("departments") or []
        )
    launch = (t["config"] or {}).get("launch") or {}
    params = (
        {"min_test_questions": launch["min_test_questions"]}
        if launch.get("min_test_questions")
        else {}
    )
    r = _a(settings, "GET", "/tenant-pack/readiness", tenant, params=params)
    if r.status_code >= 400:
        raise HTTPException(502, "Knowledge service could not report readiness")
    return t, r.json()["items"] + staff_items


@router.get(
    "/platform/tenants/{tenant}/readiness", summary="Readiness checklist (F10.2, F10.6)"
)
def readiness(
    tenant: UUID,
    ctx: Context = Depends(SUPER),
    settings: Settings = Depends(settings_dep),
):
    t, items = _readiness(settings, ctx, tenant)
    return {
        "status": t["status"],
        "ready": all(i["ok"] for i in items if i["blocking"]),
        "items": items,
    }


@router.post(
    "/platform/tenants/{tenant}/launch",
    status_code=202,
    summary="Run the launch test set (F10.3); the college goes live only if it passes",
)
def launch(
    tenant: UUID,
    ctx: Context = Depends(SUPER),
    settings: Settings = Depends(settings_dep),
):
    t, items = _readiness(settings, ctx, tenant)
    if t["status"] != "onboarding":
        raise HTTPException(409, f"This college is already {t['status']}")
    blocking = [
        i["label"] + (f": {i['detail']}" if i["detail"] else "")
        for i in items
        if i["blocking"] and not i["ok"]
    ]
    if blocking:
        raise HTTPException(409, {"problems": blocking})
    r = _a(settings, "POST", "/evals/runs", tenant, json={"label": "launch"})
    if r.status_code >= 400:
        raise HTTPException(502, "Knowledge service could not start the launch test")
    run_id = r.json()["id"]
    with tx(settings.database_url, _ctx(ctx, tenant)) as cur:
        cur.execute(
            "update app.tenants set config = config || %s where id = %s",
            (Jsonb({"launch_run": run_id}), tenant),
        )
        audit(
            cur,
            _ctx(ctx, tenant),
            "tenant.launch_started",
            "tenant",
            tenant,
            after={"run": run_id},
        )
    return {"status": "testing", "run_id": run_id}


@router.get(
    "/platform/tenants/{tenant}/launch",
    summary="Launch test result; goes live when it passes",
)
def launch_status(
    tenant: UUID,
    ctx: Context = Depends(SUPER),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, _ctx(ctx, tenant)) as cur:
        t = _tenant(cur, tenant)
    cfg = t["config"] or {}
    if t["status"] == "live":
        return {"status": "live"}
    if not cfg.get("launch_run"):
        return {"status": t["status"], "detail": "No launch test has been run"}
    launch_cfg = cfg.get("launch") or {}
    params = {
        k: launch_cfg[k] for k in ("min_accuracy", "min_refusal") if k in launch_cfg
    }
    r = _a(
        settings,
        "GET",
        f"/tenant-pack/launch/{cfg['launch_run']}",
        tenant,
        params=params,
    )
    result = r.json()
    if result.get("status") != "done":
        return {"status": "testing"}
    if result["passed"]:
        with tx(settings.database_url, _ctx(ctx, tenant)) as cur:
            cur.execute(
                "update app.tenants set status = 'live' where id = %s and status = 'onboarding'",
                (tenant,),
            )
            audit(
                cur,
                _ctx(ctx, tenant),
                "tenant.live",
                "tenant",
                tenant,
                after={"scores": result["scores"]},
            )
        return {"status": "live", "scores": result["scores"]}
    return {
        "status": "onboarding",
        "scores": result["scores"],
        "reasons": result["reasons"],
    }


def onboarding_preview_allowed(
    settings: Settings, tenant: UUID, authorization: str | None
) -> bool:
    """Sandbox preview (F10.4): while a college is onboarding, its widget opens only for that
    college's own signed-in staff (or the Super-Admin)."""
    from ..security import read_token

    token = (authorization or "").removeprefix("Bearer ").strip()
    claims = read_token(token_keys(settings), token, "access") if token else None
    if not claims:
        return False
    return claims.get("tid") == str(tenant) or claims.get("role") == "super_admin"


__all__ = ["router", "onboarding_preview_allowed", "status"]
