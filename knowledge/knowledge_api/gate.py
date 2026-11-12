"""Evaluation Agent and gate (PRD 8.4, 20): every approved change is applied to a sandbox copy of
the tenant first, the tenant's regression set (plus the proposal's new cases) is answered by
Module B headlessly before and after the change, and the change reaches the real tenant only if
no metric drops by more than the tolerance and no previously passing critical case fails.
Deployed changes record what they did, so each can be rolled back.

Grading is deterministic here (expected citations, expected facts, forbidden citations, refusal,
routing). The PRD's DeepSeek-distill judge needs the lab GPUs; it can be added as a second grader.
"""

from __future__ import annotations

import csv
import re
import uuid
from datetime import date
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from . import curated, documents, search
from .assistant_client import Assistant, AssistantError
from .config import Settings
from .experience_client import notify
from .db import Jsonb, tx
from .embedder import Embedder, make_embedder

# groundedness: share of factual claims in the answers that Module B's grounding review finds
# supported by the cited source text (PRD 20: ≥ 95%). forbidden_free: no expired or excluded
# source cited. (Until Day 8 "groundedness" meant forbidden_free, which overstated it.)
METRICS = ("accuracy", "refusal", "groundedness", "forbidden_free", "routing")
MANUAL_KINDS = {"prompt"}


# ------------------------------------------------------------------ cases


def load_cases(settings: Settings, tenant: str, pack: Path) -> int:
    path = pack / "eval" / "questions.csv"
    if not path.exists():
        return 0
    split = lambda v: [x for x in (v or "").split("|") if x]  # noqa: E731
    n = 0
    with tx(settings.database_url, tenant) as cur:
        for r in csv.DictReader(path.open(encoding="utf-8-sig")):
            cur.execute(
                "insert into learn.eval_cases (tenant_id, ext_id, question, language, kind, expected_refs,"
                " forbidden_refs, expected_facts, expected_role, critical) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " on conflict (tenant_id, ext_id) do update set question = excluded.question,"
                " kind = excluded.kind, expected_refs = excluded.expected_refs,"
                " forbidden_refs = excluded.forbidden_refs, expected_facts = excluded.expected_facts,"
                " expected_role = excluded.expected_role, critical = excluded.critical",
                (
                    tenant,
                    r["id"],
                    r["question"],
                    r.get("language") or "en",
                    r["kind"],
                    split(r["expected_refs"]),
                    split(r["forbidden_refs"]),
                    split(r.get("expected_facts")),
                    r.get("expected_role") or None,
                    r.get("critical") == "1",
                ),
            )
            n += 1
    return n


def _cases(
    settings: Settings,
    tenant: str,
    extra: list[dict] | None = None,
    limit: int | None = None,
) -> list[dict]:
    with tx(settings.database_url, tenant) as cur:
        cur.execute("select * from learn.eval_cases where active order by ext_id")
        rows = cur.fetchall()
    if limit:
        crit = [r for r in rows if r["critical"]]
        rows = (
            crit + [r for r in rows if not r["critical"]][: max(0, limit - len(crit))]
        )
    return rows + (extra or [])


# ------------------------------------------------------------------ grading


def _norm(text: str) -> str:
    return re.sub(r"(?<=\d),(?=\d)", "", text).lower()


def _refs(done: dict, qa_refs: dict[str, str]) -> set[str]:
    out = set()
    for s in done.get("sources", []):
        if s["source_type"] == "catalogue":
            out.add(f"catalogue:{s.get('ref_id')}")
        elif s["source_type"] == "curated_qa":
            out.add(f"qa:{qa_refs.get(s.get('ref_id'), s.get('ref_id'))}")
        else:
            out.add(f"doc:{s['title']}")
    return out


def _ref_match(expected: str, got: set[str]) -> bool:
    """'catalogue:*' accepts any catalogue citation (a question that names no program)."""
    if expected.endswith(":*"):
        return any(g.startswith(expected[:-1]) for g in got)
    return expected in got


