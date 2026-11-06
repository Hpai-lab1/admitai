"""Approval inbox and knowledge screens (PRD F4.4, F2.x). Module A owns the proposals and the
knowledge base; Module C shows them to the right people and records their decisions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..clients import UpstreamError, knowledge_get, knowledge_post
from ..config import Settings
from ..db import Context, audit, tx
from ..deps import require_staff, settings_dep
from ..schemas import DecideIn, DelegatesIn, PeakSeasonIn, SecondReviewIn

router = APIRouter(tags=["approvals"])
APPROVERS = require_staff("college_admin", "admission_officer", "faculty")


def _upstream(fn, *args):
    try:
        return fn(*args)
    except UpstreamError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None


def can_decide(ctx: Context, proposal: dict) -> bool:
    """College admins decide everything; faculty only their departments; officers the rest."""
    dept = proposal.get("department")
    if ctx.role == "college_admin":
        return True
    if ctx.role == "faculty":
        return dept is not None and dept in ctx.departments
    return dept is None


def _inbox(settings: Settings, ctx: Context) -> tuple[list[dict], dict]:
    data = _upstream(
        knowledge_get, settings, "/improvements", ctx.tenant_id, {"status": "pending"}
    )
    items = data.get("items", data) if isinstance(data, dict) else data
    mine = [p for p in items if can_decide(ctx, p)]
    mine.sort(key=lambda p: p.get("impact_score", 0), reverse=True)
    peak = data if isinstance(data, dict) else {}
    return mine, {
        "peak_season": bool(peak.get("peak_season")),
        "held": peak.get("held", 0),
    }


def _proposals(settings: Settings, ctx: Context) -> list[dict]:
    return _inbox(settings, ctx)[0]


def _failed_gate(settings: Settings, ctx: Context) -> dict[str, dict]:
    """Changes the evaluation gate rejected, which this person may approve again (PRD 8.4)."""
    data = _upstream(
        knowledge_get,
        settings,
        "/improvements",
        ctx.tenant_id,
        {"status": "failed_gate"},
    )
    items = data.get("items", data) if isinstance(data, dict) else data
    return {str(p["id"]): p for p in items if can_decide(ctx, p)}


@router.get(
    "/approvals", summary="Improvement proposals you may decide, highest impact first"
)
def list_approvals(
    ctx: Context = Depends(APPROVERS), settings: Settings = Depends(settings_dep)
):
    items, peak = _inbox(settings, ctx)
    return {"items": items, **peak}


@router.put(
    "/approvals/peak-season",
    summary="Peak-season mode: only wrong-fact proposals reach experts (College Admin; PRD 8.3)",
)
def set_peak_season(
    body: PeakSeasonIn,
    ctx: Context = Depends(require_staff("college_admin")),
    settings: Settings = Depends(settings_dep),
):
    result = _upstream(
        knowledge_post,
        settings,
        "/learning/settings",
        ctx.tenant_id,
        {"peak_season": body.peak_season, "updated_by": str(ctx.profile_id)},
        "PUT",
    )
    with tx(settings.database_url, ctx) as cur:
        audit(
            cur,
            ctx,
            "improvement.peak_season",
            "tenant",
            ctx.tenant_id,
            after={"peak_season": body.peak_season},
        )
    return result


@router.get(
    "/approvals/settings",
    summary="Peak-season mode and named delegates for overdue proposals (PRD 8.3)",
)
def get_learning_settings(
    ctx: Context = Depends(APPROVERS), settings: Settings = Depends(settings_dep)
):
    return _upstream(knowledge_get, settings, "/learning/settings", ctx.tenant_id, None)


@router.put(
    "/approvals/delegates",
    summary="Name who gets an overdue proposal before the College Admin, per department "
    "(College Admin; PRD 8.3)",
)
def set_delegates(
    body: DelegatesIn,
    ctx: Context = Depends(require_staff("college_admin")),
    settings: Settings = Depends(settings_dep),
):
    clean = {k.strip(): v.strip() for k, v in body.delegates.items()}
    if any(not k or not v or len(k) > 200 or len(v) > 200 for k, v in clean.items()):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Each delegate needs a department (or 'default') and a name",
        )
    result = _upstream(
        knowledge_post,
        settings,
        "/learning/settings",
        ctx.tenant_id,
        {"delegates": clean, "updated_by": str(ctx.profile_id)},
        "PUT",
    )
    with tx(settings.database_url, ctx) as cur:
        audit(
            cur,
            ctx,
            "improvement.delegates",
            "tenant",
            ctx.tenant_id,
            after={"departments": sorted(clean)},
        )
    return result


@router.get(
    "/approvals/second-reviews",
    summary="Approved proposals sampled for a second approver this month (PRD 8.3)",
)
def list_second_reviews(
    ctx: Context = Depends(APPROVERS), settings: Settings = Depends(settings_dep)
):
    data = _upstream(
        knowledge_get,
        settings,
        "/improvements",
        ctx.tenant_id,
        {"second_review": "pending", "include_held": "true"},
    )
    items = data.get("items", data) if isinstance(data, dict) else data
    me = str(ctx.profile_id)
    # A second review is by someone other than the person who approved it.
    return {
        "items": [
            p for p in items if can_decide(ctx, p) and str(p.get("decided_by")) != me
        ]
    }


@router.post(
    "/approvals/{proposal_id}/second-review",
    summary="Agree or disagree with an approved proposal as its second approver (PRD 8.3)",
)
def second_review(
    proposal_id: str,
    body: SecondReviewIn,
    ctx: Context = Depends(APPROVERS),
    settings: Settings = Depends(settings_dep),
):
    waiting = {str(p["id"]) for p in list_second_reviews(ctx, settings)["items"]}
    if proposal_id not in waiting:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Not waiting for your second review (you cannot review your own approval)",
        )
    try:
        result = knowledge_post(
            settings,
            f"/improvements/{proposal_id}/second-review",
            ctx.tenant_id,
            {
                "verdict": body.verdict,
                "reviewer": str(ctx.profile_id),
                "note": body.note,
            },
        )
    except UpstreamError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    with tx(settings.database_url, ctx) as cur:
        audit(
            cur,
            ctx,
            "improvement.second_review",
            "improvement",
            proposal_id,
            after={"verdict": body.verdict},
        )
    return result


@router.post("/approvals/decide", summary="Approve or reject several proposals at once")
def decide(
    body: DecideIn,
    ctx: Context = Depends(APPROVERS),
    settings: Settings = Depends(settings_dep),
):
    allowed = {str(p["id"]) for p in _proposals(settings, ctx)}
    ids = list(dict.fromkeys(body.ids))
    rejected_by_gate = (
        _failed_gate(settings, ctx)
        if body.decision == "approve" and not allowed.issuperset(ids)
        else {}
    )
    results = []
    for pid in ids:
        if pid not in allowed and pid not in rejected_by_gate:
            results.append({"id": pid, "ok": False, "error": "Not in your inbox"})
            continue
        note = body.note
        if pid in rejected_by_gate and not note:
            # Approving again keeps the answer the approver already wrote for a knowledge gap. After
            # a crash the note holds the error instead, which must never become an answer; Module A
            # then asks for the answer again if the change needs one.
            kept = rejected_by_gate[pid].get("note")
            if kept and not kept.startswith("Evaluation could not run"):
                note = kept
        try:
            knowledge_post(
                settings,
                f"/improvements/{pid}/{body.decision}",
                ctx.tenant_id,
                {"decided_by": str(ctx.profile_id), "note": note},
            )
            results.append({"id": pid, "ok": True})
        except UpstreamError as exc:
            results.append({"id": pid, "ok": False, "error": str(exc)})
    with tx(settings.database_url, ctx) as cur:
        for r in results:
            if r["ok"]:
                audit(
                    cur,
                    ctx,
                    f"improvement.{body.decision}",
                    "improvement",
                    r["id"],
                    after={"note": body.note},
                )
    return {"results": results}


@router.get(
    "/approvals/history",
    summary="Recent decisions you could have made: evaluating, deployed, failed the gate, rolled back",
)
def approval_history(
    ctx: Context = Depends(APPROVERS), settings: Settings = Depends(settings_dep)
):
    data = _upstream(knowledge_get, settings, "/improvements", ctx.tenant_id, None)
    items = data.get("items", data) if isinstance(data, dict) else data
    mine = [p for p in items if p.get("status") != "pending" and can_decide(ctx, p)]
    mine.sort(key=lambda p: p.get("created_at", ""), reverse=True)
    out = []
    for p in mine[:30]:
        if p.get("eval_run_id") and p.get("status") in (
            "deployed",
            "failed_gate",
            "rolled_back",
        ):
            detail = _upstream(
                knowledge_get, settings, f"/improvements/{p['id']}", ctx.tenant_id, None
            )
            p = {**p, "eval": detail.get("eval")}
        out.append(p)
    return {"items": out}


@router.post(
    "/approvals/{proposal_id}/rollback",
    summary="Undo a deployed improvement (College Admin; PRD 8.4 one-click rollback)",
)
def rollback(
    proposal_id: str,
    ctx: Context = Depends(require_staff("college_admin")),
    settings: Settings = Depends(settings_dep),
):
    result = _upstream(
        knowledge_post,
        settings,
        f"/improvements/{proposal_id}/rollback",
        ctx.tenant_id,
        {"decided_by": str(ctx.profile_id)},
    )
    with tx(settings.database_url, ctx) as cur:
        audit(cur, ctx, "improvement.rollback", "improvement", proposal_id)
    return result


@router.get(
    "/kb/documents",
    tags=["knowledge"],
    summary="Documents in the knowledge base (from Module A)",
)
def kb_documents(
    ctx: Context = Depends(APPROVERS), settings: Settings = Depends(settings_dep)
):
    return _upstream(knowledge_get, settings, "/kb/documents", ctx.tenant_id, None)


@router.get(
    "/curated-qa", tags=["knowledge"], summary="Curated answers (from Module A)"
)
def curated_qa(
    ctx: Context = Depends(APPROVERS), settings: Settings = Depends(settings_dep)
):
    return _upstream(knowledge_get, settings, "/curated-qa", ctx.tenant_id, None)
