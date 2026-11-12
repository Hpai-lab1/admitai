"""Curated Q&A (PRD F4.4, 8.3): expert-approved answers with a mandatory academic year and expiry.
Every change bumps the version and keeps a snapshot, so any version can be restored."""

from __future__ import annotations

from datetime import date

import psycopg

from .db import Jsonb
from .embedder import Embedder, embed_ml

STATUSES = {"draft", "approved", "suspended", "expired"}
FIELDS = (
    "question",
    "variants",
    "answer",
    "category",
    "owner_role",
    "department",
    "academic_year",
    "valid_to",
)


class CuratedError(ValueError):
    pass


def _text_for_embedding(q: dict) -> str:
    return " ".join([q["question"], *q.get("variants", []), q["answer"]])


def _snapshot(row: dict) -> dict:
    snap = {k: row[k] for k in FIELDS + ("status",)}
    snap["valid_to"] = (
        row["valid_to"].isoformat()
        if isinstance(row["valid_to"], date)
        else row["valid_to"]
    )
    return snap


def _history(cur: psycopg.Cursor, row: dict, action: str, actor: str | None) -> None:
    cur.execute(
        "insert into kb.curated_qa_history (tenant_id, qa_id, version, snapshot, action, actor)"
        " values (%s,%s,%s,%s,%s,%s)",
        (
            row["tenant_id"],
            row["id"],
            row["version"],
            Jsonb(_snapshot(row)),
            action,
            actor,
        ),
    )


def public(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "question": row["question"],
        "variants": row["variants"],
        "answer": row["answer"],
        "category": row["category"],
        "owner_role": row["owner_role"],
        "department": row["department"],
        "academic_year": row["academic_year"],
        "valid_to": row["valid_to"].isoformat(),
        "status": row["status"],
        "status_reason": row["status_reason"],
        "version": row["version"],
        "last_used_at": row["last_used_at"].isoformat()
        if row["last_used_at"]
        else None,
        "source_correction_id": row["source_correction_id"],
        "updated_at": row["updated_at"].isoformat(),
    }


def _validate(data: dict) -> dict:
    if (
        not (data.get("question") or "").strip()
        or not (data.get("answer") or "").strip()
    ):
        raise CuratedError("question and answer are required")
    if not data.get("academic_year"):
        raise CuratedError("academic_year is required (PRD 8.3)")
    if not data.get("valid_to"):
        raise CuratedError("valid_to is required (PRD 8.3)")
    v = data["valid_to"]
    data["valid_to"] = v if isinstance(v, date) else date.fromisoformat(str(v))
    data["variants"] = [
        s.strip() for s in data.get("variants") or [] if s and s.strip()
    ]
    data.setdefault("owner_role", "admission_officer")
    return data


def create(
    cur: psycopg.Cursor,
    embedder: Embedder,
    tenant_id: str,
    data: dict,
    actor: str | None = None,
    status: str = "draft",
) -> dict:
    data = _validate(dict(data))
    if status not in STATUSES:
        raise CuratedError(f"unknown status {status}")
    text = _text_for_embedding(data)
    emb, emb_ml = embedder.documents([text])[0], embed_ml(embedder, [text])[0]
    cur.execute(
        "insert into kb.curated_qa (tenant_id, question, variants, answer, category, owner_role, department,"
        " academic_year, valid_to, status, embedding, embedding_ml, source_correction_id, external_ref)"
        " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        " on conflict (tenant_id, external_ref) where external_ref is not null do nothing returning *",
        (
            tenant_id,
            data["question"].strip(),
            data["variants"],
            data["answer"].strip(),
            data.get("category"),
            data["owner_role"],
            data.get("department"),
            data["academic_year"],
            data["valid_to"],
            status,
            emb,
            emb_ml,
            data.get("source_correction_id"),
            data.get("external_ref"),
        ),
    )
    row = cur.fetchone()
    if row is None:  # same external_ref imported before
        cur.execute(
            "select * from kb.curated_qa where external_ref = %s",
            (data["external_ref"],),
        )
        return cur.fetchone()
    _history(cur, row, "create", actor)
    return row


