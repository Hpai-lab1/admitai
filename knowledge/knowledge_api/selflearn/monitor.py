"""Stage 8, monitor and self-heal: a live lesson that makes things worse is rolled back
automatically with the existing rollback (gate.rollback_change), marked 'rolled_back', with why.

Signals per live lesson, from the time it went live:
- the ratings of questions like the lesson's own (cosine to its question cluster >= the boost
  similarity): at least LEARNING_ROLLBACK_MIN_VOTES trusted votes from at least
  LEARNING_MIN_AGREEING_SESSIONS distinct sessions, of which LEARNING_ROLLBACK_DOWN_RATIO or more
  are negative (one unhappy session cannot roll a lesson back either);
- the nightly full test: a case the lesson added fails in a nightly run after it went live.
"""

from __future__ import annotations

import numpy as np

from .. import gate, paraphrase
from ..assistant_client import Assistant
from ..config import Settings
from ..db import Jsonb, tx
from ..embedder import Embedder
from . import agree, event
from .apply import new_version


def live_signals(settings: Settings, embedder: Embedder, cur, lesson: dict) -> dict:
    cur.execute(
        "select f.id, f.question, f.rating, f.session_fp, t.score from learn.feedback_events f"
        " join learn.trust_scores t on t.subject_kind = 'feedback' and t.subject_id = f.id::text"
        " where f.created_at > %s and f.question is not null and f.rating is not null"
        " and not (f.id = any(%s))",
        (lesson["live_at"], list(lesson["created_from"])),
    )
    c = agree.unit(lesson["centroid"])
    votes = []
    for r in cur.fetchall():
        if r["score"] < settings.learning_min_trust:
            continue
        if (
            float(
                np.dot(c, agree.unit(embedder.query(paraphrase.expand(r["question"]))))
            )
            >= settings.learning_boost_similarity
        ):
            votes.append(r)
    down = [v for v in votes if v["rating"] == "down"]
    return {
        "votes": len(votes),
        "down": len(down),
        "sessions": len({v["session_fp"] for v in votes}),
        "down_ratio": round(len(down) / len(votes), 3) if votes else None,
    }


def nightly_failures(cur, lesson: dict) -> list[str]:
    cur.execute(
        "select ext_id from learn.eval_cases where proposal_id = %s",
        (lesson["proposal_id"],),
    )
    own = {r["ext_id"] for r in cur.fetchall()}
    if not own:
        return []
    cur.execute(
        "select results from learn.eval_runs where label = 'nightly' and status = 'done'"
        " and finished_at > %s and results is not null order by finished_at desc limit 1",
        (lesson["live_at"],),
    )
    row = cur.fetchone()
    return [
        r["id"]
        for r in (row["results"] if row else [])
        if r["id"] in own and not r["passed"]
    ]


def should_roll_back(settings: Settings, sig: dict, failing: list[str]) -> str | None:
    if failing:
        return f"the nightly test fails the lesson's own question(s): {', '.join(failing[:5])}"
    if (
        sig["votes"] >= settings.learning_rollback_min_votes
        and sig["sessions"] >= settings.learning_min_agreeing_sessions
        and (sig["down_ratio"] or 0) >= settings.learning_rollback_down_ratio
    ):
        return (
            f"{sig['down']} of {sig['votes']} trusted ratings from {sig['sessions']} sessions on "
            "these questions were negative after it went live"
        )
    return None


def check(
    settings: Settings, embedder: Embedder, assistant: Assistant, tenant: str
) -> list[dict]:
    """Check every live lesson of a college; roll back the ones that degrade results."""
    out = []
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select l.*, p.deployed_ref, p.status as proposal_status from learn.lessons l"
            " join learn.proposals p on p.id = l.proposal_id where l.status = 'live'"
        )
        lessons = cur.fetchall()
    for lesson in lessons:
        with tx(settings.database_url, tenant) as cur:
            sig = live_signals(settings, embedder, cur, lesson)
            failing = nightly_failures(cur, lesson)
            cur.execute(
                "insert into learn.lesson_metrics (tenant_id, lesson_id, phase, metrics)"
                " values (%s, %s, %s, %s)",
                (
                    tenant,
                    lesson["id"],
                    "nightly" if failing else "live",
                    Jsonb({**sig, "nightly_failing": failing}),
                ),
            )
        why = should_roll_back(settings, sig, failing)
        if why is None or lesson["proposal_status"] != "deployed":
            continue
        roll_back(settings, embedder, assistant, tenant, lesson, why)
        out.append({"lesson_id": str(lesson["id"]), "reason": why})
    return out


def roll_back(
    settings: Settings,
    embedder: Embedder,
    assistant: Assistant,
    tenant: str,
    lesson: dict,
    why: str,
) -> None:
    gate.rollback_change(
        settings,
        assistant,
        tenant,
        lesson["deployed_ref"] or {},
        "self-learning",
        embedder,
    )
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "update learn.eval_cases set active = false where proposal_id = %s",
            (lesson["proposal_id"],),
        )
        cur.execute(
            "update learn.proposals set status = 'rolled_back', note = %s, updated_at = now()"
            " where id = %s",
            (f"Rolled back automatically: {why}"[:500], lesson["proposal_id"]),
        )
        v = new_version(
            cur, tenant, str(lesson["id"]), str(lesson["proposal_id"]), "rollback", why
        )
        cur.execute(
            "update learn.lessons set status = 'rolled_back', reason = %s, knowledge_version = %s,"
            " rolled_back_at = now(), updated_at = now() where id = %s",
            (f"rolled back automatically: {why}"[:500], v, lesson["id"]),
        )
    event("rolled_back", tenant=tenant, lesson=lesson["id"], reason=why[:120])