def grade(case: dict, reply: dict, qa_refs: dict[str, str]) -> dict:
    done = reply.get("done") or {}
    outcome = done.get("outcome", "error")
    got = _refs(done, qa_refs)
    forbidden_cited = bool(set(case["forbidden_refs"]) & got)
    answered = outcome in ("answered", "partial")
    kind = case["kind"]
    if kind == "out_of_scope":
        passed, category = (not answered) and not forbidden_cited, "refusal"
    elif kind == "sensitive":
        passed, category = outcome == "handoff", "refusal"
    elif kind in ("proposal_forbid", "must_not_cite"):
        # After expiring a source, "I don't know" is acceptable; citing the old source is not.
        passed, category = not forbidden_cited, "forbid"
    else:
        expected = set(case["expected_refs"])
        ref_ok = not expected or any(_ref_match(e, got) for e in expected)
        facts_ok = all(
            _norm(f) in _norm(reply.get("text", "")) for f in case["expected_facts"]
        )
        passed, category = (
            answered and ref_ok and facts_ok and not forbidden_cited,
            "accuracy",
        )
    roles = {r["role"] for r in (done.get("route") or {}).get("routes", [])}
    role_ok = (
        None
        if not case.get("expected_role") or kind in ("out_of_scope",)
        else case["expected_role"] in roles
    )
    return {
        "id": case["ext_id"],
        "category": category,
        "passed": passed,
        "critical": case["critical"],
        "forbidden_cited": forbidden_cited,
        "role_ok": role_ok,
        "outcome": outcome,
        "cited": sorted(got),
        # Long enough for the judge to grade the whole answer (PRD answers are a few sentences).
        "answer": reply.get("text", "")[:1500],
        "message_id": done.get("message_id") if answered else None,
    }


def ground(
    assistant: Assistant,
    target: str,
    pairs: list[tuple[dict, str]],
    cache: dict | None = None,
) -> str | None:
    """Fill each answered result's claim counts from Module B's grounding review. `cache` maps an
    answer (text and citations) to its review, so a gate's candidate run only pays for answers
    that changed since the baseline. Returns an error message if the review was unavailable."""
    cache = {} if cache is None else cache
    todo = {}
    for r, text in pairs:
        key = (text, tuple(r["cited"]))
        if key in cache:
            r.update(cache[key])
        elif r.get("message_id"):
            todo[r["message_id"]] = (r, key)
    if todo:
        try:
            reviews = assistant.review(target, list(todo))
        except AssistantError as exc:
            return str(exc)
        for mid, (r, key) in todo.items():
            rv = reviews.get(mid)
            if rv is None:
                continue
            found = {
                "claims": rv["claims"],
                "supported_claims": rv["supported"],
                "unsupported_claims": rv["unsupported"],
            }
            r.update(found)
            cache[key] = found
    return None


def scores(results: list[dict]) -> dict:
    def rate(xs):
        return round(sum(xs) / len(xs), 4) if xs else None

    return {
        "accuracy": rate([r["passed"] for r in results if r["category"] == "accuracy"]),
        "refusal": rate([r["passed"] for r in results if r["category"] == "refusal"]),
        "groundedness": (
            round(
                sum(r.get("supported_claims", 0) for r in results)
                / sum(r.get("claims", 0) for r in results),
                4,
            )
            if sum(r.get("claims", 0) for r in results)
            else None
        ),
        "claims": sum(r.get("claims", 0) for r in results),
        "forbidden_free": rate([not r["forbidden_cited"] for r in results]),
        "routing": rate([r["role_ok"] for r in results if r["role_ok"] is not None]),
        "cases": len(results),
        "passed": sum(r["passed"] for r in results),
    }


