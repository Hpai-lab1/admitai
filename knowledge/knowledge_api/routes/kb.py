"""Knowledge base: search, sources, documents and curated Q&A (PRD F4, 12.1)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field

from .. import crawl, curated, documents, search
from ..config import Settings
from ..experience_client import notify
from ..db import Jsonb, tx
from ..deps import reranker_dep, embedder_dep, settings_dep, tenant_dep
from ..embedder import Embedder

router = APIRouter()


# ------------------------------------------------------------------ search


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    role: Literal["admission", "faculty"] | None = None
    department: str | None = None
    academic_year: str | None = None
    top_k: int = Field(8, ge=1, le=50)
    min_score: float = Field(0.0, ge=0, le=1)


@router.post(
    "/kb/search",
    tags=["search"],
    summary="Hybrid search over approved, in-date knowledge",
)
def kb_search(
    body: SearchIn,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
    embedder: Embedder = Depends(embedder_dep),
    reranker=Depends(reranker_dep),
):
    # Faculty questions are filtered to the department; admission questions see shared content too.
    result = search.search(
        settings.database_url,
        embedder,
        tenant,
        body.query,
        top_k=body.top_k,
        academic_year=body.academic_year,
        department=body.department,
        min_score=body.min_score,
        reranker=reranker,
    )
    return {"query": body.query, **result}


# ------------------------------------------------------------------ sources and documents


class SourceIn(BaseModel):
    kind: Literal["upload", "crawl"]
    name: str = Field(min_length=1, max_length=200)
    start_url: str | None = None
    include: list[str] = []
    exclude: list[str] = []
    max_depth: int = Field(2, ge=0, le=5)
    max_pages: int = Field(50, ge=1, le=500)
    defaults: dict = {}


@router.post(
    "/kb/sources",
    tags=["documents"],
    status_code=201,
    summary="Register an upload or crawl source",
)
def create_source(
    body: SourceIn,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    if body.kind == "crawl" and not (body.start_url or "").startswith(
        ("http://", "https://")
    ):
        raise HTTPException(422, "A crawl source needs start_url (http or https)")
    config = body.model_dump(exclude={"kind", "name"})
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "insert into kb.sources (tenant_id, kind, name, config) values (%s,%s,%s,%s) returning *",
            (tenant, body.kind, body.name, Jsonb(config)),
        )
        row = cur.fetchone()
    return {
        "id": str(row["id"]),
        "kind": row["kind"],
        "name": row["name"],
        "config": row["config"],
    }


@router.post(
    "/kb/sources/{source_id}/sync",
    tags=["documents"],
    summary="Re-crawl a website source now",
)
def sync_source(
    source_id: str,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
    embedder: Embedder = Depends(embedder_dep),
):
    with tx(settings.database_url, tenant) as cur:
        cur.execute("select * from kb.sources where id::text = %s", (source_id,))
        source = cur.fetchone()
    if source is None:
        raise HTTPException(404, "Source not found")
    if source["kind"] != "crawl":
        raise HTTPException(422, "Only crawl sources can be synced")
    result = crawl.crawl(settings, embedder, tenant, source)
    notify(
        settings,
        tenant,
        "kb.sync.completed",
        {"source_id": source_id, "name": source["name"], "result": result},
    )
    return result


@router.post(
    "/kb/documents",
    tags=["documents"],
    status_code=202,
    summary="Upload a document (draft until approved)",
)
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    academic_year: str | None = Form(None),
    department: str | None = Form(None),
    owner_role: str | None = Form(None),
    owner: str | None = Form(None),
    valid_from: date | None = Form(None),
    valid_to: date | None = Form(None),
    replaces: str | None = Form(
        None, description="Id of the document this upload is a new version of"
    ),
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
    embedder: Embedder = Depends(embedder_dep),
):
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, "Files may be at most 50 MB")
    meta = {
        "title": title,
        "academic_year": academic_year,
        "department": department,
        "owner_role": owner_role,
        "owner": owner,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "replaces": replaces,
    }
    filename = file.filename or "upload"
    with tx(settings.database_url, tenant) as cur:
        try:
            row, duplicate = documents.create(cur, tenant, filename, data, meta)
        except documents.DocumentError as exc:
            raise HTTPException(422, str(exc)) from None
    if not duplicate:
        background.add_task(
            documents.process,
            settings,
            embedder,
            tenant,
            str(row["id"]),
            filename,
            data,
        )
    return {**documents.public(row), "duplicate": duplicate}


@router.get(
    "/kb/documents",
    tags=["documents"],
    summary="Documents with status, academic year and owner",
)
def list_documents(
    status_: str | None = Query(None, alias="status"),
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, tenant) as cur:
        documents.expire_due(cur)
        return {"items": [documents.public(r) for r in documents.list_(cur, status_)]}


@router.get("/kb/documents/{doc_id}", tags=["documents"])
def get_document(
    doc_id: str,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, tenant) as cur:
        row = documents.get(cur, doc_id)
        if row is None:
            raise HTTPException(404, "Document not found")
        cur.execute(
            "select ord, heading_path, page, content from kb.chunks where document_id = %s order by ord",
            (row["id"],),
        )
        return {**documents.public(row), "sections": cur.fetchall()}


class DocumentPatch(BaseModel):
    action: Literal["approve", "expire", "reject", "rollback"] | None = None
    actor: str | None = None
    reason: str | None = None
    title: str | None = None
    academic_year: str | None = None
    department: str | None = None
    owner_role: str | None = None
    owner: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None


@router.patch(
    "/kb/documents/{doc_id}",
    tags=["documents"],
    summary="Approve, expire, reject, roll back or edit metadata",
)
def patch_document(
    doc_id: str,
    body: DocumentPatch,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, tenant) as cur:
        if documents.get(cur, doc_id) is None:
            raise HTTPException(404, "Document not found")
        row = documents.update_meta(
            cur,
            doc_id,
            body.model_dump(exclude_unset=True, exclude={"action", "actor", "reason"}),
        )
        if body.action:
            try:
                row = documents.act(cur, doc_id, body.action, body.actor, body.reason)
            except documents.DocumentError as exc:
                raise HTTPException(409, str(exc)) from None
    return documents.public(row)


# ------------------------------------------------------------------ curated Q&A


class CuratedIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    variants: list[str] = []
    answer: str = Field(min_length=1, max_length=5000)
    category: str | None = None
    owner_role: Literal["admission_officer", "faculty", "college_admin"] = (
        "admission_officer"
    )
    department: str | None = None
    academic_year: str
    valid_to: date
    source_correction_id: str | None = None
    actor: str | None = None


class CuratedPatch(BaseModel):
    question: str | None = None
    variants: list[str] | None = None
    answer: str | None = None
    category: str | None = None
    department: str | None = None
    academic_year: str | None = None
    valid_to: date | None = None
    actor: str | None = None


class CuratedAction(BaseModel):
    actor: str | None = None
    reason: str | None = None
    version: int | None = None


@router.get("/curated-qa", tags=["curated"], summary="List curated answers")
def list_curated(
    status_: str | None = Query(None, alias="status"),
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, tenant) as cur:
        curated.expire_due(cur)
        return {"items": [curated.public(r) for r in curated.list_(cur, status_)]}


@router.post(
    "/curated-qa",
    tags=["curated"],
    status_code=201,
    summary="Create a draft curated answer",
)
def create_curated(
    body: CuratedIn,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
    embedder: Embedder = Depends(embedder_dep),
):
    with tx(settings.database_url, tenant) as cur:
        try:
            row = curated.create(
                cur, embedder, tenant, body.model_dump(exclude={"actor"}), body.actor
            )
        except curated.CuratedError as exc:
            raise HTTPException(422, str(exc)) from None
    return curated.public(row)


@router.get("/curated-qa/{qa_id}", tags=["curated"])
def get_curated(
    qa_id: str,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, tenant) as cur:
        row = curated.get(cur, qa_id)
        if row is None:
            raise HTTPException(404, "Curated answer not found")
        return {**curated.public(row), "history": curated.history(cur, qa_id)}


@router.patch(
    "/curated-qa/{qa_id}",
    tags=["curated"],
    summary="Edit (creates a new draft version)",
)
def patch_curated(
    qa_id: str,
    body: CuratedPatch,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
    embedder: Embedder = Depends(embedder_dep),
):
    with tx(settings.database_url, tenant) as cur:
        try:
            row = curated.update(
                cur, embedder, qa_id, body.model_dump(exclude={"actor"}), body.actor
            )
        except KeyError:
            raise HTTPException(404, "Curated answer not found") from None
        except curated.CuratedError as exc:
            raise HTTPException(422, str(exc)) from None
    return curated.public(row)


@router.post(
    "/curated-qa/{qa_id}/{action}",
    tags=["curated"],
    summary="approve | suspend | expire | rollback",
)
def act_curated(
    qa_id: str,
    action: Literal["approve", "suspend", "expire", "rollback"],
    body: CuratedAction,
    tenant: str = Depends(tenant_dep),
    settings: Settings = Depends(settings_dep),
    embedder: Embedder = Depends(embedder_dep),
):
    with tx(settings.database_url, tenant) as cur:
        try:
            if action == "rollback":
                if body.version is None:
                    raise HTTPException(422, "version is required for rollback")
                row = curated.rollback(cur, embedder, qa_id, body.version, body.actor)
            else:
                status_map = {
                    "approve": "approved",
                    "suspend": "suspended",
                    "expire": "expired",
                }
                row = curated.set_status(
                    cur, qa_id, status_map[action], body.actor, body.reason
                )
        except KeyError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Curated answer or version not found"
            ) from None
        except curated.CuratedError as exc:
            raise HTTPException(409, str(exc)) from None
    return curated.public(row)
