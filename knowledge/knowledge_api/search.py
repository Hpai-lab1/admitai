"""Hybrid retrieval (PRD 9.4, F4.6): vector + full-text over approved, in-date content, merged by
reciprocal-rank fusion, filtered by academic year and department.

Searches both document chunks and curated Q&A; curated answers are expert wording, so callers can
prefer them, but every number still has to match the catalogue (Module B's sentence checks).
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import date
from uuid import UUID

import numpy as np

from .db import tx
from .embedder import Embedder, uses_local_script

RRF_K = 60
CANDIDATES = 20


@dataclass
class _Index:
    stamp: tuple
    ids: list[str]
    meta: list[dict]
    matrix: np.ndarray  # rows are unit vectors
    matrix_ml: (
        np.ndarray | None
    )  # multilingual vectors; zero rows where not yet embedded


_cache: dict[str, _Index] = {}
_lock = threading.Lock()

_STAMP_SQL = """
select (select count(*) from kb.chunks) as chunks,
       (select max(updated_at) from kb.documents) as docs,
       (select count(*) from kb.curated_qa) as qa,
       (select max(updated_at) from kb.curated_qa) as qa_changed,
       (select count(embedding_ml) from kb.chunks) + (select count(embedding_ml) from kb.curated_qa) as ml
"""

_ROWS_SQL = """
select c.id::text as id, 'chunk' as kind, c.content, c.heading_path, c.page, {vec} as embedding,
       {vec_ml} as embedding_ml,
       d.id::text as document_id, d.title, d.academic_year, d.department, d.valid_from, d.valid_to,
       d.status, d.url, d.owner_role
from kb.chunks c join kb.documents d on d.id = c.document_id
union all
select q.id::text, 'curated_qa', 'Q: ' || q.question || E'\nA: ' || q.answer, coalesce(q.category, ''),
       null, {qvec}, {qvec_ml}, null, q.question, q.academic_year, q.department, null, q.valid_to,
       q.status, null, q.owner_role
from kb.curated_qa q
"""

# PRD 9.4: on the lab's PostgreSQL the vector search runs in the database (pgvector HNSW, set up
# by `python -m knowledge_api.migrate`). Elsewhere the vectors are searched here in memory.
# KB_VECTOR_BACKEND: auto (default: pgvector when its columns exist), memory, or pgvector.
_backend: dict[str, bool] = {}

_PG_VECTOR_SQL = """
(select c.id::text as id, 1 - (c.{col} <=> %(v)s::vector) as sim
 from kb.chunks c join kb.documents d on d.id = c.document_id
 where c.{col} is not null and d.status = 'approved'
   and (d.valid_from is null or d.valid_from <= %(today)s)
   and (d.valid_to is null or d.valid_to >= %(today)s)
   and (%(year)s::text is null or d.academic_year is null or d.academic_year = %(year)s)
   and (%(dept)s::text is null or d.department is null or d.department = %(dept)s)
 order by c.{col} <=> %(v)s::vector limit %(k)s)
union all
(select q.id::text, 1 - (q.{col} <=> %(v)s::vector)
 from kb.curated_qa q
 where q.{col} is not null and q.status = 'approved'
   and (q.valid_to is null or q.valid_to >= %(today)s)
   and (%(year)s::text is null or q.academic_year is null or q.academic_year = %(year)s)
   and (%(dept)s::text is null or q.department is null or q.department = %(dept)s)
 order by q.{col} <=> %(v)s::vector limit %(k)s)