def _p(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return round(values[min(len(values) - 1, int(q * len(values)))], 3)


def latency_summary(results: list[dict]) -> dict:
    """Seconds per answer as Module A saw them (whole answer, and first released sentence)."""
    total = [r["latency_s"] for r in results if r.get("latency_s") is not None]
    first = [r["first_token_s"] for r in results if r.get("first_token_s") is not None]
    if not total:
        return {}
    return {
        "latency_p50_s": _p(total, 0.5),
        "latency_p95_s": _p(total, 0.95),
        "first_sentence_p50_s": _p(first, 0.5),
        "first_sentence_p95_s": _p(first, 0.95),
    }


def real_drop(b: float, nb: int, c: float, nc: int, z_crit: float = 1.645) -> bool:
    """Is the fall from share b (of nb claims) to c (of nc claims) more than sampling noise?
    One-sided two-proportion z-test at 95%. Groundedness is judged per claim by a model, on a
    few dozen claims: one claim moves it by about 3 points, and repeat runs of an unchanged
    college differed by 9, so a plain tolerance would block good changes at random."""
    if nb == 0 or nc == 0:
        return False
    pooled = (b * nb + c * nc) / (nb + nc)
    se = (pooled * (1 - pooled) * (1 / nb + 1 / nc)) ** 0.5
    if se == 0:
        return c < b
    return (b - c) / se > z_crit


def compare(base: dict, cand: dict, tolerance_points: float, new_ids: set[str]) -> dict:
    """The gate (8.4): no metric down more than the tolerance, no previously passing critical case
    failing, and the proposal's own new cases passing. Groundedness must also fall by more than
    sampling noise to block; claims newly found unsupported are shown to the approver as warnings."""
    reasons, warnings = [], []
    for m in METRICS:
        b, c = base["scores"].get(m), cand["scores"].get(m)
        if b is not None and c is None:
            # A gate that silently skips a metric is no gate (e.g. the review was down).
            reasons.append(f"{m} could not be measured for the change")
        elif b is not None and c is not None and (b - c) * 100 > tolerance_points:
            if m == "groundedness" and not real_drop(
                b, base["scores"].get("claims", 0), c, cand["scores"].get("claims", 0)
            ):
                warnings.append(
                    f"groundedness {b:.1%} → {c:.1%}, within the review's noise on "
                    f"{cand['scores'].get('claims', 0)} claims"
                )
                continue
            reasons.append(f"{m} fell from {b:.1%} to {c:.1%}")
    before = {r["id"]: r for r in base["results"]}
    for r in cand["results"]:
        prev = before.get(r["id"])
        if r["critical"] and prev and prev["passed"] and not r["passed"]:
            reasons.append(f"critical case {r['id']} no longer passes")
        if r["id"] in new_ids and not r["passed"]:
            reasons.append(f"new case {r['id']} does not pass")
        was = set((prev or {}).get("unsupported_claims") or [])
        for claim in r.get("unsupported_claims") or []:
            if claim not in was:
                warnings.append(f"{r['id']}: not shown to be supported: {claim[:200]}")
    return {"passed": not reasons, "reasons": reasons, "warnings": warnings[:10]}


def _qa_refs(settings: Settings, target: str) -> dict[str, str]:
    with tx(settings.database_url, target) as cur:
        cur.execute(
            "select id::text, external_ref from kb.curated_qa where external_ref is not null"
        )
        return {r["id"]: r["external_ref"] for r in cur.fetchall()}


def retry_regressions(
    settings: Settings,
    assistant: Assistant,
    tenant: str,
    target: str,
    base: dict,
    cand: dict,
    extra: list[dict],
    review_cache: dict | None = None,
) -> int:
    """Flake guard: a small model at temperature 0 still varies a little between calls. A case that
    passed in the baseline (or a new case) and fails in the candidate is asked once more; only a
    repeated failure counts. Retries are recorded on the result."""
    before = {r["id"]: r["passed"] for r in base["results"]}
    cases = {c["ext_id"]: c for c in _cases(settings, tenant, extra)}
    qa_refs = _qa_refs(settings, target)
    changed = 0
    for i, r in enumerate(cand["results"]):
        if (
            r["passed"]
            or not (before.get(r["id"]) or r["id"] not in before)
            or r["id"] not in cases
        ):
            continue
        try:
            reply = assistant.ask(
                target,
                f"eval-retry-{uuid.uuid4().hex[:8]}-{r['id']}",
                cases[r["id"]]["question"],
            )
        except AssistantError:
            continue
        again = grade(cases[r["id"]], reply, qa_refs)
        again["retried"] = True
        ground(assistant, target, [(again, reply.get("text", ""))], review_cache)
        cand["results"][i] = again
        changed += again["passed"]
    cand["scores"] = scores(cand["results"])
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "update learn.eval_runs set scores=%s, results=%s where id::text=%s",
            (Jsonb(cand["scores"]), Jsonb(cand["results"]), cand["id"]),
        )
    return changed


