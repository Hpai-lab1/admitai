"""Improvement proposals, evaluation runs and reports (PRD 8, 12.1, 17, 20)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .. import (
    catalogue_cases,
    comparison,
    gate,
    health,
    judge,
    learning,
    quality,
    tenant_pack,
    voice_eval,
)
from ..config import Settings
from ..db import Jsonb, tx
from ..deps import embedder_dep, settings_dep, tenant_dep
from ..embedder import Embedder

router = APIRouter()


def assistant_dep(request: Request):
    return request.app.state.assistant


# ------------------------------------------------------------------ proposals


@router.get(
    "/improvements", tags=["improvements"], summary="Proposals ranked by impact score"
)
def list_improvements(
    status_: str | None = Query(None, alias="status"),
    include_held: bool = False,
    second_review: Literal["pending", "agreed", "disagreed"] | None = None,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, tenant) as cur:
        peak = learning.peak_season(cur)
        cur.execute(
            "select * from learn.proposals where (%s::text is null or status = %s)"
            " and (%s::text is null or second_review = %s)"
            " order by impact_score desc, created_at",
            (status_, status_, second_review, second_review),
        )
        rows = cur.fetchall()
    # Peak season (8.3): lower-severity proposals wait until after the peak.
    held = [p for p in rows if learning.held_in_peak(p, peak)]
    shown = rows if include_held else [p for p in rows if p not in held]
    return {
        "items": [learning.public(p) for p in shown],
        "peak_season": peak,
        "held": len(held),
    }


class LearningSettingsIn(BaseModel):
    peak_season: bool | None = None
    # 8.3: who gets an overdue proposal before the College Admin, per department
    # ({"default": "deputy.registrar@college.example", "Computer Science": "hod.cse@college.example"}).
    delegates: dict[str, str] | None = None
    updated_by: str | None = Field(default=None, max_length=200)


@router.get(
    "/learning/settings",
    tags=["improvements"],
    summary="Peak-season mode for this college (PRD 8.3)",
)
def get_learning_settings(
    tenant: str = Depends(tenant_dep), settings: Settings = Depends(settings_dep)
):
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select peak_season, delegates, updated_by, updated_at from learn.settings"
        )
        return cur.fetchone() or {
            "peak_season": False,
            "delegates": {},
            "updated_by": None,
            "updated_at": None,
        }


@router.put(
    "/learning/settings",
    tags=["improvements"],
    summary="Peak-season mode (only wrong-fact proposals reach experts) and named delegates for "
    "overdue proposals (PRD 8.3). Fields left out keep their value.",
)
def put_learning_settings(
    body: LearningSettingsIn,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    if body.delegates is not None:
        bad = [
            k
            for k, v in body.delegates.items()
            if not k.strip() or not v.strip() or len(k) > 200 or len(v) > 200
        ]
        if bad:
            raise HTTPException(
                422, "Each delegate needs a department (or 'default') and a name"
            )
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "insert into learn.settings (tenant_id, peak_season, delegates, updated_by)"
            " values (%s, coalesce(%s, false), coalesce(%s, '{}'::jsonb), %s)"
            " on conflict (tenant_id) do update set"
            " peak_season = coalesce(%s, learn.settings.peak_season),"
            " delegates = coalesce(%s, learn.settings.delegates),"
            " updated_by = excluded.updated_by, updated_at = now()"
            " returning peak_season, delegates, updated_by, updated_at",
            (
                tenant,
                body.peak_season,
                Jsonb(body.delegates) if body.delegates is not None else None,
                body.updated_by,
                body.peak_season,
                Jsonb(body.delegates) if body.delegates is not None else None,
            ),
        )
        return cur.fetchone()


@router.get("/improvements/{proposal_id}", tags=["improvements"])
def get_improvement(
    proposal_id: str,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, tenant) as cur:
        cur.execute("select * from learn.proposals where id::text = %s", (proposal_id,))
        p = cur.fetchone()
        if p is None:
            raise HTTPException(404, "Proposal not found")
        run = None
        if p["eval_run_id"]:
            cur.execute(
                "select id, label, scores, passed, gate, finished_at from learn.eval_runs where id = %s",
                (p["eval_run_id"],),
            )
            run = cur.fetchone()
    return {**learning.public(p), "eval": run, "deployed_ref": p["deployed_ref"]}


class DecideIn(BaseModel):
    decided_by: str | None = None
    note: str | None = Field(default=None, max_length=4000)


@router.post(
    "/improvements/analyse",
    tags=["improvements"],
    summary="Run the Feedback Analyst now",
)
def analyse(
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
    embedder: Embedder = Depends(embedder_dep),
    assistant=Depends(assistant_dep),
):
    return learning.run(settings, embedder, assistant, tenant)


class SecondReviewIn(BaseModel):
    verdict: Literal["agree", "disagree"]
    reviewer: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=4000)


@router.post(
    "/improvements/second-reviews/sample",
    tags=["improvements"],
    summary="Pick 10% of last month's approved proposals for a second approver (PRD 8.3); "
    "once a month, safe to repeat",
)
def sample_second_reviews(
    tenant: str = Depends(tenant_dep), settings: Settings = Depends(settings_dep)
):
    return quality.sample_second_reviews(settings, tenant)


@router.post(
    "/improvements/{proposal_id}/second-review",
    tags=["improvements"],
    summary="Record the second approver's verdict on a sampled proposal (PRD 8.3)",
)
def second_review(
    proposal_id: str,
    body: SecondReviewIn,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    try:
        return quality.record_second_review(
            settings,
            tenant,
            proposal_id,
            body.verdict == "agree",
            body.reviewer,
            body.note,
        )
    except quality.SecondReviewError as exc:
        raise HTTPException(exc.status, str(exc)) from None


@router.post(
    "/improvements/{proposal_id}/{decision}",
    tags=["improvements"],
    summary="approve (then evaluation gate and deploy) | reject | rollback",
)
def decide(
    proposal_id: str,
    decision: Literal["approve", "reject", "rollback"],
    body: DecideIn,
    background: BackgroundTasks,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
    embedder: Embedder = Depends(embedder_dep),
    assistant=Depends(assistant_dep),
):
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select * from learn.proposals where id::text = %s for update",
            (proposal_id,),
        )
        p = cur.fetchone()
        if p is None:
            raise HTTPException(404, "Proposal not found")
        if decision == "rollback":
            if p["status"] != "deployed" or not p["deployed_ref"]:
                raise HTTPException(409, "Only a deployed change can be rolled back")
            gate.rollback_change(
                settings,
                assistant,
                tenant,
                p["deployed_ref"],
                body.decided_by,
                embedder,
            )
            # The cases this change added test something that no longer exists.
            cur.execute(
                "update learn.eval_cases set active = false where proposal_id = %s",
                (p["id"],),
            )
            status = "rolled_back"
        elif p["status"] not in ("pending", "failed_gate") or (
            p["status"] == "failed_gate" and decision != "approve"
        ):
            # A proposal that failed the gate (or whose evaluation was interrupted) may be approved
            # again, e.g. after the knowledge it conflicted with was fixed.
            raise HTTPException(409, f"This proposal is already {p['status']}")
        elif decision == "reject":
            status = "rejected"
        elif (
            p["kind"] in gate.MANUAL_KINDS
            or p["diff"].get("source_type") == "catalogue"
        ):
            status = "approved_manual"  # prompt changes are made by Module B's owner, then evaluated
        else:
            if (
                p["kind"] == "curated_answer"
                and not p["diff"].get("answer")
                and len((body.note or "").strip()) < 15
            ):
                raise HTTPException(
                    422, "Write the answer in the note when approving a knowledge gap"
                )
            status = "evaluating"
        cur.execute(
            "update learn.proposals set status=%s, decided_by=coalesce(%s, decided_by), decided_at=now(),"
            " note=coalesce(%s, note), updated_at=now() where id=%s",
            (status, body.decided_by, body.note, p["id"]),
        )
        cur.execute("select * from learn.proposals where id = %s", (p["id"],))
        out = learning.public(cur.fetchone())
    if status == "evaluating":
        background.add_task(
            gate.gate_and_deploy,
            settings,
            embedder,
            assistant,
            tenant,
            proposal_id,
            body.note,
            settings.eval_max_cases,
        )
    return out


# ------------------------------------------------------------------ evaluation runs


class RunIn(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=1000)
    label: Literal["adhoc", "launch"] = "adhoc"


@router.post(
    "/evals/runs",
    tags=["evals"],
    status_code=202,
    summary="Run the tenant's test set now",
)
def start_run(
    body: RunIn,
    background: BackgroundTasks,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
    assistant=Depends(assistant_dep),
):
    run_id = gate.new_run(settings, tenant, tenant, body.label, "manual")
    background.add_task(
        gate.run_suite,
        settings,
        assistant,
        tenant,
        tenant,
        body.label,
        "manual",
        None,
        body.limit,
        run_id,
    )
    return {"status": "started", "id": str(run_id)}


@router.post(
    "/evals/cases/generate",
    tags=["evals"],
    summary="Regenerate the test cases that come from the program catalogue (PRD F10.3)",
)
def generate_catalogue_cases(
    tenant: str = Depends(tenant_dep), settings: Settings = Depends(settings_dep)
):
    return catalogue_cases.generate(settings, tenant)


@router.get("/evals/cases", tags=["evals"], summary="The tenant's active test cases")
def list_cases(
    source: Literal["pack", "proposal", "staff", "catalogue"] | None = None,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select ext_id, question, language, kind, expected_refs, forbidden_refs, expected_facts,"
            " expected_role, critical, source from learn.eval_cases where active"
            " and (%s::text is null or source = %s) order by ext_id",
            (source, source),
        )
        return {"items": cur.fetchall()}


class VoiceCheckIn(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=1000)


@router.post(
    "/evals/voice",
    tags=["evals"],
    status_code=202,
    summary="Answer the test set as chat and as voice, and send both scores to Module B (PRD F2.10)",
)
def start_voice_check(
    body: VoiceCheckIn,
    background: BackgroundTasks,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
    assistant=Depends(assistant_dep),
):
    import uuid

    check_id = uuid.uuid4().hex[:12]
    background.add_task(
        voice_eval.run_voice_check, settings, assistant, tenant, body.limit, check_id
    )
    return {"status": "started", "check_id": check_id}


@router.get("/evals/voice/{check_id}", tags=["evals"])
def get_voice_check(
    check_id: str,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select id, label, status, gate from learn.eval_runs where trigger = %s",
            (f"voice:{check_id}",),
        )
        runs = cur.fetchall()
    if not runs:
        raise HTTPException(404, "Voice check not found")
    result = next(
        ((r["gate"] or {}).get("voice_check") for r in runs if r["gate"]), None
    )
    if result is None:
        status = "error" if any(r["status"] == "error" for r in runs) else "running"
        return {"status": status}
    return {"status": "done", **result}


@router.get("/evals/runs", tags=["evals"])
def list_runs(
    tenant: str = Depends(tenant_dep), settings: Settings = Depends(settings_dep)
):
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select id, trigger, label, status, scores, passed, gate, started_at, finished_at"
            " from learn.eval_runs order by started_at desc limit 50"
        )
        return {"items": cur.fetchall()}


@router.get("/evals/runs/{run_id}", tags=["evals"])
def get_run(
    run_id: str,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, tenant) as cur:
        cur.execute("select * from learn.eval_runs where id::text = %s", (run_id,))
        run = cur.fetchone()
    if run is None:
        raise HTTPException(404, "Run not found")
    return run


class ComparisonIn(BaseModel):
    models: list[str] = Field(min_length=2, max_length=comparison.MAX_MODELS)
    limit: int | None = Field(default=None, ge=1, le=1000)
    requested_by: str | None = Field(default=None, max_length=200)


@router.post(
    "/evals/comparisons",
    tags=["evals"],
    status_code=202,
    summary="Answer the test set with several models on sandbox copies and compare them (PRD 20)",
)
def start_comparison(
    body: ComparisonIn,
    background: BackgroundTasks,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
    assistant=Depends(assistant_dep),
):
    models = [m.strip() for m in body.models if m.strip()]
    if (
        len(set(models)) != len(models)
        or len(models) < 2
        or any(len(m) > 100 for m in models)
    ):
        raise HTTPException(422, "Name two to five different models")
    cid = comparison.start(settings, tenant, models, body.requested_by)
    background.add_task(
        comparison.run_comparison, settings, assistant, tenant, cid, models, body.limit
    )
    return {"status": "started", "id": cid}


@router.get("/evals/comparisons", tags=["evals"])
def list_comparisons(
    tenant: str = Depends(tenant_dep), settings: Settings = Depends(settings_dep)
):
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select id, models, status, requested_by, started_at, finished_at from learn.comparisons"
            " order by started_at desc limit 50"
        )
        return {"items": cur.fetchall()}


@router.get("/evals/comparisons/{comparison_id}", tags=["evals"])
def get_comparison(
    comparison_id: str,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select * from learn.comparisons where id::text = %s", (comparison_id,)
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "Comparison not found")
    return row


class JudgeIn(BaseModel):
    force: bool = False  # development only: grade outside the scheduled window


@router.post(
    "/evals/runs/{run_id}/judge",
    tags=["evals"],
    status_code=202,
    summary="Grade a finished run with the judge models (rubric, cross-checked, 10% human spot "
    "check; PRD 20). Only in the scheduled window.",
)
def judge_run(
    run_id: str,
    body: JudgeIn,
    background: BackgroundTasks,
    request: Request,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    open_now = judge.in_window(settings.judge_window, settings.judge_timezone)
    if not open_now and not (body.force and settings.app_env == "development"):
        raise HTTPException(
            409,
            f"The judge models run only in the scheduled window ({settings.judge_window},"
            f" {settings.judge_timezone})",
        )
    with tx(settings.database_url, tenant) as cur:
        cur.execute("select status from learn.eval_runs where id::text = %s", (run_id,))
        run = cur.fetchone()
    if run is None:
        raise HTTPException(404, "Run not found")
    if run["status"] != "done":
        raise HTTPException(409, "Only a finished run can be graded")
    primary, check = getattr(request.app.state, "judges", None) or (None, None)
    background.add_task(judge.judge_run, settings, tenant, run_id, primary, check)
    return {
        "status": "started",
        "run_id": run_id,
        "models": [settings.judge_model, settings.judge_check_model],
    }


class HumanGradeIn(BaseModel):
    case_id: str = Field(min_length=1, max_length=200)
    verdict: Literal["correct", "partly", "incorrect"]
    grader: str = Field(min_length=1, max_length=200)


@router.post(
    "/evals/runs/{run_id}/human-grade",
    tags=["evals"],
    summary="A person's verdict on a spot-checked case; it overrides the judges (PRD 20)",
)
def human_grade(
    run_id: str,
    body: HumanGradeIn,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    try:
        return judge.human_grade(
            settings, tenant, run_id, body.case_id, body.verdict, body.grader
        )
    except judge.JudgeError as exc:
        raise HTTPException(404 if "not found" in str(exc) else 409, str(exc)) from None


# ------------------------------------------------------------------ reports and content operations


@router.get(
    "/reports/weekly",
    tags=["reports"],
    summary="Weekly gap, improvement and KB health report",
)
def weekly(
    tenant: str = Depends(tenant_dep), settings: Settings = Depends(settings_dep)
):
    return health.weekly_report(settings, tenant)


@router.get(
    "/reports/digest",
    tags=["reports"],
    summary="The weekly approval digest (top 10 by impact) for Module C to e-mail (PRD 8.3, F7.7)",
)
def weekly_digest(
    top: int = Query(10, ge=1, le=50),
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    return quality.digest(settings, tenant, top)


@router.get(
    "/reports/quality",
    tags=["reports"],
    summary="Quality dashboard data: acceptance rate, eval score per run, proposals opened/"
    "approved/deployed, per week (PRD F9.3, 8.6)",
)
def quality_report(
    weeks: int = Query(12, ge=1, le=104),
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    return quality.quality_report(settings, tenant, weeks)


class CutoverIn(BaseModel):
    old_year: str
    new_year: str
    new_valid_to: date


@router.post("/cutover", tags=["reports"], summary="Academic-year cut-over (PRD 17.3)")
def cutover(
    body: CutoverIn,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    return health.cutover(
        settings, tenant, body.old_year, body.new_year, body.new_valid_to
    )


@router.get(
    "/export/degraded",
    tags=["reports"],
    summary="Top approved answers for degraded mode",
)
def degraded(
    tenant: str = Depends(tenant_dep), settings: Settings = Depends(settings_dep)
):
    return health.degraded_export(settings, tenant)


# ------------------------------------------------------------------ Tenant Pack (F10.1-F10.3, F10.6)


@router.post(
    "/tenant-pack",
    tags=["tenant pack"],
    summary="Import and validate a Tenant Pack (zip)",
)
async def import_tenant_pack(
    file: UploadFile = File(...),
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
    embedder: Embedder = Depends(embedder_dep),
):
    data = await file.read(tenant_pack.MAX_PACK_BYTES + 1)
    try:
        return await run_in_threadpool(
            tenant_pack.import_pack, settings, embedder, tenant, data
        )
    except tenant_pack.PackError as exc:
        raise HTTPException(422, {"problems": exc.problems}) from None


@router.get(
    "/tenant-pack/readiness",
    tags=["tenant pack"],
    summary="Knowledge readiness checklist",
)
def pack_readiness(
    min_test_questions: int | None = None,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    launch = {"min_test_questions": min_test_questions} if min_test_questions else {}
    return {"items": tenant_pack.readiness(settings, tenant, launch)}


@router.get(
    "/tenant-pack/launch/{run_id}",
    tags=["tenant pack"],
    summary="Has the launch test run passed? (F10.3)",
)
def launch_result(
    run_id: str,
    min_accuracy: float = 0.85,
    min_refusal: float = 0.9,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select status, scores, results from learn.eval_runs where id::text = %s",
            (run_id,),
        )
        run = cur.fetchone()
    if run is None:
        raise HTTPException(404, "Run not found")
    if run["status"] != "done":
        return {"status": run["status"]}
    verdict = tenant_pack.launch_verdict(
        run["scores"],
        run["results"],
        {"min_accuracy": min_accuracy, "min_refusal": min_refusal},
    )
    return {"status": "done", "scores": run["scores"], **verdict}