"""


def uses_pgvector(database_url: str) -> bool:
    choice = os.environ.get("KB_VECTOR_BACKEND", "auto")
    if choice == "memory":
        return False
    if database_url not in _backend:
        with tx(database_url, None) as cur:
            cur.execute(
                "select count(*) as n from information_schema.columns where table_schema = 'kb'"
                " and column_name = 'embedding_v'"
            )
            _backend[database_url] = cur.fetchone()["n"] >= 2
    if choice == "pgvector" and not _backend[database_url]:
        raise RuntimeError(
            "KB_VECTOR_BACKEND=pgvector but the pgvector columns are missing: run the migrations"
        )
    return _backend[database_url]


def _pg_vector_ranks(
    database_url: str,
    tenant: str,
    qvec: np.ndarray,
    ml: bool,
    today: date,
    academic_year: str | None,
    department: str | None,
    extra_ids: list[str],
) -> tuple[list[str], dict[str, float]]:
    """Best CANDIDATES by cosine similarity, from the HNSW index, plus the similarity of
    `extra_ids` (keyword hits outside the top), so both rankings can be fused as before."""
    col = "embedding_ml_v" if ml else "embedding_v"
    v = "[" + ",".join(f"{x:.7f}" for x in qvec.tolist()) + "]"
    with tx(database_url, tenant) as cur:
        # Filtered HNSW scans can run short of rows: keep scanning until enough pass the filters.
        cur.execute("select set_config('hnsw.ef_search', '100', true)")
        cur.execute("select set_config('hnsw.iterative_scan', 'relaxed_order', true)")
        cur.execute(
            _PG_VECTOR_SQL.format(col=col) + " order by sim desc limit %(k)s",
            {
                "v": v,
                "today": today,
                "year": academic_year,
                "dept": department,
                "k": CANDIDATES,
            },
        )
        top = cur.fetchall()
        scores = {r["id"]: float(r["sim"]) for r in top}
        missing = [i for i in extra_ids if i not in scores]
        if missing:
            cur.execute(
                f"select id::text, 1 - ({col} <=> %(v)s::vector) as sim from kb.chunks"
                f" where id = any(%(ids)s::uuid[]) and {col} is not null"
                f" union all select id::text, 1 - ({col} <=> %(v)s::vector) from kb.curated_qa"
                f" where id = any(%(ids)s::uuid[]) and {col} is not null",
                {"v": v, "ids": missing},
            )
            scores.update({r["id"]: float(r["sim"]) for r in cur.fetchall()})
    return [r["id"] for r in top], scores


def invalidate(tenant_id: UUID | str | None = None) -> None:
    with _lock:
        if tenant_id is None:
            _cache.clear()
            _backend.clear()
        else:
            _cache.pop(str(tenant_id), None)


def _index(database_url: str, tenant_id: str) -> _Index:
    with tx(database_url, tenant_id) as cur:
        cur.execute(_STAMP_SQL)
        s = cur.fetchone()
        stamp = (s["chunks"], s["docs"], s["qa"], s["qa_changed"], s["ml"])
        with _lock:
            idx = _cache.get(tenant_id)
            if idx is not None and idx.stamp == stamp:
                return idx
        in_db = uses_pgvector(database_url)
        cur.execute(
            _ROWS_SQL.format(
                vec="null::real[]" if in_db else "c.embedding",
                vec_ml="null::real[]" if in_db else "c.embedding_ml",
                qvec="null::real[]" if in_db else "q.embedding",
                qvec_ml="null::real[]" if in_db else "q.embedding_ml",
            )
        )
        rows = cur.fetchall()
    if in_db:  # only metadata is cached; the vectors stay in the database
        for r in rows:
            r.pop("embedding")
            r.pop("embedding_ml")
        matrix = np.zeros((len(rows), 1), dtype=np.float32)
        matrix_ml = matrix if s["ml"] else None
    else:
        matrix = _unit_matrix([r.pop("embedding") for r in rows])
        ml_vectors = [r.pop("embedding_ml") for r in rows]
        matrix_ml = (
            _unit_matrix(ml_vectors) if any(v is not None for v in ml_vectors) else None
        )
    idx = _Index(stamp, [r["id"] for r in rows], rows, matrix, matrix_ml)
    with _lock:
        _cache[tenant_id] = idx
    return idx


def _unit_matrix(vectors: list[list[float] | None]) -> np.ndarray:
    dim = next((len(v) for v in vectors if v is not None), 1)
    m = np.asarray(
        [v if v is not None else [0.0] * dim for v in vectors], dtype=np.float32
    ).reshape(len(vectors), dim)
    if len(m):
        m /= np.linalg.norm(m, axis=1, keepdims=True).clip(min=1e-9)
    return m


def servable(
    m: dict, today: date, academic_year: str | None, department: str | None
) -> bool:
    """F4.6: only approved, in-date content; optional academic-year and department filters
    (content without a department is shared by all departments)."""
    if m["status"] != "approved":
        return False
    if m["valid_from"] and m["valid_from"] > today:
        return False
    if m["valid_to"] and m["valid_to"] < today:
        return False
    if academic_year and m["academic_year"] and m["academic_year"] != academic_year:
        return False
    if department and m["department"] and m["department"] != department:
        return False
    return True


def _keyword_ranks(
    database_url: str, tenant_id: str, query: str, allowed: set[str]
) -> list[str]:
    """Full-text candidates; OR of the query's lexemes so one missing word does not drop a match."""
    sql = """
    with q as (
        select to_tsquery('english', coalesce(nullif(array_to_string(
                   tsvector_to_array(to_tsvector('english', %(q)s)), ' | '), ''), 'zzzz')) as tq
    )
    select id::text, ts_rank_cd(tsv, q.tq) as rank from kb.chunks, q where tsv @@ q.tq
    union all
    select id::text, ts_rank_cd(tsv, q.tq) from kb.curated_qa, q where tsv @@ q.tq
    order by rank desc limit 200
    """
    with tx(database_url, tenant_id) as cur:
        cur.execute(sql, {"q": query})
        return [r["id"] for r in cur.fetchall() if r["id"] in allowed][:CANDIDATES]


RERANK_CANDIDATES = 8  # PRD 7.3: top_k 8, reranked


def _with_context(item: dict) -> str:
    c = item["citation"]
    where = " > ".join(x for x in (c["title"], c.get("heading_path")) if x)
    if where and item["kind"] == "chunk":
        return f"{where}\n{item['content']}"
    return item["content"]