def get(cur: psycopg.Cursor, qa_id: str) -> dict | None:
    cur.execute("select * from kb.curated_qa where id::text = %s", (qa_id,))
    return cur.fetchone()


def list_(cur: psycopg.Cursor, status: str | None = None) -> list[dict]:
    cur.execute(
        "select * from kb.curated_qa where (%s::text is null or status = %s) order by updated_at desc",
        (status, status),
    )
    return cur.fetchall()


def update(
    cur: psycopg.Cursor,
    embedder: Embedder,
    qa_id: str,
    changes: dict,
    actor: str | None = None,
) -> dict:
    """Editing content creates a new version that needs approval again (status -> draft)."""
    row = get(cur, qa_id)
    if row is None:
        raise KeyError(qa_id)
    merged = {k: row[k] for k in FIELDS} | {
        k: v for k, v in changes.items() if k in FIELDS and v is not None
    }
    merged = _validate(merged)
    text = _text_for_embedding(merged)
    emb, emb_ml = embedder.documents([text])[0], embed_ml(embedder, [text])[0]
    cur.execute(
        "update kb.curated_qa set question=%s, variants=%s, answer=%s, category=%s, owner_role=%s, department=%s,"
        " academic_year=%s, valid_to=%s, embedding=%s, embedding_ml=%s, version=version+1, status='draft', status_reason=null,"
        " updated_at=now() where id=%s returning *",
        (
            merged["question"],
            merged["variants"],
            merged["answer"],
            merged["category"],
            merged["owner_role"],
            merged["department"],
            merged["academic_year"],
            merged["valid_to"],
            emb,
            emb_ml,
            row["id"],
        ),
    )
    row = cur.fetchone()
    _history(cur, row, "edit", actor)
    return row


def set_status(
    cur: psycopg.Cursor,
    qa_id: str,
    status: str,
    actor: str | None = None,
    reason: str | None = None,
) -> dict:
    if status not in STATUSES:
        raise CuratedError(f"unknown status {status}")
    row = get(cur, qa_id)
    if row is None:
        raise KeyError(qa_id)
    if status == "approved" and row["valid_to"] < date.today():
        raise CuratedError(
            "cannot approve an answer whose valid_to date has passed; edit the dates first"
        )
    cur.execute(
        "update kb.curated_qa set status=%s, status_reason=%s, updated_at=now() where id=%s returning *",
        (status, reason, row["id"]),
    )
    row = cur.fetchone()
    _history(cur, row, status, actor)
    return row


def rollback(
    cur: psycopg.Cursor,
    embedder: Embedder,
    qa_id: str,
    version: int,
    actor: str | None = None,
) -> dict:
    """Restore the content of an earlier version as a new version (status as it was then)."""
    cur.execute(
        "select snapshot from kb.curated_qa_history where qa_id::text = %s and version = %s"
        " order by id desc limit 1",
        (qa_id, version),
    )
    h = cur.fetchone()
    if h is None:
        raise KeyError(f"{qa_id} v{version}")
    snap = h["snapshot"]
    row = update(cur, embedder, qa_id, {k: snap[k] for k in FIELDS}, actor)
    if (
        snap["status"] == "approved"
        and date.fromisoformat(snap["valid_to"]) >= date.today()
    ):
        row = set_status(
            cur, qa_id, "approved", actor, reason=f"rolled back to v{version}"
        )
    return row


def history(cur: psycopg.Cursor, qa_id: str) -> list[dict]:
    cur.execute(
        "select version, action, actor, at, snapshot from kb.curated_qa_history where qa_id::text = %s order by id",
        (qa_id,),
    )
    return [{**h, "at": h["at"].isoformat()} for h in cur.fetchall()]


def expire_due(cur: psycopg.Cursor, today: date | None = None) -> int:
    """Automatic expiry (F4.6): answers past valid_to stop being served and are marked expired."""
    cur.execute(
        "update kb.curated_qa set status='expired', status_reason='valid_to passed', updated_at=now()"
        " where status in ('approved','draft','suspended') and valid_to < %s returning *",
        (today or date.today(),),
    )
    rows = cur.fetchall()
    for r in rows:
        _history(cur, r, "expired", "system")
    return len(rows)