def new_run(settings: Settings, tenant: str, target: str, label: str, trigger: str):
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "insert into learn.eval_runs (tenant_id, trigger, label, target_tenant) values (%s,%s,%s,%s) returning id",
            (tenant, trigger, label, target),
        )
        return cur.fetchone()["id"]


def run_suite(
    settings: Settings,
    assistant: Assistant,
    tenant: str,
    target: str,
    label: str,
    trigger: str,
    extra: list[dict] | None = None,
    limit: int | None = None,
    run_id=None,
    review_cache: dict | None = None,
    channel: str = "web_chat",
    review: bool = True,
) -> dict:
    """Answer every case through Module B for `target` (the tenant or its sandbox), grade it, and
    have Module B's grounding review check the claims of every answer.
    Pass `run_id` from new_run() to let a caller follow a run started in the background.
    `channel` web_voice asks as the voice channel (F2.10); `review=False` skips the grounding review."""
    cases = _cases(settings, tenant, extra, limit)
    run_id = run_id or new_run(settings, tenant, target, label, trigger)
    try:
        qa_refs = _qa_refs(settings, target)
        results, pairs = [], []
        for case in cases:
            try:
                if channel == "web_chat":
                    reply = assistant.ask(
                        target,
                        f"eval-{run_id.hex[:8]}-{case['ext_id']}",
                        case["question"],
                    )
                else:
                    reply = assistant.ask(
                        target,
                        f"eval-{run_id.hex[:8]}-{case['ext_id']}",
                        case["question"],
                        channel=channel,
                    )
            except AssistantError as exc:
                reply = {"text": str(exc), "done": {"outcome": "error"}}
            results.append(grade(case, reply, qa_refs))
            if "total_s" in reply:
                results[-1]["latency_s"] = round(reply["total_s"], 3)
                results[-1]["first_token_s"] = (
                    round(reply["first_token_s"], 3)
                    if "first_token_s" in reply
                    else None
                )
            results[-1]["model"] = (
                (reply.get("done") or {}).get("versions") or {}
            ).get("model")
            pairs.append((results[-1], reply.get("text", "")))
        review_error = (
            ground(assistant, target, pairs, review_cache) if review else None
        )
        sc = scores(results)
        sc.update(latency_summary(results))
        if review_error:
            sc["review_error"] = review_error
        with tx(settings.database_url, tenant) as cur:
            cur.execute(
                "update learn.eval_runs set status='done', scores=%s, results=%s, finished_at=now() where id=%s",
                (Jsonb(sc), Jsonb(results), run_id),
            )
        return {"id": str(run_id), "scores": sc, "results": results}
    except Exception as exc:
        with tx(settings.database_url, tenant) as cur:
            cur.execute(
                "update learn.eval_runs set status='error', error=%s, finished_at=now() where id=%s",
                (str(exc)[:500], run_id),
            )
        raise


# ------------------------------------------------------------------ sandbox copy of a tenant

