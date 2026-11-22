"""Applying and undoing lessons. Called from gate.apply_change / gate.rollback_change, so a
lesson goes through the same sandbox, gate and rollback as every other improvement.

Knowledge versions: each lesson going live, and each rollback, is a new row in
learn.knowledge_versions for the college; search reads the live rows on the next question, so
the very next session uses it.
"""

from __future__ import annotations

from ..config import Settings
from ..db import tx
from ..embedder import Embedder

KINDS = ("retrieval_boost", "search_alias", "catalogue_reextract")


def _centroid(settings: Settings, tenant: str, lesson_id: str) -> list[float] | None:
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select centroid from learn.lessons where id::text = %s", (lesson_id,)
        )
        row = cur.fetchone()
    return row["centroid"] if row else None


def apply(
    settings: Settings,
    embedder: Embedder,
    target: str,
    p: dict,
    idmap: dict[str, str] | None,
) -> dict:
    """Apply a lesson proposal to `target` (the college, or a sandbox copy with idmap)."""
    from .. import search

    d, kind = p["diff"], p["kind"]
    real = str(p["tenant_id"])
    lesson_id = d["lesson_id"]
    done: dict = {"lesson_id": lesson_id}
    if kind == "catalogue_reextract":
        from ..gate import _change_fees

        if d.get("fee_changes"):
            done.update(_change_fees(settings, target, d["fee_changes"], idmap))
        ic = d.get("intake_change")
        if ic:
            pid = (idmap or {}).get(ic["program_id"], ic["program_id"])
            with tx(settings.database_url, target) as cur:
                cur.execute(
                    "update kb.programs set intake = %s, updated_at = now() where id::text = %s",
                    (ic["new"], pid),
                )
            done["intake_change"] = ic
        search.invalidate(target)
        return done
    with tx(settings.database_url, target) as cur:
        if kind == "search_alias" or d.get("alias"):
            a = d.get("alias") or {"phrase": d["phrase"], "expansion": d["expansion"]}
            cur.execute(
                "insert into learn.lesson_aliases (tenant_id, lesson_id, phrase, expansion)"
                " values (%s, %s, %s, %s) returning id",
                (target, lesson_id, a["phrase"].lower(), a["expansion"]),
            )
            done["alias_ids"] = [cur.fetchone()["id"]]
        if kind == "retrieval_boost":
            from .. import paraphrase

            centroid = _centroid(settings, real, lesson_id) or embedder.query(
                paraphrase.expand(d["question"])
            )
            chunk = (idmap or {}).get(d.get("chunk_id"), d.get("chunk_id"))
            cur.execute(
                "insert into learn.retrieval_boosts (tenant_id, lesson_id, centroid,"
                " min_similarity, chunk_id, document_url, quote) values (%s,%s,%s,%s,%s,%s,%s)"
                " returning id",
                (
                    target,
                    lesson_id,
                    list(centroid),
                    settings.learning_boost_similarity,
                    chunk,
                    d.get("document_url"),
                    d["quote"],
                ),
            )
            done["boost_ids"] = [cur.fetchone()["id"]]
    search.invalidate(target)
    return done


def rollback(settings: Settings, tenant: str, done: dict) -> None:
    """Undo what apply() did on the college (fees are undone by gate.rollback_change)."""
    with tx(settings.database_url, tenant) as cur:
        if done.get("alias_ids"):
            cur.execute(
                "update learn.lesson_aliases set active = false where id = any(%s)",
                (done["alias_ids"],),
            )
        if done.get("boost_ids"):
            cur.execute(
                "update learn.retrieval_boosts set active = false where id = any(%s)",
                (done["boost_ids"],),
            )
        ic = done.get("intake_change")
        if ic:
            cur.execute(
                "update kb.programs set intake = %s, updated_at = now() where id::text = %s",
                (ic["old"], ic["program_id"]),
            )


def new_version(
    cur,
    tenant: str,
    lesson_id: str | None,
    proposal_id: str | None,
    action: str,
    note: str,
) -> int:
    cur.execute(
        "select coalesce(max(version), 0) + 1 as v from learn.knowledge_versions"
    )
    v = cur.fetchone()["v"]
    cur.execute(
        "insert into learn.knowledge_versions (tenant_id, version, lesson_id, proposal_id, action,"
        " note) values (%s,%s,%s,%s,%s,%s)",
        (tenant, v, lesson_id, proposal_id, action, note[:500]),
    )
    return v


# ------------------------------------------------------------------ sandboxes


def copy_to_sandbox(cur, tenant: str, sandbox: str, idmap: dict[str, str]) -> None:
    """A sandbox copy answers with the college's live lessons too, so before and after compare
    like with like."""
    cur.execute(
        "insert into learn.lesson_aliases (tenant_id, lesson_id, phrase, expansion)"
        " select %s, lesson_id, phrase, expansion from learn.lesson_aliases"
        " where tenant_id = %s and active",
        (sandbox, tenant),
    )
    cur.execute(
        "select lesson_id, centroid, min_similarity, chunk_id::text, document_url, quote"
        " from learn.retrieval_boosts where tenant_id = %s and active",
        (tenant,),
    )
    for b in cur.fetchall():
        cur.execute(
            "insert into learn.retrieval_boosts (tenant_id, lesson_id, centroid, min_similarity,"
            " chunk_id, document_url, quote) values (%s,%s,%s,%s,%s,%s,%s)",
            (
                sandbox,
                b["lesson_id"],
                b["centroid"],
                b["min_similarity"],
                idmap.get(b["chunk_id"]) if b["chunk_id"] else None,
                b["document_url"],
                b["quote"],
            ),
        )


def drop_sandbox(conn, sandbox: str) -> None:
    conn.execute("delete from learn.lesson_aliases where tenant_id = %s", (sandbox,))
    conn.execute("delete from learn.retrieval_boosts where tenant_id = %s", (sandbox,))
