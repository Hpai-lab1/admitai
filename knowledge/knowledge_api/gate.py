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
import hashlib
import json
import logging
import re
import time
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

log = logging.getLogger("admitai.gate")

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
        if (
            not ref_ok
            and facts_ok
            and case["expected_facts"]
            and any(e.startswith("catalogue:") for e in expected)
            and any(g.startswith("qa:") for g in got)
        ):
            # A curated answer that states the catalogue's own figures is as good as citing the
            # catalogue (a correction that agrees with it must not fail the catalogue's cases).
            ref_ok = True
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
    if reasons:
        # Name the questions behind a falling metric, so the approver sees what broke.
        now_failing = [
            r["id"]
            for r in cand["results"]
            if not r["critical"]
            and r["id"] not in new_ids
            and (before.get(r["id"]) or {}).get("passed")
            and not r["passed"]
        ]
        if now_failing:
            reasons.append(
                "passed before, fail with the change: " + ", ".join(now_failing[:10])
            )
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
    follow=None,
) -> int:
    """Flake guard: a small model at temperature 0 still varies a little between calls. A case that
    passed in the baseline (or a new case) and fails in the candidate is asked once more; only a
    repeated failure counts. Retries are recorded on the result."""
    before = {r["id"]: r["passed"] for r in base["results"]}
    loaded = _cases(settings, tenant, extra)
    cases = {c["ext_id"]: c for c in (follow(loaded) if follow else loaded)}
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


# ------------------------------------------------------------------ sharing the model
# The gate asks Module B the same shared model that answers visitors. Two suites at once would
# both crawl and slow every visitor, so suites run one at a time (a database lock, so this holds
# across processes), and between questions a suite waits while visitors are being answered.
SUITE_LOCK = 0x41444D47  # any constant; the same for every process on this database


def _suite_lock(settings: Settings) -> psycopg.Connection:
    """Wait for the model to be free of other suites. Closing the connection releases the lock."""
    conn = psycopg.connect(settings.database_url, autocommit=True)
    try:
        conn.execute(f"set lock_timeout = '{int(settings.suite_queue_max_s)}s'")
        conn.execute("select pg_advisory_lock(%s)", (SUITE_LOCK,))
    except psycopg.errors.LockNotAvailable:
        conn.close()
        raise RuntimeError(
            "Another test run has been using the model for too long; try again later"
        ) from None
    except BaseException:
        conn.close()
        raise
    return conn


def yield_to_visitors(
    settings: Settings,
    assistant: Assistant,
    tenant: str,
    sleep=time.sleep,
    clock=time.monotonic,
) -> float:
    """Seconds spent waiting, at most settings.gate_yield_max_s, while Module B is answering
    visitors. If Module B cannot say, the suite carries on rather than stalling."""
    load = getattr(assistant, "load", None)
    if load is None or settings.gate_yield_max_s <= 0:
        return 0.0
    start = clock()
    while clock() - start < settings.gate_yield_max_s:
        try:
            busy = int(load(tenant).get("live_turns", 0))
        except Exception:  # noqa: BLE001 - an old Module B, or it is down
            break
        if busy <= 0:
            break
        sleep(1.0)
    return clock() - start


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
    follow=None,
    only: set[str] | None = None,
    parallel: int = 1,
) -> dict:
    """Answer every case through Module B for `target` (the tenant or its sandbox), grade it, and
    have Module B's grounding review check the claims of every answer.
    Pass `run_id` from new_run() to let a caller follow a run started in the background.
    `channel` web_voice asks as the voice channel (F2.10); `review=False` skips the grounding review."""
    cases = _cases(settings, tenant, extra, limit)
    if only is not None:  # a quick test: just the questions the change can affect
        cases = [c for c in cases if c["ext_id"] in only]
    if follow:  # e.g. a corrected fee: the candidate expects the new amount
        cases = follow(cases)
    run_id = run_id or new_run(settings, tenant, target, label, trigger)
    lock = None
    try:
        lock = _suite_lock(settings)
        qa_refs = _qa_refs(settings, target)
        waited = 0.0

        def one(case):
            nonlocal waited
            waited += yield_to_visitors(settings, assistant, target)
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
            r = grade(case, reply, qa_refs)
            if "total_s" in reply:
                r["latency_s"] = round(reply["total_s"], 3)
                r["first_token_s"] = (
                    round(reply["first_token_s"], 3)
                    if "first_token_s" in reply
                    else None
                )
            r["model"] = ((reply.get("done") or {}).get("versions") or {}).get("model")
            return r, reply.get("text", "")

        if parallel > 1 and len(cases) > 1:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=parallel) as pool:
                pairs = list(pool.map(one, cases))  # in case order
        else:
            pairs = [one(c) for c in cases]
        results = [r for r, _ in pairs]
        review_error = (
            ground(assistant, target, pairs, review_cache) if review else None
        )
        sc = scores(results)
        sc.update(latency_summary(results))
        sc["waited_for_visitors_s"] = round(waited, 1)
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
    finally:
        if lock is not None:
            lock.close()