_CLONE = [
    # (table, columns copied, id remapping of foreign keys)
    (
        "kb.programs",
        "code, name, aliases, department, level, duration_months, intake, eligibility, contact,"
        " academic_year, status",
        {},
    ),
    (
        "kb.program_fees",
        "program_id, category, component, amount, currency, period, valid_from, valid_to",
        {"program_id": "kb.programs"},
    ),
    (
        "kb.program_dates",
        "program_id, label, on_date, academic_year",
        {"program_id": "kb.programs"},
    ),
    (
        "kb.documents",
        "source_id, title, mime, url, academic_year, department, owner_role, owner, status,"
        " status_reason, valid_from, valid_to, version, previous_id, checksum, content, diff_summary,"
        " chunk_count, approved_by, approved_at",
        {"previous_id": "kb.documents", "source_id": None},
    ),
    (
        "kb.chunks",
        "document_id, ord, content, heading_path, page, embedding, embedding_ml",
        {"document_id": "kb.documents"},
    ),
    (
        "kb.curated_qa",
        "question, variants, answer, category, owner_role, department, academic_year, valid_to,"
        " status, status_reason, version, last_used_at, embedding, embedding_ml,"
        " source_correction_id, external_ref",
        {},
    ),
]


def clone_tenant(settings: Settings, tenant: str) -> tuple[str, dict]:
    """Copy a tenant's knowledge to a fresh sandbox tenant id. Returns (sandbox id, id map) so a
    change can be applied to the copy using the original ids."""
    sandbox = str(uuid.uuid4())
    idmap: dict[str, str] = {}
    with psycopg.connect(
        settings.admin_database_url, row_factory=dict_row
    ) as conn, conn.cursor() as cur:
        cur.execute(
            "insert into kb.sandboxes (sandbox_id, tenant_id) values (%s, %s)",
            (sandbox, tenant),
        )
        for table, cols, fks in _CLONE:
            names = [c.strip() for c in cols.split(",")]
            cur.execute(
                f"select id::text, {cols} from {table} where tenant_id = %s order by created_at",
                (tenant,),
            )
            rows = cur.fetchall()
            for row in rows:
                new_id = str(uuid.uuid4())
                idmap[row["id"]] = new_id
            for row in rows:
                values = []
                for c in names:
                    v = row[c]
                    if c in fks:
                        v = idmap.get(str(v)) if (v is not None and fks[c]) else None
                    values.append(v)
                cur.execute(
                    f"insert into {table} (id, tenant_id, {cols}) values (%s, %s, {', '.join(['%s'] * len(names))})",
                    (idmap[row["id"]], sandbox, *values),
                )
        conn.commit()
    return sandbox, idmap


def drop_sandbox(settings: Settings, sandbox: str) -> None:
    with psycopg.connect(settings.admin_database_url) as conn:
        for table in (
            "kb.chunks",
            "kb.curated_qa_history",
            "kb.curated_qa",
            "kb.documents",
            "kb.program_dates",
            "kb.program_fees",
            "kb.programs",
        ):
            conn.execute(f"delete from {table} where tenant_id = %s", (sandbox,))
        conn.execute("delete from kb.sandboxes where sandbox_id = %s", (sandbox,))
    search.invalidate(sandbox)


# ------------------------------------------------------------------ applying changes


def current_year(cur) -> tuple[str, date]:
    """Academic year and validity for new curated answers: the tenant's latest catalogue or
    curated-answer year (PRD 8.3: academic_year and valid_to are mandatory)."""
    cur.execute(
        "select academic_year, max(valid_to) as valid_to from kb.curated_qa where status = 'approved'"
        " group by academic_year order by academic_year desc limit 1"
    )
    row = cur.fetchone()
    if row:
        return row["academic_year"], row["valid_to"]
    cur.execute("select max(academic_year) as y from kb.programs")
    y = (cur.fetchone() or {}).get(
        "y"
    ) or f"{date.today().year}-{(date.today().year + 1) % 100:02d}"
    return y, date(int(y[:4]) + 1, 7, 31)


