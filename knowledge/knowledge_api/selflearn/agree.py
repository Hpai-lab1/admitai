"""Stage 4, agreement: retrieval and style lessons need the same issue from at least
LEARNING_MIN_AGREEING_SESSIONS distinct, trusted sessions before anything is drafted.

Feedback about similar questions (cosine of the question vectors >= the clustering threshold the
Feedback Analyst uses) and of the same family joins one forming lesson (status 'proposed'). Only
distinct session fingerprints of trusted feedback count: fifty complaints from one session count
once. Fact claims never pass on agreement; they need evidence (evidence.py).
"""

from __future__ import annotations

import numpy as np

from ..db import Jsonb

FAMILY = {
    "retrieval_miss": "retrieval",
    "missing_answer": "retrieval",
    "answer_style": "style",
    "wrong_fact_claim": "fact",
}


def unit(v) -> np.ndarray:
    a = np.asarray(v, dtype=np.float32)
    return a / max(float(np.linalg.norm(a)), 1e-9)


def distinct_trusted(entries: list[tuple[str, float]], minimum: float) -> set[str]:
    """entries: (session fingerprint, trust score). The sessions that count towards agreement."""
    return {fp for fp, t in entries if fp and t >= minimum}


def met(sessions: list[str] | set[str], needed: int) -> bool:
    return len(set(sessions)) >= needed


def attach(
    cur,
    tenant: str,
    cls: str,
    question: str,
    vec: np.ndarray,
    similarity: float,
    feedback_id: int,
    fp: str,
    trusted: bool,
) -> dict:
    """Join the forming lesson for this issue, or open one. Returns the lesson row."""
    family = FAMILY[cls]
    cur.execute(
        "select * from learn.lessons where status = 'proposed' and issue_class = %s"
        " for update",
        (family,),
    )
    best, match = similarity, None
    for row in cur.fetchall():
        if row["centroid"] is None:
            continue
        sim = float(np.dot(vec, unit(row["centroid"])))
        if sim >= best:
            best, match = sim, row
    if match is None:
        cur.execute(
            "insert into learn.lessons (tenant_id, issue_class, question, centroid, payload)"
            " values (%s, %s, %s, %s, %s) returning *",
            (tenant, family, question, vec.tolist(), Jsonb({"classes": [cls]})),
        )
        match = cur.fetchone()
    centroid = unit(unit(match["centroid"]) * len(match["created_from"] or [1]) + vec)
    classes = sorted({*(match["payload"] or {}).get("classes", []), cls})
    cur.execute(
        "update learn.lessons set created_from = array_append(created_from, %s),"
        " sessions = case when %s and not (%s = any(sessions)) then array_append(sessions, %s)"
        "   else sessions end,"
        " centroid = %s, payload = payload || %s, updated_at = now() where id = %s returning *",
        (
            feedback_id,
            trusted,
            fp,
            fp,
            centroid.tolist(),
            Jsonb({"classes": classes}),
            match["id"],
        ),
    )
    return cur.fetchone()