# ------------------------------------------------------------------ sandbox copy of a tenant

_CLONE = [
    # (table, columns copied, id remapping of foreign keys)
    (
        "kb.programs",
        "code, name, aliases, department, level, duration_months, intake, eligibility, contact,"
        " academic_year, status, source_url",
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
        settings.worker_database_url, row_factory=dict_row
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
        if settings.self_learning_enabled:
            from .selflearn import apply as lessons

            lessons.copy_to_sandbox(cur, tenant, sandbox, idmap)
        conn.commit()
    return sandbox, idmap


def drop_sandbox(settings: Settings, sandbox: str) -> None:
    with psycopg.connect(settings.worker_database_url) as conn:
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
        if settings.self_learning_enabled:
            from .selflearn import apply as lessons

            lessons.drop_sandbox(conn, sandbox)
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


_FIGURE = re.compile(
    r"(₹|\brs\.?|\binr\b)?\s*(\d[\d,]*\d|\d)(\s*(?:inr|rupees)\b)?", re.IGNORECASE
)


def _figures(text: str) -> list[str]:
    """Amounts and counts of three or more digits in an answer, without years (2027, 2027-28).
    A number written as money ('₹2000', 'Rs. 2000', '2000 INR') is an amount even when it
    looks like a year."""
    out = []
    for m in _FIGURE.finditer(text):
        n = m.group(2).replace(",", "")
        money = bool(m.group(1) or m.group(3))
        year_like = len(n) == 4 and 1990 <= int(n) <= 2099
        if len(n) >= 3 and (money or not year_like) and n not in out:
            out.append(n)
    return out


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
        # The figures in the new answer (fees, durations; not years) are what must come out. With
        # them, citing the catalogue also passes: the answer agrees with the catalogue, which
        # the assistant may rightly prefer.
        facts = _figures(d.get("answer") or "")
        refs = [f"qa:{ref}", "catalogue:*"] if facts else [f"qa:{ref}"]
        # The answer is checked against the question it answers. The proposal's examples are
        # every visitor question grouped with it ("What careers can B.Des. Fashion Design lead
        # to?" next to its eligibility), and an eligibility answer must not be expected there.
        if d.get("question"):
            examples = [d["question"]]
        return [
            {
                **base,
                "ext_id": f"{ref}-{i}",
                "question": q,
                "kind": "proposal",
                "expected_refs": refs,
                "expected_facts": facts,
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
    if p["kind"] in ("retrieval_boost", "search_alias"):
        title = d.get("title") or (d.get("evidence") or {}).get("title")
        qs = list(dict.fromkeys([d.get("question"), *examples]))
        return [
            {
                **base,
                "ext_id": f"{ref}-{i}",
                "question": q,
                "kind": "proposal",
                "expected_refs": [f"doc:{title}"],
                "forbidden_refs": [],
            }
            for i, q in enumerate(qs[:2])
            if q and title
        ]
    if p["kind"] == "catalogue_reextract":
        new = [c["new"] for c in d.get("fee_changes") or []]
        if d.get("intake_change"):
            new.append(str(d["intake_change"]["new"]))
        return (
            [
                {
                    **base,
                    "ext_id": f"{ref}-0",
                    "question": d.get("question"),
                    "kind": "proposal",
                    "expected_refs": ["catalogue:*"],
                    "expected_facts": new,
                    "forbidden_refs": [],
                }
            ]
            if d.get("question") and new
            else []
        )
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


def _corrected_answer(
    settings: Settings, target: str, p: dict, idmap: dict[str, str] | None
) -> str | None:
    """The curated answer this correction corrects: the one the corrected reply cited (recorded on
    the proposal, or for older proposals read from its correction signals), as it exists in
    `target` (a sandbox copy has its own ids). None when it is unknown or no longer in use."""
    qa_id = (p.get("diff") or {}).get("corrects_qa_id")
    if not qa_id and p.get("id") and p.get("tenant_id"):
        from .learning import cited_answer

        with tx(settings.database_url, str(p["tenant_id"])) as cur:
            cur.execute(
                "select sources from learn.signals where proposal_id::text = %s and kind = 'correction'",
                (str(p["id"]),),
            )
            qa_id = cited_answer([dict(r) for r in cur.fetchall()])
    if not qa_id:
        return None
    qa_id = (idmap or {}).get(qa_id, qa_id)
    with tx(settings.database_url, target) as cur:
        cur.execute(
            "select id::text from kb.curated_qa where id::text = %s"
            " and status in ('approved', 'suspended')",
            (qa_id,),
        )
        row = cur.fetchone()
    return row["id"] if row else None


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


def _change_fees(
    settings: Settings, target: str, changes: list[dict], idmap: dict | None
) -> dict:
    """A correction of a fee changes the official fee list (catalogue wins, so a curated answer
    alone would never reach visitors). On the sandbox copy the copied fee is changed."""
    from decimal import Decimal

    from . import catalogue, catalogue_cases, health

    with tx(settings.database_url, target) as cur:
        for c in changes:
            fee = (idmap or {}).get(c["fee_id"], c["fee_id"])
            if catalogue.set_fee(cur, fee, Decimal(c["new"])) is None:
                raise ValueError(f"The {c['component']} fee of {c['program']} is gone")
    if idmap is None:  # the real college: everything that follows the catalogue follows
        search.invalidate(target)
        health.check_catalogue_conflicts(settings, target)
        catalogue_cases.generate(settings, target)
    return {"fee_changes": changes}


def _follow_fees(cases: list[dict], changes: list[dict], names: dict) -> list[dict]:
    """The candidate run expects the corrected fees: a case quoting an old fee for that
    programme expects the new one (the baseline still expects the old)."""
    out = []
    for c in cases:
        facts = list(c.get("expected_facts") or [])
        q = (c.get("question") or "").lower()
        for ch in changes:
            if ch["old"] in facts and any(
                len(n) >= 2 and n.lower() in q for n in names.get(ch["code"], [])
            ):
                facts = [ch["new"] if f == ch["old"] else f for f in facts]
        out.append({**c, "expected_facts": facts})
    return out


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
    from .selflearn import apply as lessons

    if kind in lessons.KINDS:  # self-learning lessons (selflearn/apply.py)
        return lessons.apply(settings, embedder, target, p, idmap)
    if kind == "curated_answer":
        answer = curated.strip_citation_markers(
            (note or "").strip()
            if note and len(note.strip()) >= 15
            else d.get("answer") or ""
        )
        if not answer:
            raise ValueError(
                "This gap needs an answer: approve it with the answer text in the note"
            )
        if d.get("fee_changes"):
            return _change_fees(settings, target, d["fee_changes"], idmap)
        # The answer the corrected reply cited is the one to update; a similar question is only
        # a fallback (a reworded question scores well below SAME_QUESTION).
        existing = _corrected_answer(settings, target, p, idmap) or _same_question(
            settings, embedder, target, d["question"]
        )
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
    if done.get("fee_changes"):
        _change_fees(
            settings,
            tenant,
            [{**c, "new": c["old"], "old": c["new"]} for c in done["fee_changes"]],
            None,
        )
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
    if done.get("lesson_id"):
        from .selflearn import apply as lessons

        lessons.rollback(settings, tenant, done)
    if "sensitive_topic_added" in done:
        cfg = assistant.get_settings(tenant)
        cfg["sensitive_topics"] = [
            t
            for t in cfg.get("sensitive_topics", [])
            if t != done["sensitive_topic_added"]
        ]
        assistant.put_settings(tenant, cfg)
    search.invalidate(tenant)


# ------------------------------------------------------------------ reusing a baseline
# Every approval used to answer the whole test set twice. The "before" run usually repeats the
# last measured state, so a run is stored with the fingerprint of what it measured, and a gate
# whose college has the same fingerprint reuses it. Anything that can change an answer is in the
# fingerprint; a reused baseline is never older than a day; when in doubt, nothing is reused.
BASELINE_MAX_AGE_H = 24
_FP_PARTS = {
    "documents": "select id, status, version, checksum, valid_from, valid_to, academic_year,"
    " department from kb.documents",
    "curated": "select id, question, variants, answer, status, version, valid_to, academic_year,"
    " department from kb.curated_qa",
    "programs": "select id, code, name, aliases, department, level, duration_months, intake,"
    " eligibility, contact, academic_year, status from kb.programs",
    "fees": "select program_id, category, component, amount, currency, period, valid_from,"
    " valid_to from kb.program_fees",
    "dates": "select program_id, label, on_date, academic_year from kb.program_dates",
    "cases": "select ext_id, question, language, kind, expected_refs, forbidden_refs,"
    " expected_facts, critical, active from learn.eval_cases",
}


_FP_LESSONS = {
    "aliases": "select phrase, expansion from learn.lesson_aliases where active",
    "boosts": "select lesson_id, chunk_id, quote from learn.retrieval_boosts where active",
}


def fingerprint(settings: Settings, assistant: Assistant, tenant: str) -> str | None:
    """What the college's answers depend on, as one hash; None when Module B cannot say."""
    try:
        b = {
            "versions": assistant.versions(tenant),
            "settings": assistant.get_settings(tenant),
        }
    except Exception:  # noqa: BLE001 - an old Module B or a fake: never reuse then
        return None
    parts = {}
    fp_parts = dict(_FP_PARTS)
    if settings.self_learning_enabled:  # live lessons change answers too
        fp_parts.update(_FP_LESSONS)
    with tx(settings.database_url, tenant) as cur:
        for name, sql in fp_parts.items():
            cur.execute(
                "select md5(coalesce(string_agg(h, '|' order by h), '')) as h"
                f" from (select md5(t::text) as h from ({sql}) t) x"
            )
            parts[name] = cur.fetchone()["h"]
    blob = json.dumps(
        {"kb": parts, "b": b, "day": date.today().isoformat()},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def _mark(settings: Settings, tenant: str, run_id: str, fp: str | None) -> None:
    if fp:
        with tx(settings.database_url, tenant) as cur:
            cur.execute(
                "update learn.eval_runs set fingerprint = %s where id::text = %s",
                (fp, run_id),
            )


def reusable_baseline(settings: Settings, tenant: str, fp: str | None) -> dict | None:
    """The latest run of this exact state, within a day, covering every active case."""
    if not fp:
        return None
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select id, results from learn.eval_runs where fingerprint = %s and status = 'done'"
            " and results is not null"
            f" and finished_at > now() - interval '{BASELINE_MAX_AGE_H} hours'"
            " order by finished_at desc limit 1",
            (fp,),
        )
        row = cur.fetchone()
    if not row:
        return None
    wanted = {c["ext_id"] for c in _cases(settings, tenant)}
    results = [
        r for r in row["results"] if r.get("id") in wanted
    ]  # result "id" = case ext_id
    if {r.get("id") for r in results} != wanted:
        return None
    sc = scores(results)
    sc.update(latency_summary(results))
    return {"id": str(row["id"]), "scores": sc, "results": results, "reused": True}


def nightly_full_test(settings: Settings, assistant: Assistant, tenant: str) -> dict:
    """The whole test set, once a night (approvals are tested on their own questions only). It
    also becomes the "before" a quick test can reuse. Critical questions that passed last night
    and fail now are reported in the console's bell."""
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select results from learn.eval_runs where label = 'nightly' and status = 'done'"
            " and results is not null order by finished_at desc limit 1"
        )
        row = cur.fetchone()
    before = {r["id"]: r["passed"] for r in (row["results"] if row else [])}
    fp = fingerprint(settings, assistant, tenant)
    run = run_suite(
        settings,
        assistant,
        tenant,
        tenant,
        "nightly",
        "nightly",
        parallel=max(1, settings.suite_parallel),
    )
    _mark(settings, tenant, run["id"], fp)
    worse = [
        r["id"]
        for r in run["results"]
        if r["critical"] and before.get(r["id"]) and not r["passed"]
    ]
    if worse:
        _tell_staff(
            settings,
            tenant,
            {
                "id": f"nightly-{run['id']}",
                "title": "Nightly test of all questions",
                "diff": {},
            },
            False,
            [
                f"{len(worse)} critical question(s) got worse since last night: {', '.join(worse[:5])}"
            ],
            False,
        )
    return {"run": run["id"], "scores": run["scores"], "worse": worse}


def select_quick(
    settings: Settings, embedder: Embedder, tenant: str, p: dict, note: str | None
) -> set[str]:
    """The questions a change can affect, for a quick test: the same programme's questions
    (critical first), the questions most like the corrected one, and nothing else. The whole
    test set still runs nightly."""
    from . import health

    cases = _cases(settings, tenant)
    d = p.get("diff") or {}
    question = d.get("question") or p.get("title") or ""
    text = " ".join(
        str(x)
        for x in (question, d.get("answer"), note, p.get("title"), d.get("ref_title"))
        if x
    )
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select code, name, aliases from kb.programs where status = 'approved'"
        )
        programmes = cur.fetchall()
    codes = {pr["code"] for pr in programmes if health._mentions(text, pr)}
    codes |= {c["code"] for c in d.get("fee_changes") or []}
    names = [
        n.lower()
        for pr in programmes
        if pr["code"] in codes
        for n in (pr["code"], pr["name"], *pr["aliases"])
        if len(n) >= 2
    ]
    same = [
        c
        for c in cases
        if any(r == f"catalogue:{code}" for r in c["expected_refs"] for code in codes)
        or any(n in (c["question"] or "").lower() for n in names)
    ]
    if d.get("fee_changes"):
        # A fee can only change fee answers: the programme's fee questions and its critical ones.
        same = [
            c
            for c in same
            if c["critical"]
            or "-fee-" in c["ext_id"]
            or "fee" in (c["question"] or "").lower()
        ]
    similar: list[dict] = []
    try:
        qv = embedder.query(question)
        vs = embedder.documents([c["question"] for c in cases])

        def cos(a, b):
            na = sum(x * x for x in a) ** 0.5
            nb = sum(x * x for x in b) ** 0.5
            return sum(x * y for x, y in zip(a, b)) / (na * nb or 1)

        ranked = sorted(zip(cases, vs), key=lambda cv: -cos(qv, cv[1]))
        similar = [c for c, _ in ranked[: settings.quick_similar]]
    except Exception:  # noqa: BLE001 - the programme's questions still run
        log.exception("could not rank similar questions")
    ordered = (
        [c for c in same if c["critical"]]
        + similar
        + [c for c in same if not c["critical"]]
    )
    chosen: list[str] = []
    for c in ordered:
        if c["ext_id"] not in chosen:
            chosen.append(c["ext_id"])
    return set(chosen[: settings.quick_max_cases])


def _lazy_baseline(
    settings: Settings,
    assistant: Assistant,
    tenant: str,
    trigger: str,
    cand: dict,
    new_ids: set[str],
) -> dict:
    """The "before" of a quick test: the live college (still without the change) answers only the
    questions that failed with it; a question that passed counts as unchanged."""
    failed = {
        r["id"] for r in cand["results"] if not r["passed"] and r["id"] not in new_ids
    }
    before = (
        run_suite(
            settings,
            assistant,
            tenant,
            tenant,
            "baseline",
            trigger,
            None,
            None,
            only=failed,
            parallel=max(1, settings.suite_parallel),
        )["results"]
        if failed
        else []
    )
    by_id = {r["id"]: r for r in before}
    results = [by_id.get(r["id"], r) for r in cand["results"] if r["id"] not in new_ids]
    sc = scores(results)
    sc.update(latency_summary(results))
    return {"id": None, "scores": sc, "results": results}


def recent_reviews(settings: Settings, tenant: str) -> dict:
    """Grounding reviews of the last day's test answers, by answer text and citations: the same
    answer citing the same sources gets the same review, so a quick test only pays (in model time)
    for answers that changed. A changed fee changes the answer's text, so it is reviewed again."""
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select results from learn.eval_runs where status = 'done' and results is not null"
            " and finished_at > now() - interval '24 hours' order by finished_at"
        )
        rows = cur.fetchall()
    out: dict = {}
    for row in rows:
        for r in row["results"]:
            if "claims" in r and r.get("answer") and len(r["answer"]) < 1500:
                out[(r["answer"], tuple(r.get("cited") or []))] = {
                    "claims": r["claims"],
                    "supported_claims": r.get("supported_claims", 0),
                    "unsupported_claims": r.get("unsupported_claims", []),
                }
    return out


def _subset(run: dict, only: set[str]) -> dict:
    results = [r for r in run["results"] if r.get("id") in only]
    sc = scores(results)
    sc.update(latency_summary(results))
    return {**run, "scores": sc, "results": results}


def _tell_staff(
    settings: Settings,
    tenant: str,
    p: dict,
    passed: bool,
    reasons: list[str],
    early: bool,
) -> None:
    """The console's bell: the approvers of this area learn the test result without watching the
    page (Module C keeps it to the console; it is not sent to the college's webhooks)."""
    try:
        notify(
            settings,
            tenant,
            "improvement.tested",
            {
                "proposal_id": str(p["id"]),
                "title": p["title"],
                "department": p.get("department"),
                "decided_by": p.get("decided_by"),
                "passed": passed,
                "taken_down": early and not passed,
                "reasons": reasons[:3],
                "fee_changes": (p.get("diff") or {}).get("fee_changes") or [],
            },
        )
    except Exception:  # noqa: BLE001 - a missed bell never changes the result
        log.exception("could not tell staff about %s", p["id"])


def _take_down(
    settings: Settings,
    assistant: Assistant,
    embedder: Embedder,
    tenant: str,
    p: dict,
    published: dict,
    verdict: dict | None,
) -> None:
    """An early-published change that failed (or could not finish) its test is undone. Staff see
    it as "taken down" in Approvals and Review answers."""
    rollback_change(settings, assistant, tenant, published, "evaluation gate", embedder)
    log.info(
        "took down %s: %s",
        p["id"],
        (verdict or {}).get("reasons", ["the test could not run"]),
    )


def gate_and_deploy(
    settings: Settings,
    embedder: Embedder,
    assistant: Assistant,
    tenant: str,
    proposal_id: str,
    note: str | None,
    limit: int | None = None,
    publish_now: bool = False,
) -> dict:
    """Background job after approval: sandbox -> baseline -> apply -> candidate -> compare -> deploy.

    publish_now (College Admin's urgent fix): the change goes live as soon as the sandbox copy
    is made, so the "before" run still measures the college without it; the gate then runs as
    usual, and a failed gate takes the change down again."""
    with tx(settings.database_url, tenant) as cur:
        cur.execute("select * from learn.proposals where id::text = %s", (proposal_id,))
        p = cur.fetchone()
    follow = None
    if p["kind"] == "curated_answer":
        from . import health

        d = p["diff"] or {}
        text = (
            note.strip() if note and len(note.strip()) >= 15 else d.get("answer") or ""
        )
        with tx(settings.database_url, tenant) as cur:
            changes = health.fee_changes(cur, f"{d.get('question') or ''} {text}")
            cur.execute(
                "select code, name, aliases from kb.programs where code = any(%s)",
                ([c["code"] for c in changes],),
            )
            names = {
                r["code"]: [r["code"], r["name"], *r["aliases"]] for r in cur.fetchall()
            }
            p = {**p, "diff": {**d, "fee_changes": changes}}
            cur.execute(
                "update learn.proposals set diff = %s where id::text = %s",
                (Jsonb(p["diff"]), proposal_id),
            )
        if changes:

            def follow(cases):
                return _follow_fees(cases, changes, names)

    sandbox, idmap = clone_tenant(settings, tenant)
    published = None
    try:
        # Marked as a sandbox so Module B lets us delete it again afterwards.
        assistant.put_settings(
            sandbox, {**assistant.get_settings(tenant), "sandbox": True}
        )
        trigger = f"proposal:{proposal_id}"
        extra = new_cases(p)
        reviews: dict = {}  # answers unchanged by the proposal are not reviewed twice
        fp = fingerprint(settings, assistant, tenant) if limit is None else None
        only = None
        lesson = bool((p.get("diff") or {}).get("lesson_id"))
        if settings.gate_mode == "quick" and limit is None:
            only = select_quick(settings, embedder, tenant, p, note) | {
                c["ext_id"] for c in extra
            }
            if lesson:  # plus a stratified sample of the whole test set (selflearn/gatecheck.py)
                from .selflearn import gatecheck

                only |= gatecheck.stratified_sample(
                    settings, tenant, settings.learning_gate_sample
                )
        r_cases = r_before = None
        if lesson:
            from .selflearn import gatecheck

            r_cases = gatecheck.retrieval_cases(settings, tenant, extra)
            r_before = gatecheck.retrieval(settings, embedder, sandbox, r_cases)
        par = max(1, settings.suite_parallel)
        if only is not None:
            reviews.update(recent_reviews(settings, tenant))
        if publish_now:
            published = apply_change(settings, embedder, assistant, tenant, p, note)
            with tx(settings.database_url, tenant) as cur:
                cur.execute(
                    "update learn.proposals set deployed_ref=%s, updated_at=now() where id::text=%s",
                    (Jsonb({**published, "published_early": True}), proposal_id),
                )
        base = reusable_baseline(settings, tenant, fp)
        if base is not None and only is not None:
            base = _subset(base, only)
        lazy = only is not None and base is None and not publish_now
        if lazy:
            # Quick test: measure "before" only for questions that fail after the change (below);
            # a question that passes with the change cannot have got worse.
            base = {"id": None, "scores": {}, "results": [], "lazy": True}
        if base is None:
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
                only=only,
                parallel=par,
            )
            if only is None:  # only a whole-set run can stand in for a later baseline
                _mark(settings, tenant, base["id"], fp)
        applied = apply_change(settings, embedder, assistant, sandbox, p, note, idmap)
        r_after = (
            gatecheck.retrieval(settings, embedder, sandbox, r_cases)
            if lesson
            else None
        )
        if applied.get(
            "external_ref"
        ):  # updated an existing answer: its own ref is what to expect
            for c in extra:
                c["expected_refs"] = [
                    f"qa:{applied['external_ref']}" if r.startswith("qa:") else r
                    for r in c["expected_refs"]
                ]
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
            follow=follow,
            only=only,
            parallel=par,
        )
        if lazy:
            base = _lazy_baseline(
                settings, assistant, tenant, trigger, cand, {c["ext_id"] for c in extra}
            )
        retry_regressions(
            settings, assistant, tenant, sandbox, base, cand, extra, reviews, follow
        )
        verdict = compare(
            base,
            cand,
            0.0
            if lesson
            else settings.gate_tolerance_points,  # lessons: zero regressions
            {c["ext_id"] for c in extra},
        )
        if lesson:
            more, summary = gatecheck.judge(
                r_before, r_after, {c["ext_id"] for c in extra}
            )
            verdict = {
                **verdict,
                "passed": verdict["passed"] and not more,
                "reasons": verdict["reasons"] + more,
                "retrieval": summary,
            }
        if published is not None:
            # Already live: keep it if the gate passed, take it down if not.
            deployed = (
                {**published, "published_early": True} if verdict["passed"] else None
            )
            if not verdict["passed"]:
                _take_down(settings, assistant, embedder, tenant, p, published, verdict)
        else:
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
                            # the run reused as the baseline, when nothing had changed since
                            "baseline_reused_from": base["id"]
                            if base.get("reused")
                            else None,
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
                    Jsonb(
                        deployed
                        if deployed is not None or published is None
                        else {"published_early": True, "taken_down": True}
                    ),
                    proposal_id,
                ),
            )
        if verdict["passed"] and limit is None and only is None:
            # The candidate measured the college as it now is: the next gate can start from it.
            _mark(
                settings, tenant, cand["id"], fingerprint(settings, assistant, tenant)
            )
        _tell_staff(
            settings,
            tenant,
            p,
            verdict["passed"],
            verdict.get("reasons", []),
            published is not None,
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
        if published is not None:  # untested changes do not stay live
            try:
                _take_down(settings, assistant, embedder, tenant, p, published, None)
            except Exception:  # noqa: BLE001 - recorded below; staff can roll back by hand
                log.exception("could not take down %s", proposal_id)
        with tx(settings.database_url, tenant) as cur:
            cur.execute(
                "update learn.proposals set status='failed_gate', note=%s, deployed_ref=%s,"
                " updated_at=now() where id::text=%s",
                (
                    f"Evaluation could not run: {exc}"[:500],
                    Jsonb({"published_early": True, "taken_down": True})
                    if published is not None
                    else None,
                    proposal_id,
                ),
            )
        _tell_staff(
            settings,
            tenant,
            p,
            False,
            [f"the test could not run: {exc}"[:200]],
            published is not None,
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


def resume_interrupted(
    settings: Settings, embedder: Embedder, assistant: Assistant, start=None
) -> list[str]:
    """At start-up: approvals whose test run was cut off by the restart are tested again, one
    after another in the background, instead of staying "being tested" for ever. Only one
    knowledge process runs the gate, so anything still 'evaluating' here was interrupted."""
    import threading

    with psycopg.connect(settings.worker_database_url, autocommit=True) as conn:
        rows = conn.execute(
            "update learn.proposals set updated_at = now() where status = 'evaluating'"
            " returning id::text, tenant_id::text, note, deployed_ref"
        ).fetchall()
    if not rows:
        return []

    def work():
        for pid, tenant, note, ref in rows:
            early = bool(ref and ref.get("published_early"))
            try:
                if early and not ref.get("taken_down"):
                    # Live but untested: undo it so the new copy is measured without it, then
                    # publish it again straight away as part of the re-run.
                    rollback_change(
                        settings, assistant, tenant, ref, "restart", embedder
                    )
                gate_and_deploy(
                    settings,
                    embedder,
                    assistant,
                    tenant,
                    pid,
                    note,
                    settings.eval_max_cases,
                    early,
                )
            except Exception:  # noqa: BLE001 - gate_and_deploy records its own failure
                log.exception("resumed evaluation of %s failed", pid)

    (start or (lambda f: threading.Thread(target=f, daemon=True).start()))(work)
    return [r[0] for r in rows]


SWEEP_HOURS = 2  # a test run or model comparison still "running" after this was cut off


def sweep_stale(settings: Settings) -> dict:
    """Runs every few minutes: work cut off by a restart is marked as such instead of showing
    "processing" or "running" for ever. Uploads are not kept, so an interrupted one is rejected
    with a note to upload it again (a re-upload is then not taken for a duplicate). Proposals are
    left alone: start-up tests them again (resume_interrupted)."""
    with psycopg.connect(settings.worker_database_url, autocommit=True) as conn:
        docs = conn.execute(
            "update kb.documents set status = 'rejected', status_reason = 'Reading the file was"
            " interrupted (service restart); upload it again', updated_at = now()"
            " where status = 'processing' and created_at < now() - make_interval(mins => %s)",
            (STALE_MINUTES,),
        ).rowcount
        runs = conn.execute(
            "update learn.eval_runs set status = 'error', error = 'interrupted', finished_at = now()"
            " where status = 'running' and started_at < now() - make_interval(hours => %s)",
            (SWEEP_HOURS,),
        ).rowcount
        comps = conn.execute(
            "update learn.comparisons set status = 'error', error = 'interrupted', finished_at = now()"
            " where status = 'running' and started_at < now() - make_interval(hours => %s)",
            (SWEEP_HOURS,),
        ).rowcount
    return {"documents": docs, "runs": runs, "comparisons": comps}


def recover_interrupted(settings: Settings) -> dict:
    """After a restart: gate runs left 'evaluating' or 'running' are marked interrupted (so they
    can be approved again) and orphaned sandbox copies are removed. Safe to run any time."""
    import psycopg

    with psycopg.connect(settings.worker_database_url, autocommit=True) as conn:
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
