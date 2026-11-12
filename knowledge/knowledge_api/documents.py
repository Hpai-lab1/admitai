"""Documents (PRD F4.1, F4.5, F4.6, F4.8): ingest, approve, expire, version and roll back.

Lifecycle: processing -> draft | needs_review | rejected -> approved -> expired. Only approved,
in-date documents are searchable. A re-upload that replaces a document becomes version n+1 with
a diff summary; approving it expires the previous version, and rollback reverses that.
"""

from __future__ import annotations

import difflib
import hashlib
from datetime import date

import psycopg

from . import ingest
from .config import Settings
from .db import tx
from .embedder import Embedder, embed_ml

ACTIONS = {"approve", "expire", "reject", "rollback"}


class DocumentError(ValueError):
    pass


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def public(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "mime": row["mime"],
        "url": row["url"],
        "status": row["status"],
        "status_reason": row["status_reason"],
        "academic_year": row["academic_year"],
        "department": row["department"],
        "owner_role": row["owner_role"],
        "owner": row["owner"] or row["department"] or row["owner_role"],
        "valid_from": row["valid_from"].isoformat() if row["valid_from"] else None,
        "valid_to": row["valid_to"].isoformat() if row["valid_to"] else None,
        "version": row["version"],
        "previous_id": str(row["previous_id"]) if row["previous_id"] else None,
        "diff_summary": row["diff_summary"],
        "chunks": row["chunk_count"],
        "approved_by": row["approved_by"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


def _date(v) -> date | None:
    if v in (None, ""):
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def create(
    cur: psycopg.Cursor, tenant_id: str, filename: str, data: bytes, meta: dict
) -> tuple[dict, bool]:
    """Register an upload. Returns (row, duplicate). A file identical to a live document is linked to
    it instead of being indexed twice (F4 error handling)."""
    digest = checksum(data)
    cur.execute(
        "select * from kb.documents where checksum = %s and status not in ('rejected','expired')"
        " order by created_at desc limit 1",
        (digest,),
    )
    existing = cur.fetchone()
    if existing:
        return existing, True
    try:
        mime = ingest.mime_for(filename)
    except ingest.IngestError:
        mime = "application/octet-stream"
    previous = None
    if meta.get("replaces"):
        cur.execute(
            "select * from kb.documents where id::text = %s", (str(meta["replaces"]),)
        )
        previous = cur.fetchone()
        if previous is None:
            raise DocumentError("The document to replace was not found.")
    inherit = previous or {}
    cur.execute(
        "insert into kb.documents (tenant_id, source_id, title, mime, url, academic_year, department, owner_role,"
        " owner, valid_from, valid_to, version, previous_id, checksum)"
        " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *",
        (
            tenant_id,
            meta.get("source_id"),
            meta.get("title") or inherit.get("title") or filename,
            mime,
            meta.get("url"),
            meta.get("academic_year") or inherit.get("academic_year"),
            meta.get("department") or inherit.get("department"),
            meta.get("owner_role") or inherit.get("owner_role") or "admission_officer",
            meta.get("owner") or inherit.get("owner"),
            _date(meta.get("valid_from")),
            _date(meta.get("valid_to")),
            (previous["version"] + 1) if previous else 1,
            previous["id"] if previous else None,
            digest,
        ),
    )
    return cur.fetchone(), False


def process(
    settings: Settings,
    embedder: Embedder,
    tenant_id: str,
    doc_id: str,
    filename: str,
    data: bytes,
    blocks: list[ingest.Block] | None = None,
) -> dict:
    """Parse, chunk and embed one document. Failures leave nothing partially indexed."""
    status, reason, chunks, text = "draft", None, [], None
    try:
        blocks = blocks if blocks is not None else ingest.parse(filename, data)
        if any(b.ocr for b in blocks):
            # Recognised text can misread digits: staff check fees and dates before approving.
            reason = (
                "Text read by OCR from a scanned PDF: check numbers and dates against the "
                "original before approving."
            )
        text = ingest.full_text(blocks)
        chunks = ingest.chunk(blocks, settings.chunk_tokens, settings.chunk_overlap)
    except ingest.NeedsReview as exc:
        status, reason = "needs_review", str(exc)
    except ingest.IngestError as exc:
        status, reason = "rejected", str(exc)
    texts = [f"{c.heading_path}\n{c.content}".strip() for c in chunks]
    vectors = embedder.documents(texts) if chunks else []
    vectors_ml = embed_ml(embedder, texts)
    with tx(settings.database_url, tenant_id) as cur:
        cur.execute("select previous_id from kb.documents where id = %s", (doc_id,))
        prev_id = cur.fetchone()["previous_id"]
        diff = None
        if prev_id and text is not None:
            cur.execute("select content from kb.documents where id = %s", (prev_id,))
            diff = diff_summary(cur.fetchone()["content"] or "", text)
        for c, v, v_ml in zip(chunks, vectors, vectors_ml):
            cur.execute(
                "insert into kb.chunks (tenant_id, document_id, ord, content, heading_path, page, embedding,"
                " embedding_ml) values (%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant_id, doc_id, c.ord, c.content, c.heading_path, c.page, v, v_ml),
            )
        cur.execute(
            "update kb.documents set status=%s, status_reason=%s, content=%s, chunk_count=%s, diff_summary=%s,"
            " updated_at=now() where id=%s returning *",
            (status, reason, text, len(chunks), diff, doc_id),
        )
        return cur.fetchone()


def diff_summary(old: str, new: str) -> str:
    """Short human-readable summary of what changed between two versions (F4.8)."""
    old_lines, new_lines = old.splitlines(), new.splitlines()
    added = removed = 0
    examples: list[str] = []
    for line in difflib.unified_diff(old_lines, new_lines, lineterm="", n=0):
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
        if len(examples) < 6 and line[:1] in "+-" and line[1:].strip():
            examples.append(line[:160])
    if not added and not removed:
        return "No text changes."
    return f"{added} line(s) added, {removed} removed.\n" + "\n".join(examples)


def get(cur: psycopg.Cursor, doc_id: str) -> dict | None:
    cur.execute("select * from kb.documents where id::text = %s", (doc_id,))
    return cur.fetchone()


def list_(cur: psycopg.Cursor, status: str | None = None) -> list[dict]:
    cur.execute(
        "select * from kb.documents where (%s::text is null or status = %s) order by updated_at desc",
        (status, status),
    )
    return cur.fetchall()


def update_meta(cur: psycopg.Cursor, doc_id: str, meta: dict) -> dict:
    allowed = {
        "title",
        "academic_year",
        "department",
        "owner_role",
        "owner",
        "valid_from",
        "valid_to",
    }
    changes = {k: v for k, v in meta.items() if k in allowed}
    for k in ("valid_from", "valid_to"):
        if k in changes:
            changes[k] = _date(changes[k])
    if not changes:
        return get(cur, doc_id)
    sets = ", ".join(f"{k} = %s" for k in changes)
    cur.execute(
        f"update kb.documents set {sets}, updated_at = now() where id::text = %s returning *",
        (*changes.values(), doc_id),
    )
    return cur.fetchone()


def act(
    cur: psycopg.Cursor,
    doc_id: str,
    action: str,
    actor: str | None = None,
    reason: str | None = None,
) -> dict:
    if action not in ACTIONS:
        raise DocumentError(f"Unknown action '{action}'.")
    row = get(cur, doc_id)
    if row is None:
        raise KeyError(doc_id)
    if action == "approve":
        if row["status"] not in ("draft", "needs_review", "expired"):
            raise DocumentError(
                f"A document that is {row['status']} cannot be approved."
            )
        if row["chunk_count"] == 0:
            raise DocumentError(
                "This document has no indexed text, so approving it would serve nothing."
            )
        if row["valid_to"] and row["valid_to"] < date.today():
            raise DocumentError(
                "The valid_to date has passed; update it before approving."
            )
        cur.execute(
            "update kb.documents set status='approved', status_reason=null, approved_by=%s, approved_at=now(),"
            " updated_at=now() where id=%s returning *",
            (actor, row["id"]),
        )
        new = cur.fetchone()
        if row["previous_id"]:
            cur.execute(
                "update kb.documents set status='expired', status_reason=%s, updated_at=now()"
                " where id=%s and status='approved'",
                (f"replaced by version {row['version']}", row["previous_id"]),
            )
        return new
    if action in ("expire", "reject"):
        status = "expired" if action == "expire" else "rejected"
        cur.execute(
            "update kb.documents set status=%s, status_reason=%s, updated_at=now() where id=%s returning *",
            (status, reason or f"{action}d by {actor or 'staff'}", row["id"]),
        )
        return cur.fetchone()
    # rollback: expire this version and restore the previous one
    if not row["previous_id"]:
        raise DocumentError(
            "This is the first version; there is nothing to roll back to."
        )
    cur.execute(
        "update kb.documents set status='expired', status_reason=%s, updated_at=now() where id=%s",
        (f"rolled back by {actor or 'staff'}", row["id"]),
    )
    cur.execute(
        "update kb.documents set status='approved', status_reason=%s, approved_by=%s, approved_at=now(),"
        " updated_at=now() where id=%s returning *",
        (f"restored from version {row['version']}", actor, row["previous_id"]),
    )
    return cur.fetchone()


def expire_due(cur: psycopg.Cursor, today: date | None = None) -> int:
    """Automatic expiry (F4.6) so lists show the true state; search also filters by date itself."""
    cur.execute(
        "update kb.documents set status='expired', status_reason='valid_to passed', updated_at=now()"
        " where status='approved' and valid_to < %s",
        (today or date.today(),),
    )
    return cur.rowcount