def new_cases(p: dict) -> list[dict]:
    """Eval cases a proposal adds to the regression set (8.3)."""
    d, ref = p["diff"], f"proposal-{str(p['id'])[:8]}"
    examples = list(
        dict.fromkeys(e["question"] for e in p["examples"] if e.get("question"))
    ) or [d.get("question")]
    base = {
        "language": "en",
        "expected_facts": [],
        "critical": False,
        "expected_role": None,
        "source": "proposal",
    }
    if p["kind"] == "curated_answer":
        return [
            {
                **base,
                "ext_id": f"{ref}-{i}",
                "question": q,
                "kind": "proposal",
                "expected_refs": [f"qa:{ref}"],
                "forbidden_refs": [],
            }
            for i, q in enumerate(examples[:2])
            if q
        ]
    if p["kind"] == "document_action" and d.get("source_type") == "document":
        return [
            {
                **base,
                "ext_id": f"{ref}-{i}",
                "question": q,
                "kind": "proposal_forbid",
                "expected_refs": [],
                "forbidden_refs": [f"doc:{d['title']}"],
            }
            for i, q in enumerate(examples[:2])
            if q
        ]
    if p["kind"] == "sensitive_topic":
        return [
            {
                **base,
                "ext_id": f"{ref}-0",
                "question": examples[0],
                "kind": "sensitive",
                "expected_refs": [],
                "forbidden_refs": [],
                "critical": True,
            }
        ]
    return []


SAME_QUESTION = (
    0.92  # cosine between question vectors: the same question, reworded at most
)


def _same_question(
    settings: Settings, embedder: Embedder, target: str, question: str
) -> str | None:
    """An approved or suspended curated answer to (nearly) the same question, if there is one."""
    import numpy as np

    with tx(settings.database_url, target) as cur:
        cur.execute(
            "select id::text, question from kb.curated_qa where status in ('approved', 'suspended')"
        )
        rows = cur.fetchall()
    if not rows:
        return None
    vecs = np.asarray(
        embedder.documents([question] + [r["question"] for r in rows]), dtype=np.float32
    )
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True).clip(min=1e-9)
    sims = vecs[1:] @ vecs[0]
    best = int(np.argmax(sims))
    return rows[best]["id"] if sims[best] >= SAME_QUESTION else None


def apply_change(
    settings: Settings,
    embedder: Embedder,
    assistant: Assistant,
    target: str,
    p: dict,
    note: str | None,
    idmap: dict[str, str] | None = None,
) -> dict:
    """Apply proposal p to `target` (real tenant, or sandbox with idmap). Returns what was done."""
    d, kind = p["diff"], p["kind"]
    if kind == "curated_answer":
        answer = (
            (note or "").strip()
            if note and len(note.strip()) >= 15
            else d.get("answer")
        )
        if not answer:
            raise ValueError(
                "This gap needs an answer: approve it with the answer text in the note"
            )
        existing = _same_question(settings, embedder, target, d["question"])
        with tx(settings.database_url, target) as cur:
            if existing:
                # A correction to a question that already has a curated answer is a new version of
                # that answer (PRD F4.4, 8.4), never a second answer competing with it.
                before = curated.get(cur, existing)
                if not before[
                    "external_ref"
                ]:  # same reference in the sandbox and the real tenant
                    cur.execute(
                        "update kb.curated_qa set external_ref = %s where id = %s",
                        (f"proposal-{str(p['id'])[:8]}", existing),
                    )
                curated.update(
                    cur,
                    embedder,
                    existing,
                    {"answer": answer},
                    actor=p.get("decided_by") or "improvement",
                )
                row = curated.set_status(
                    cur,
                    existing,
                    "approved",
                    p.get("decided_by"),
                    f"improvement {p['id']}",
                )
                done = {
                    "curated_qa_id": str(row["id"]),
                    "updated_from_version": before["version"],
                    "previous_status": before["status"],
                }
            else:
                year, valid_to = current_year(cur)
                row = curated.create(
                    cur,
                    embedder,
                    target,
                    {
                        "question": d["question"],
                        "answer": answer,
                        "academic_year": year,
                        "valid_to": valid_to,
                        "department": d.get("department") or p.get("department"),
                        "owner_role": "faculty"
                        if p.get("department")
                        else "admission_officer",
                        "external_ref": f"proposal-{str(p['id'])[:8]}",
                        "source_correction_id": f"proposal:{p['id']}",
                    },
                    actor=p.get("decided_by") or "improvement",
                    status="approved",
                )
                done = {"curated_qa_id": str(row["id"])}
        search.invalidate(target)
        return {**done, "answer": answer, "external_ref": row["external_ref"]}
    if kind in ("document_action", "curated_conflict"):
        ref = d.get("ref_id") or d.get("qa_id")
        ref = (idmap or {}).get(ref, ref)
        with tx(settings.database_url, target) as cur:
            if d.get("source_type") == "curated_qa" or kind == "curated_conflict":
                curated.set_status(
                    cur, ref, "expired", p.get("decided_by"), f"improvement {p['id']}"
                )
                done = {"expired_curated_qa": ref}
            elif d.get("source_type") == "document":
                documents.act(
                    cur, ref, "expire", p.get("decided_by"), f"improvement {p['id']}"
                )
                done = {"expired_document": ref}
            else:
                raise ValueError(
                    "Catalogue values are corrected by re-importing the catalogue"
                )
        search.invalidate(target)
        return done
    if kind == "sensitive_topic":
        cfg = assistant.get_settings(target)
        topics = list(dict.fromkeys([*cfg.get("sensitive_topics", []), d["topic"]]))
        assistant.put_settings(target, {**cfg, "sensitive_topics": topics})
        return {"sensitive_topic_added": d["topic"]}
    raise ValueError(f"{kind} proposals are applied by hand")


