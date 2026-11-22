"""The per-feedback pipeline (stages 1-5) and the lesson steps after it (gate, live, approval).

Called by the worker (worker.py); every function is safe to call again: a feedback row is
processed once (claimed with SKIP LOCKED, marked done), a lesson moves forward only from the
status it is in.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from .. import gate, learning
from ..assistant_client import Assistant
from ..config import Settings
from ..db import Jsonb, tx
from ..embedder import Embedder
from ..pii import mask_pii
from . import agree, classify, draft, evidence, event, trust
from .apply import new_version


def now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ intake


def take_new_events(settings: Settings, tenant: str) -> int:
    """Copy new feedback.created events into learn.feedback_events (once each)."""
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "insert into learn.feedback_events (tenant_id, event_ref, conversation_id, message_id,"
            " trace_id, rating_kind, rating_value, reason, channel, actor, created_at)"
            " select e.tenant_id, e.id, e.payload->>'conversation_id', e.payload->>'message_id',"
            "  e.payload->>'trace_id', e.payload->>'kind', (e.payload->>'value')::int,"
            "  e.payload->>'reason', coalesce(e.payload->>'channel', 'web_chat'),"
            "  coalesce(e.payload->>'actor', 'visitor'), e.received_at"
            " from learn.events e where e.type = 'feedback.created'"
            "  and e.payload ? 'kind' and not (e.payload ? 'erased')"
            "  and e.received_at > now() - make_interval(secs => %s)"
            " on conflict (tenant_id, event_ref) do nothing",
            (settings.learning_backfill_h * 3600,),
        )
        return cur.rowcount


def claim(settings: Settings, tenant: str, limit: int = 20) -> list[dict]:
    with tx(settings.database_url, tenant) as cur:
        # Rows left 'processing' by a crash are taken again after ten minutes.
        cur.execute(
            "update learn.feedback_events set status = 'new' where status = 'processing'"
            " and next_attempt_at < now() - interval '10 minutes'"
        )
        cur.execute(
            "select f.id from learn.feedback_events f where f.status = 'new'"
            " and f.next_attempt_at <= now() order by f.id limit %s for update skip locked",
            (limit,),
        )
        ids = [r["id"] for r in cur.fetchall()]
        if not ids:
            return []
        cur.execute(
            "update learn.feedback_events set status = 'processing', attempts = attempts + 1,"
            " next_attempt_at = now() where id = any(%s) returning *",
            (ids,),
        )
        return cur.fetchall()


def fail(settings: Settings, tenant: str, fid: int, attempts: int, err: str) -> None:
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "update learn.feedback_events set status = case when %s >= 3 then 'failed' else 'new' end,"
            " next_attempt_at = now() + make_interval(secs => %s), last_error = %s where id = %s",
            (attempts, 30 * 2**attempts, err[:500], fid),
        )


def _finish(cur, fid: int, note: str, lesson_id=None) -> None:
    cur.execute(
        "update learn.feedback_events set status = 'done', processed_at = now(),"
        " outcome_note = %s, lesson_id = coalesce(%s, lesson_id) where id = %s",
        (note[:500], lesson_id, fid),
    )


def _embed(embedder: Embedder, text: str) -> np.ndarray:
    """In the space search compares questions in: after the shared admissions vocabulary."""
    from .. import paraphrase

    return agree.unit(embedder.query(paraphrase.expand(text or "")))


# ------------------------------------------------------------------ stages 1-5


def process(
    settings: Settings,
    embedder: Embedder,
    assistant: Assistant,
    tenant: str,
    row: dict,
    model=None,
    fetch: evidence.Fetcher | None = None,
) -> dict:
    """One feedback, through understand -> trust -> agreement / evidence -> draft."""
    fid = row["id"]
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select payload from learn.events where id = %s", (row["event_ref"],)
        )
        payload = (cur.fetchone() or {}).get("payload") or {}
    b = learning._from_b(assistant, tenant, row.get("message_id"))
    value = row.get("rating_value")
    rating = (
        "up"
        if (row["rating_kind"] == "thumb" and value == 1)
        or (row["rating_kind"] == "rating" and (value or 0) >= 4)
        else "down"
    )
    fb = {
        "id": fid,
        "created_at": row["created_at"],
        "message_id": row.get("message_id"),
        "rating": rating,
        "reason": row.get("reason"),
        "comment": mask_pii(payload.get("comment")),
        "question": mask_pii(b.get("question") or payload.get("question")),
        "answer": mask_pii(b.get("answer")),
        "outcome": b.get("outcome"),
        "citations": b.get("sources") or [],
        "actor": row.get("actor"),
        "session_fp": trust.session_fp(settings.learning_session_salt, tenant, payload),
    }
    retrieved = []
    if fb["question"]:
        from .. import search

        res = search.search(
            settings.database_url,
            embedder,
            tenant,
            fb["question"],
            top_k=4,
            lessons=False,
        )
        retrieved = [
            {"id": i["id"], "title": i["citation"]["title"], "score": i["vector_score"]}
            for i in res["items"]
        ]
    analysis = classify.classify(
        fb, model if settings.learning_classifier == "llm" else None
    )
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "update learn.feedback_events set rating = %s, comment = %s, question = %s, answer = %s,"
            " outcome = %s, citations = %s, retrieved_refs = %s, session_fp = %s where id = %s",
            (
                rating,
                fb["comment"],
                fb["question"],
                fb["answer"],
                fb["outcome"],
                Jsonb(fb["citations"]),
                Jsonb(retrieved),
                fb["session_fp"],
                fid,
            ),
        )
        hist = trust.history(cur, fb, settings.learning_min_trust)
        t = trust.score(fb, analysis, hist, settings.learning_max_feedback_per_hour)
        cur.execute(
            "insert into learn.feedback_analysis (feedback_id, tenant_id, class, claim, topic,"
            " programme, confidence, model, reasoning) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " on conflict (feedback_id) do update set class = excluded.class,"
            " claim = excluded.claim, confidence = excluded.confidence, model = excluded.model,"
            " reasoning = excluded.reasoning",
            (
                fid,
                tenant,
                analysis.cls,
                mask_pii(analysis.claim),
                analysis.topic,
                analysis.programme,
                analysis.confidence,
                analysis.model,
                analysis.reasoning,
            ),
        )
        cur.execute(
            "insert into learn.trust_scores (tenant_id, subject_kind, subject_id, score, factors)"
            " values (%s, 'feedback', %s, %s, %s) on conflict (tenant_id, subject_kind, subject_id)"
            " do update set score = excluded.score, factors = excluded.factors, updated_at = now()",
            (tenant, str(fid), t.score, Jsonb(t.factors)),
        )
        cur.execute(
            "insert into learn.trust_scores (tenant_id, subject_kind, subject_id, score, factors)"
            " select %s, 'session', %s, avg(t.score), jsonb_build_object('feedback', count(*))"
            " from learn.feedback_events f join learn.trust_scores t on t.subject_kind = 'feedback'"
            "  and t.subject_id = f.id::text where f.session_fp = %s"
            " on conflict (tenant_id, subject_kind, subject_id) do update set score = excluded.score,"
            " factors = excluded.factors, updated_at = now()",
            (tenant, fb["session_fp"], fb["session_fp"]),
        )
    trusted = t.trusted(settings.learning_min_trust)
    event(
        "understood",
        tenant=tenant,
        feedback=fid,
        cls=analysis.cls,
        model=analysis.model,
        trust=t.score,
        trusted=trusted,
    )
    result = {
        "feedback_id": fid,
        "class": analysis.cls,
        "trust": t.score,
        "trusted": trusted,
    }

    with tx(settings.database_url, tenant) as cur:
        if (
            analysis.cls in ("praise", "junk_abuse")
            or not trusted
            or not fb["question"]
        ):
            note = (
                "praise: recorded for the lessons and sources it confirms"
                if analysis.cls == "praise"
                else "ignored: abuse, injection or not about the answer"
                if analysis.cls == "junk_abuse"
                else "stored: not trusted enough to teach anything"
                if not trusted
                else "stored: the conversation could not be found"
            )
            _finish(cur, fid, note)
            return {**result, "note": note}
        vec = _embed(embedder, fb["question"])
        if analysis.cls == "wrong_fact_claim":
            if not analysis.claim:
                note = "a wrong answer without a specific claim: left to the Feedback Analyst"
                _finish(cur, fid, note)
                return {**result, "note": note}
            cur.execute(
                "insert into learn.lessons (tenant_id, issue_class, status, question, centroid,"
                " payload, created_from, sessions) values (%s, 'fact', 'verifying', %s, %s, %s,"
                " %s, %s) returning *",
                (
                    tenant,
                    fb["question"],
                    vec.tolist(),
                    Jsonb({"claim": analysis.claim, "classes": [analysis.cls]}),
                    [fid],
                    [fb["session_fp"]],
                ),
            )
            lesson = cur.fetchone()
            _finish(cur, fid, "a fact claim: checking the source page", lesson["id"])
        else:
            lesson = agree.attach(
                cur,
                tenant,
                analysis.cls,
                fb["question"],
                vec,
                settings.cluster_similarity,
                fid,
                fb["session_fp"],
                trusted,
            )
            if analysis.alias:
                cur.execute(
                    "update learn.lessons set payload = payload || %s where id = %s",
                    (Jsonb({"alias": list(analysis.alias)}), lesson["id"]),
                )
                lesson["payload"] = {**lesson["payload"], "alias": list(analysis.alias)}
            n = len(set(lesson["sessions"]))
            need = settings.learning_min_agreeing_sessions
            _finish(
                cur,
                fid,
                f"joined an issue: {n} of {need} distinct trusted sessions",
                lesson["id"],
            )
            event(
                "agreement",
                tenant=tenant,
                feedback=fid,
                lesson=lesson["id"],
                sessions=n,
                need=need,
            )
            if not agree.met(lesson["sessions"], need):
                return {
                    **result,
                    "lesson_id": str(lesson["id"]),
                    "note": "waiting for agreement",
                }
            cur.execute(
                "update learn.lessons set status = 'verifying', updated_at = now() where id = %s"
                " and status = 'proposed'",
                (lesson["id"],),
            )
            if cur.rowcount == 0:
                return {**result, "lesson_id": str(lesson["id"])}
    out = verify_and_draft(
        settings, embedder, tenant, str(lesson["id"]), model=model, fetch=fetch
    )
    return {**result, "lesson_id": str(lesson["id"]), **out}


def _lesson(cur, lesson_id: str) -> dict:
    cur.execute(
        "select * from learn.lessons where id::text = %s for update", (lesson_id,)
    )
    return cur.fetchone()


def _examples(cur, ids: list[int]) -> list[dict]:
    cur.execute(
        "select f.question, f.answer, f.reason, f.comment, f.conversation_id, f.created_at,"
        " a.class from learn.feedback_events f left join learn.feedback_analysis a"
        " on a.feedback_id = f.id where f.id = any(%s) order by f.id",
        (ids,),
    )
    return [
        {
            "question": r["question"],
            "answer": (r["answer"] or "")[:300],
            "reason": r["reason"],
            "comment": (r["comment"] or "")[:300],
            "kind": r["class"],
            "conversation_id": r["conversation_id"],
            "at": r["created_at"].isoformat(),
        }
        for r in cur.fetchall()
    ]


def _set(cur, lesson_id: str, status: str, reason: str, **cols) -> None:
    extra = "".join(f", {k} = %s" for k in cols)
    cur.execute(
        f"update learn.lessons set status = %s, reason = %s, updated_at = now(){extra}"
        " where id::text = %s",
        (
            status,
            reason[:500],
            *[Jsonb(v) if isinstance(v, dict) else v for v in cols.values()],
            lesson_id,
        ),
    )
    event("lesson", lesson=lesson_id, status=status, reason=reason[:120])


def _evidence(cur, tenant: str, lesson_id: str, **e) -> None:
    cur.execute(
        "insert into learn.lesson_evidence (tenant_id, lesson_id, source_url, document_id, chunk_id,"
        " quote, supports, note, content_hash) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            tenant,
            lesson_id,
            e.get("url"),
            e.get("document_id"),
            e.get("chunk_id"),
            e["quote"],
            e.get("supports"),
            e.get("note"),
            e.get("content_hash"),
        ),
    )


def _to_inbox(cur, tenant: str, lesson: dict, prop: dict, reason: str) -> str:
    """A proposal in the staff approval inbox (status pending), with the evidence and why."""
    cur.execute(
        "insert into learn.proposals (tenant_id, kind, root_cause, rung, title, proposal, diff,"
        " examples, severity, affected, due_at, note) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        " returning id",
        (
            tenant,
            prop["kind"],
            prop["root_cause"],
            prop["rung"],
            prop["title"][:300],
            prop["proposal"],
            Jsonb(prop["diff"]),
            Jsonb(prop["examples"]),
            prop["severity"],
            max(1, len(set(lesson["sessions"]))),
            learning.add_working_days(now(), 3),
            f"Self-learning: {reason}"[:500],
        ),
    )
    pid = cur.fetchone()["id"]
    _set(cur, str(lesson["id"]), "pending_approval", reason, proposal_id=pid)
    return str(pid)


def verify_and_draft(
    settings: Settings,
    embedder: Embedder,
    tenant: str,
    lesson_id: str,
    model=None,
    fetch: evidence.Fetcher | None = None,
    attempt: int = 0,
) -> dict:
    """Stages 3 and 5 for a lesson in 'verifying': find the evidence, draft, queue for the gate
    (status 'gated') or send to the inbox (status 'pending_approval'), or reject."""
    fetch = fetch or evidence.http_fetcher()
    with tx(settings.database_url, tenant) as cur:
        lesson = _lesson(cur, lesson_id)
        if lesson is None or lesson["status"] != "verifying":
            return {"status": lesson["status"] if lesson else None}
        examples = _examples(cur, list(lesson["created_from"]))
    family = lesson["issue_class"]
    q = lesson["question"]

    if family == "style":
        hint = (
            "Visitors from several sessions found answers to questions like this hard to follow. "
            "Consider shorter sentences and explaining admissions terms."
        )
        d = {
            "id": lesson_id,
            "type": "answer_hint",
            "payload": {"question": q, "hint": hint},
        }
        with tx(settings.database_url, tenant) as cur:
            cur.execute(
                "update learn.lessons set type = 'answer_hint', payload = payload || %s"
                " where id::text = %s",
                (Jsonb(d["payload"]), lesson_id),
            )
            pid = _to_inbox(
                cur,
                tenant,
                lesson,
                draft.proposal(d, examples),
                "answer style: Module B has no input for answer hints yet, so a person applies it",
            )
        return {"status": "pending_approval", "proposal_id": pid}

    if family == "fact":
        claim_text = (lesson["payload"] or {}).get("claim") or ""
        v = evidence.verify_fact(settings, tenant, claim_text, q, fetch)
        with tx(settings.database_url, tenant) as cur:
            for e in v.evidence:
                _evidence(cur, tenant, lesson_id, **e, note=v.reason)
            if v.decision == "contradicted":
                _set(
                    cur,
                    lesson_id,
                    "rejected",
                    f"the source contradicts the claim: {v.reason}",
                )
                return {"status": "rejected", "reason": v.reason}
            payload = {
                "question": q,
                "claim": claim_text,
                "reason": v.reason,
                "url": (v.evidence[0]["url"] if v.evidence else None),
                **v.changes,
            }
            d = {"id": lesson_id, "type": "catalogue_reextract", "payload": payload}
            if v.decision in ("supported", "page_changed") and v.changes:
                cur.execute(
                    "update learn.lessons set type = 'catalogue_reextract', payload = payload || %s"
                    " where id::text = %s",
                    (Jsonb(payload), lesson_id),
                )
                why = (
                    "the page supports the claim; protected values wait for approval"
                    if v.decision == "supported"
                    else "the page changed since the catalogue was extracted; a person decides"
                )
                pid = _to_inbox(cur, tenant, lesson, draft.proposal(d, examples), why)
                return {
                    "status": "pending_approval",
                    "proposal_id": pid,
                    "decision": v.decision,
                }
            # Nothing could confirm or refute it: a person decides (no values change on approval).
            prop = draft.proposal(d, examples)
            prop = {
                **prop,
                "kind": "document_action",
                "root_cause": "outdated",
                "title": f"Unverified fact claim: {claim_text[:120]}",
                "proposal": f'A visitor says: "{claim_text[:300]}". It could not be checked '
                f"automatically ({v.reason}). Check it with the admissions office; approving "
                "records it as a task and changes nothing by itself.",
                "diff": {
                    "lesson_id": lesson_id,
                    "action": "review_catalogue",
                    "source_type": "catalogue",
                    "question": q,
                    "claim": claim_text,
                    "evidence": v.evidence,
                },
            }
            pid = _to_inbox(cur, tenant, lesson, prop, f"unverifiable: {v.reason}")
            return {
                "status": "pending_approval",
                "proposal_id": pid,
                "decision": v.decision,
            }

    # retrieval family
    with tx(settings.database_url, tenant) as cur:
        # The pages the disliked answers cited (for a retrieval miss): visitors said they were
        # not the answer, so the evidence must come from elsewhere.
        cur.execute(
            "select distinct c->>'ref_id' as ref from learn.feedback_events f"
            " join learn.feedback_analysis a on a.feedback_id = f.id,"
            " jsonb_array_elements(f.citations) c where f.id = any(%s)"
            " and a.class = 'retrieval_miss' and c->>'source_type' = 'document'",
            (list(lesson["created_from"]),),
        )
        cited = {r["ref"] for r in cur.fetchall() if r["ref"]}
    passage = evidence.find_passage(
        settings, embedder, tenant, q, model=model, exclude_docs=cited
    )
    if passage is None:
        # Maybe the page changed since it was crawled: fetch the likeliest pages again.
        from .. import search

        res = search.search(
            settings.database_url, embedder, tenant, q, top_k=3, lessons=False
        )
        for it in res["items"]:
            url = evidence.stale(settings, tenant, it["citation"]["document_id"])
            page = fetch(url) if url else None
            quote = evidence.fresh_passage(q, page) if page else None
            if quote:
                with tx(settings.database_url, tenant) as cur:
                    _evidence(
                        cur,
                        tenant,
                        lesson_id,
                        url=page.url,
                        quote=quote,
                        supports=True,
                        content_hash=page.content_hash,
                        note="found only on the page as it is today, not in the indexed copy",
                    )
                    lesson = _lesson(cur, lesson_id)
                    d = {
                        "id": lesson_id,
                        "type": "curated_from_source",
                        "payload": {
                            "question": q,
                            "answer": quote,
                            "title": page.title,
                            "document_url": page.url,
                            "quote": quote,
                            "chunk_id": None,
                        },
                    }
                    prop = draft.proposal(d, examples)
                    pid = _to_inbox(
                        cur,
                        tenant,
                        lesson,
                        prop,
                        "the page changed since it was crawled: re-crawl it, or approve this answer",
                    )
                return {"status": "pending_approval", "proposal_id": pid}
        with tx(settings.database_url, tenant) as cur:
            _set(
                cur,
                lesson_id,
                "rejected",
                "no evidence: the college's pages do not answer this",
            )
        return {"status": "rejected", "reason": "no evidence"}
    alias = (lesson["payload"] or {}).get("alias")
    if passage.rank == 0 and not alias and attempt == 0:
        with tx(settings.database_url, tenant) as cur:
            _evidence(
                cur,
                tenant,
                lesson_id,
                url=passage.url,
                document_id=passage.document_id,
                chunk_id=passage.chunk_id,
                quote=passage.quote,
                supports=True,
                content_hash=passage.content_hash,
            )
            _set(
                cur,
                lesson_id,
                "rejected",
                "search already finds this page first; the problem is not retrieval",
            )
        return {"status": "rejected", "reason": "already found first"}
    d = (
        draft.curated_lesson(q, passage)
        if attempt
        else draft.retrieval_lesson(q, passage, tuple(alias) if alias else None)
    )
    with tx(settings.database_url, tenant) as cur:
        _evidence(
            cur,
            tenant,
            lesson_id,
            url=passage.url,
            document_id=passage.document_id,
            chunk_id=passage.chunk_id,
            quote=passage.quote,
            supports=True,
            content_hash=passage.content_hash,
        )
        cur.execute(
            "update learn.lessons set type = %s, payload = payload || %s, attempt = %s,"
            " status = 'gated', gate_started_at = null, updated_at = now() where id::text = %s",
            (d["type"], Jsonb(d["payload"]), attempt, lesson_id),
        )
    event(
        "drafted",
        tenant=tenant,
        lesson=lesson_id,
        type=d["type"],
        rank_before=passage.rank,
    )
    return {"status": "gated", "type": d["type"]}


# ------------------------------------------------------------------ stage 6-7: gate, live


def next_for_gate(settings: Settings, tenant: str) -> str | None:
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select id::text from learn.lessons where status = 'gated' and gate_started_at is null"
            " order by updated_at limit 1 for update skip locked"
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                "update learn.lessons set gate_started_at = now() where id::text = %s",
                (row["id"],),
            )
        return row["id"] if row else None


def gate_lesson(
    settings: Settings,
    embedder: Embedder,
    assistant: Assistant,
    tenant: str,
    lesson_id: str,
    model=None,
    fetch: evidence.Fetcher | None = None,
) -> dict:
    """Run the existing evaluation gate for a drafted lesson; live if it passes and its type may
    go live without a person, otherwise to the inbox."""
    with tx(settings.database_url, tenant) as cur:
        lesson = _lesson(cur, lesson_id)
        examples = _examples(cur, list(lesson["created_from"]))
        d = {"id": lesson_id, "type": lesson["type"], "payload": lesson["payload"]}
        prop = draft.proposal(d, examples, cur)
        auto = prop["auto_ok"] and lesson["type"] in settings.learning_auto_live
        if not auto:
            pid = _to_inbox(
                cur,
                tenant,
                lesson,
                prop,
                "this kind of lesson waits for a person (LEARNING_AUTO_LIVE)",
            )
            return {"status": "pending_approval", "proposal_id": pid}
        cur.execute(
            "insert into learn.proposals (tenant_id, kind, root_cause, rung, title, proposal, diff,"
            " examples, severity, affected, due_at, status, decided_by, decided_at, note)"
            " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'evaluating','self-learning',now(),%s)"
            " returning *",
            (
                tenant,
                prop["kind"],
                prop["root_cause"],
                prop["rung"],
                prop["title"][:300],
                prop["proposal"],
                Jsonb(prop["diff"]),
                Jsonb(prop["examples"]),
                prop["severity"],
                max(1, len(set(lesson["sessions"]))),
                learning.add_working_days(now(), 3),
                "Self-learning: verified against the college's own page; goes live only if the "
                "evaluation gate passes. Roll back here at any time.",
            ),
        )
        p = cur.fetchone()
        cur.execute(
            "update learn.lessons set proposal_id = %s where id::text = %s",
            (p["id"], lesson_id),
        )
    event("gate", tenant=tenant, lesson=lesson_id, proposal=p["id"], kind=p["kind"])
    try:
        result = gate.gate_and_deploy(
            settings,
            embedder,
            assistant,
            tenant,
            str(p["id"]),
            None,
            settings.eval_max_cases,
        )
    except Exception as exc:  # noqa: BLE001 - recorded on the proposal by gate_and_deploy
        with tx(settings.database_url, tenant) as cur:
            _set(cur, lesson_id, "rejected", f"the gate could not run: {exc}"[:300])
        return {"status": "rejected", "error": str(exc)[:200]}
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "insert into learn.lesson_metrics (tenant_id, lesson_id, phase, metrics)"
            " values (%s, %s, 'gate', %s)",
            (
                tenant,
                lesson_id,
                Jsonb(
                    {
                        "baseline": result["baseline"],
                        "candidate": result["candidate"],
                        "retrieval": result["gate"].get("retrieval"),
                        "reasons": result["gate"].get("reasons"),
                    }
                ),
            ),
        )
        if result["gate"]["passed"]:
            v = new_version(
                cur, tenant, lesson_id, str(p["id"]), "apply", prop["title"]
            )
            _set(
                cur,
                lesson_id,
                "live",
                "passed the evaluation gate",
                knowledge_version=v,
            )
            cur.execute(
                "update learn.lessons set live_at = now() where id::text = %s",
                (lesson_id,),
            )
            return {"status": "live", "knowledge_version": v, "gate": result["gate"]}
        reasons = "; ".join(result["gate"].get("reasons") or [])[:400]
        lesson = _lesson(cur, lesson_id)
        fallback = lesson["type"] == "retrieval_boost" and lesson["attempt"] == 0
        if fallback:
            # One more try with a narrower lesson: the page's own sentence as a curated answer.
            cur.execute(
                "update learn.lessons set status = 'verifying', reason = %s, proposal_id = null,"
                " updated_at = now() where id::text = %s",
                (
                    f"boost failed the gate ({reasons}); trying a curated answer from the page",
                    lesson_id,
                ),
            )
        else:
            _set(cur, lesson_id, "rejected", f"failed the evaluation gate: {reasons}")
    if fallback:
        verify_and_draft(
            settings, embedder, tenant, lesson_id, model=model, fetch=fetch, attempt=1
        )
        return {"status": "retrying", "gate": result["gate"]}
    return {"status": "rejected", "gate": result["gate"]}


# ------------------------------------------------------------------ inbox decisions


STATUS_FROM_PROPOSAL = {
    "deployed": "live",
    "rolled_back": "rolled_back",
    "rejected": "rejected",
    "failed_gate": "rejected",
    "evaluating": "gated",
}


def sync_decisions(settings: Settings, tenant: str) -> int:
    """Staff decisions in the approval inbox carry the lesson along: approved items go through the
    gate like any other proposal (the inbox's own approve does that), and the outcome is recorded
    on the lesson, with a knowledge version when it goes live or is rolled back."""
    changed = 0
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select l.id::text as lesson_id, l.status as lesson_status, p.id::text as proposal_id,"
            " p.status, p.note, p.title from learn.lessons l join learn.proposals p on p.id = l.proposal_id"
            # A lesson the worker is gating itself is left to gate_lesson.
            " where l.status in ('pending_approval', 'live')"
            " or (l.status = 'gated' and p.decided_by is distinct from 'self-learning')"
        )
        for r in cur.fetchall():
            new = STATUS_FROM_PROPOSAL.get(r["status"])
            if r["status"] == "approved_manual":
                new = None  # a prompt change is made by hand; the lesson keeps waiting
            if not new or new == r["lesson_status"]:
                continue
            if r["lesson_status"] == "gated" and new == "gated":
                continue
            if new == "gated" and r["lesson_status"] == "pending_approval":
                _set(
                    cur,
                    r["lesson_id"],
                    "gated",
                    "approved by staff; in the evaluation gate",
                )
            elif new == "live":
                v = new_version(
                    cur, tenant, r["lesson_id"], r["proposal_id"], "apply", r["title"]
                )
                _set(
                    cur,
                    r["lesson_id"],
                    "live",
                    "passed the evaluation gate",
                    knowledge_version=v,
                )
                cur.execute(
                    "update learn.lessons set live_at = now() where id::text = %s",
                    (r["lesson_id"],),
                )
            elif new == "rolled_back":
                v = new_version(
                    cur,
                    tenant,
                    r["lesson_id"],
                    r["proposal_id"],
                    "rollback",
                    "rolled back by staff",
                )
                _set(
                    cur,
                    r["lesson_id"],
                    "rolled_back",
                    "rolled back by staff",
                    knowledge_version=v,
                )
                cur.execute(
                    "update learn.lessons set rolled_back_at = now() where id::text = %s",
                    (r["lesson_id"],),
                )
            elif r["lesson_status"] != "live":
                _set(
                    cur,
                    r["lesson_id"],
                    new,
                    f"inbox: {r['status']}. {r['note'] or ''}".strip(),
                )
            changed += 1
    return changed
