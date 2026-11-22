"""Contract-compatible mock of Module A (Knowledge & Learning) for developing Module B on its own.

It answers the agreed endpoints with the same shapes as Module A, from a fixed, synthetic fixture
(tests/fixtures/demo_college.json, generated from packs/demo-college). It is not Module A: there is
no ingestion, no embeddings and no database, and search is plain keyword overlap.

    uvicorn mocks.knowledge:app --host 127.0.0.1 --port 8151      (from assistant/)
    KNOWLEDGE_API_URL=http://127.0.0.1:8151                      (for Module B)

Environment:
  MOCK_KB_TENANTS   comma-separated tenant ids that get the fixture ("*" = any); others get
                    nothing, as Module A's row-level security would give another college nothing
  MOCK_KB_KEY       when set, X-Service-Key must equal it (Module A checks KEY_B_TO_A)
  MOCK_KB_FIXTURE   another fixture file of the same shape
  MOCK_KB_NO_LIST=1 behave like a Module A without GET /catalogue/programs (agreed contract only)
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

FIXTURE = Path(
    os.environ.get(
        "MOCK_KB_FIXTURE",
        Path(__file__).resolve().parent.parent
        / "tests"
        / "fixtures"
        / "demo_college.json",
    )
)
_STOP = set(
    "the and for are with this that from you your can will have has was were our its not but all "
    "any what when where how which who does did is do a an of to in on at by be it me my i".split()
)

app = FastAPI(title="Mock Module A (contract stand-in for Module B)")


def _data() -> dict:
    return json.loads(FIXTURE.read_text())


def _tenant(x_tenant_id: str | None, x_service_key: str | None) -> bool:
    """True if this tenant gets the fixture. Errors as Module A gives them."""
    key = os.environ.get("MOCK_KB_KEY")
    if key and x_service_key != key:
        raise HTTPException(401, "Unknown service")
    try:
        tenant = str(UUID(x_tenant_id or ""))
    except ValueError:
        raise HTTPException(400, "X-Tenant-Id must be a uuid") from None
    allowed = os.environ.get("MOCK_KB_TENANTS", "*")
    return allowed == "*" or tenant in {t.strip() for t in allowed.split(",")}


def _words(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[^\W\d_]{2,}", text.lower()):
        if w not in _STOP:
            out.add(w[:-1] if len(w) > 3 and w.endswith("s") else w)
    return out


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    role: str | None = None
    department: str | None = None
    academic_year: str | None = None
    top_k: int = Field(8, ge=1, le=50)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "module": "knowledge-mock"}


@app.post("/kb/search")
def kb_search(
    body: SearchIn,
    x_tenant_id: str | None = Header(default=None),
    x_service_key: str | None = Header(default=None),
) -> dict:
    if not _tenant(x_tenant_id, x_service_key):
        return {
            "query": body.query,
            "items": [],
            "max_vector_score": 0.0,
            "answerable": False,
            "embedder": "primary",
        }
    q = _words(body.query)
    scored = []
    for p in _data()["passages"]:
        c = p["citation"]
        if (
            body.department
            and c.get("department")
            and c["department"] != body.department
        ):
            continue
        if (
            body.academic_year
            and c.get("academic_year")
            and c["academic_year"] != body.academic_year
        ):
            continue
        words = _words(
            " ".join(
                [
                    p["content"],
                    p.get("keywords", ""),
                    c.get("title") or "",
                    c.get("heading_path") or "",
                ]
            )
        )
        overlap = len(q & words) / len(q) if q else 0.0
        if overlap > 0:
            scored.append((overlap, p))
    scored.sort(key=lambda x: -x[0])
    items = [
        {
            "id": p["id"],
            "kind": p["kind"],
            "content": p["content"],
            "score": round(overlap / (i + 1), 6),
            "vector_score": round(0.5 + 0.45 * overlap, 4),
            "keyword_hit": True,
            "citation": p["citation"],
        }
        for i, (overlap, p) in enumerate(scored[: body.top_k])
    ]
    best = items[0]["vector_score"] if items else 0.0
    return {
        "query": body.query,
        "items": items,
        "max_vector_score": best,
        "answerable": bool(items),
        "embedder": "primary",
    }


def _without_facts(p: dict) -> dict:
    keep = ("id", "code", "name", "aliases", "department", "level", "academic_year")
    return {k: p[k] for k in keep if k in p}


@app.get("/catalogue/programs")
def programs(
    facts: bool = Query(False),
    x_tenant_id: str | None = Header(default=None),
    x_service_key: str | None = Header(default=None),
) -> dict:
    if os.environ.get("MOCK_KB_NO_LIST") == "1":
        raise HTTPException(404, "Not Found")
    if not _tenant(x_tenant_id, x_service_key):
        return {"items": [], "general_dates": []}
    cat = _data()["catalogue"]
    items = cat["items"] if facts else [_without_facts(p) for p in cat["items"]]
    return {"items": items, "general_dates": cat["general_dates"]}


@app.get("/catalogue/programs/{program_id}/facts")
def program_facts(
    program_id: str,
    x_tenant_id: str | None = Header(default=None),
    x_service_key: str | None = Header(default=None),
) -> dict:
    if _tenant(x_tenant_id, x_service_key):
        ref = program_id.lower()
        for p in _data()["catalogue"]["items"]:
            names = [p["id"], p["code"], *p.get("aliases", [])]
            if ref in (n.lower() for n in names):
                return p
    raise HTTPException(404, "Program not found")