def search(
    database_url: str,
    embedder: Embedder,
    tenant_id: UUID | str,
    query: str,
    *,
    top_k: int = 8,
    academic_year: str | None = None,
    department: str | None = None,
    min_score: float = 0.0,
    today: date | None = None,
    mode: str = "hybrid",
    reranker=None,
) -> dict:
    """mode: hybrid (default), dense (vector only) or keyword (full-text only), for comparisons.
    With a `reranker`, a hybrid search's best candidates are reordered by the cross-encoder
    (PRD 9.4), except for Indic-script questions, which it does not understand."""
    tenant = str(tenant_id)
    today = today or date.today()
    idx = _index(database_url, tenant)
    allowed_pos = [
        i
        for i, m in enumerate(idx.meta)
        if servable(m, today, academic_year, department)
    ]
    if not allowed_pos:
        return {
            "items": [],
            "max_vector_score": 0.0,
            "answerable": False,
            "embedder": "none",
        }

    # Indic-script questions use the multilingual index when it exists (PRD F1.6).
    use_ml = (
        embedder.ml is not None
        and idx.matrix_ml is not None
        and uses_local_script(query)
    )
    model = embedder.ml if use_ml else embedder
    matrix = idx.matrix_ml if use_ml else idx.matrix
    qvec = np.asarray(model.query(query), dtype=np.float32)
    qvec /= max(float(np.linalg.norm(qvec)), 1e-9)
    allowed_ids = {idx.ids[i] for i in allowed_pos}
    keyword_rank = (
        _keyword_ranks(database_url, tenant, query, allowed_ids)
        if mode != "dense"
        else []
    )
    if uses_pgvector(database_url):
        vector_rank, vector_score = _pg_vector_ranks(
            database_url,
            tenant,
            qvec,
            use_ml,
            today,
            academic_year,
            department,
            keyword_rank,
        )
        vector_rank = [i for i in vector_rank if i in allowed_ids]
        for cid in keyword_rank:
            vector_score.setdefault(
                cid, 0.0
            )  # e.g. not yet embedded by the multilingual model
    else:
        sims = matrix[allowed_pos] @ qvec
        order = np.argsort(-sims)[:CANDIDATES]
        vector_rank = [idx.ids[allowed_pos[i]] for i in order]
        vector_score = {
            idx.ids[allowed_pos[i]]: float(sims[i]) for i in range(len(allowed_pos))
        }

    rerank = reranker is not None and mode == "hybrid" and not uses_local_script(query)
    limit = max(top_k, RERANK_CANDIDATES) if rerank else top_k
    fused: dict[str, float] = {}
    rankings = {
        "hybrid": (vector_rank, keyword_rank),
        "dense": (vector_rank,),
        "keyword": (keyword_rank,),
    }[mode]
    for ranking in rankings:
        for rank, cid in enumerate(ranking):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)

    by_id = {idx.ids[i]: idx.meta[i] for i in allowed_pos}
    ranked = sorted(
        fused, key=lambda cid: (fused[cid], vector_score[cid]), reverse=True
    )
    items = []
    for cid in ranked:
        if vector_score[cid] < min_score and cid not in keyword_rank[:3]:
            continue
        m = by_id[cid]
        items.append(
            {
                "id": cid,
                "kind": m["kind"],
                "content": m["content"],
                "score": round(fused[cid], 6),
                "vector_score": round(vector_score[cid], 4),
                "keyword_hit": cid in keyword_rank,
                "citation": {
                    "source_type": "document" if m["kind"] == "chunk" else "curated_qa",
                    "document_id": m["document_id"],
                    "qa_id": cid if m["kind"] == "curated_qa" else None,
                    "title": m["title"],
                    "heading_path": m["heading_path"],
                    "page": m["page"],
                    "academic_year": m["academic_year"],
                    "department": m["department"],
                    "valid_to": m["valid_to"].isoformat() if m["valid_to"] else None,
                    "url": m["url"],
                },
            }
        )
        if len(items) >= limit:
            break
    if rerank and len(items) > 1:
        # With its document and section: "Graduates work in automotive ..." answers a question
        # about CSE jobs only if the reranker cannot see it comes from the Mechanical curriculum.
        texts = [_with_context(i) for i in items]
        for item, score in zip(items, reranker.scores(query, texts)):
            item["rerank_score"] = round(score, 4)
        items.sort(key=lambda i: i["rerank_score"], reverse=True)
    items = items[:top_k]
    best = max((vector_score[c] for c in allowed_ids if c in vector_score), default=0.0)
    if items:
        _touch_curated(
            database_url,
            tenant,
            [i["id"] for i in items[:3] if i["kind"] == "curated_qa"],
        )
    return {
        "items": items,
        "max_vector_score": round(best, 4),
        "answerable": bool(items) and best >= min_score,
        "embedder": "multilingual" if use_ml else "primary",
    }


def _touch_curated(database_url: str, tenant: str, ids: list[str]) -> None:
    """Record use for the 90-day unused flag (PRD 8.3). Does not change updated_at, so the
    search index is not rebuilt."""
    if not ids:
        return
    with tx(database_url, tenant) as cur:
        cur.execute(
            "update kb.curated_qa set last_used_at = now() where id = any(%s::uuid[])",
            (ids,),
        )