def rollback_change(
    settings: Settings,
    assistant: Assistant,
    tenant: str,
    done: dict,
    by: str | None,
    embedder: Embedder | None = None,
) -> None:
    with tx(settings.database_url, tenant) as cur:
        if "updated_from_version" in done:
            curated.rollback(
                cur,
                embedder or make_embedder(settings),
                done["curated_qa_id"],
                done["updated_from_version"],
                by,
            )
        elif "curated_qa_id" in done:
            curated.set_status(cur, done["curated_qa_id"], "expired", by, "rolled back")
        if "expired_curated_qa" in done:
            curated.set_status(
                cur, done["expired_curated_qa"], "approved", by, "rolled back"
            )
        if "expired_document" in done:
            documents.act(cur, done["expired_document"], "approve", by)
    if "sensitive_topic_added" in done:
        cfg = assistant.get_settings(tenant)
        cfg["sensitive_topics"] = [
            t
            for t in cfg.get("sensitive_topics", [])
            if t != done["sensitive_topic_added"]
        ]
        assistant.put_settings(tenant, cfg)
    search.invalidate(tenant)


def gate_and_deploy(
    settings: Settings,
    embedder: Embedder,
    assistant: Assistant,
    tenant: str,
    proposal_id: str,
    note: str | None,
    limit: int | None = None,
) -> dict:
    """Background job after approval: sandbox -> baseline -> apply -> candidate -> compare -> deploy."""
    with tx(settings.database_url, tenant) as cur:
        cur.execute("select * from learn.proposals where id::text = %s", (proposal_id,))
        p = cur.fetchone()
    sandbox, idmap = clone_tenant(settings, tenant)
    try:
        # Marked as a sandbox so Module B lets us delete it again afterwards.
        assistant.put_settings(
            sandbox, {**assistant.get_settings(tenant), "sandbox": True}
        )
        trigger = f"proposal:{proposal_id}"
        extra = new_cases(p)
        reviews: dict = {}  # answers unchanged by the proposal are not reviewed twice
        base = run_suite(
            settings,
            assistant,
            tenant,
            sandbox,
            "baseline",
            trigger,
            None,
            limit,
            review_cache=reviews,
        )
        applied = apply_change(settings, embedder, assistant, sandbox, p, note, idmap)
        if applied.get(
            "external_ref"
        ):  # updated an existing answer: its own ref is what to expect
            for c in extra:
                c["expected_refs"] = (
                    [f"qa:{applied['external_ref']}"] if c["expected_refs"] else []
                )
        cand = run_suite(
            settings,
            assistant,
            tenant,
            sandbox,
            "candidate",
            trigger,
            extra,
            limit,
            review_cache=reviews,
        )
        retry_regressions(
            settings, assistant, tenant, sandbox, base, cand, extra, reviews
        )
        verdict = compare(
            base, cand, settings.gate_tolerance_points, {c["ext_id"] for c in extra}
        )
        deployed = (
            apply_change(settings, embedder, assistant, tenant, p, note)
            if verdict["passed"]
            else None
        )
        with tx(settings.database_url, tenant) as cur:
            cur.execute(
                "update learn.eval_runs set passed=%s, gate=%s where id::text = %s",
                (
                    verdict["passed"],
                    Jsonb(
                        {
                            **verdict,
                            "baseline": base["scores"],
                            "candidate": cand["scores"],
                        }
                    ),
                    cand["id"],
                ),
            )
            if verdict["passed"]:
                for c in extra:  # the new cases join the regression set
                    cur.execute(
                        "insert into learn.eval_cases (tenant_id, ext_id, question, language, kind, expected_refs,"
                        " forbidden_refs, expected_facts, critical, source, proposal_id) values"
                        " (%s,%s,%s,%s,%s,%s,%s,%s,%s,'proposal',%s) on conflict (tenant_id, ext_id) do nothing",
                        (
                            tenant,
                            c["ext_id"],
                            c["question"],
                            c["language"],
                            c["kind"],
                            c["expected_refs"],
                            c["forbidden_refs"],
                            c["expected_facts"],
                            c["critical"],
                            proposal_id,
                        ),
                    )
            cur.execute(
                "update learn.proposals set status=%s, eval_run_id=%s, deployed_ref=%s, updated_at=now()"
                " where id::text=%s",
                (
                    "deployed" if verdict["passed"] else "failed_gate",
                    cand["id"],
                    Jsonb(deployed),
                    proposal_id,
                ),
            )
        if verdict["passed"]:
            notify(
                settings,
                tenant,
                "improvement.deployed",
                {
                    "proposal_id": proposal_id,
                    "kind": p["kind"],
                    "title": p["title"],
                    "scores": cand["scores"],
                },
            )
        return {
            "gate": verdict,
            "baseline": base["scores"],
            "candidate": cand["scores"],
            "deployed": deployed,
        }
    except Exception as exc:
        with tx(settings.database_url, tenant) as cur:
            cur.execute(
                "update learn.proposals set status='failed_gate', note=%s, updated_at=now() where id::text=%s",
                (f"Evaluation could not run: {exc}"[:500], proposal_id),
            )
        raise
    finally:
        drop_sandbox(settings, sandbox)
        try:
            assistant.delete_sandbox(sandbox)
        except Exception:  # noqa: BLE001 - leftovers are harmless; never mask the gate result
            pass


STALE_MINUTES = (
    30  # no gate run on this hardware takes this long; anything older was interrupted
)


def recover_interrupted(settings: Settings) -> dict:
    """After a restart: gate runs left 'evaluating' or 'running' are marked interrupted (so they
    can be approved again) and orphaned sandbox copies are removed. Safe to run any time."""
    import psycopg

    with psycopg.connect(settings.admin_database_url, autocommit=True) as conn:
        props = conn.execute(
            "update learn.proposals set status = 'failed_gate', note = 'Evaluation was interrupted (service"
            " restart); approve again to re-run it', updated_at = now() where status = 'evaluating'"
            " and updated_at < now() - make_interval(mins => %s) returning id",
            (STALE_MINUTES,),
        ).rowcount
        runs = conn.execute(
            "update learn.eval_runs set status = 'error', error = 'interrupted', finished_at = now()"
            " where status = 'running' and started_at < now() - make_interval(mins => %s)",
            (STALE_MINUTES,),
        ).rowcount
        orphans = [
            r[0]
            for r in conn.execute(
                "select sandbox_id from kb.sandboxes where created_at < now() - make_interval(mins => %s)",
                (STALE_MINUTES,),
            )
        ]
    for sandbox in orphans:
        drop_sandbox(settings, str(sandbox))
    return {"proposals": props, "runs": runs, "sandboxes": len(orphans)}
