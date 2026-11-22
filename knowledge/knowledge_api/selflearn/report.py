"""Observability: one read-only report per college (GET /learning/self/report, or
python -m knowledge_api.selflearn.report --tenant-id <uuid>)."""

from __future__ import annotations

import argparse
import json
import sys

from ..config import Settings
from ..db import tx


def build(settings: Settings, tenant: str, limit: int = 50) -> dict:
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select a.class, count(*) as n from learn.feedback_analysis a group by a.class"
        )
        by_class = {r["class"]: r["n"] for r in cur.fetchall()}
        cur.execute(
            "select status, count(*) as n from learn.feedback_events group by status"
        )
        by_status = {r["status"]: r["n"] for r in cur.fetchall()}
        cur.execute("select status, count(*) as n from learn.lessons group by status")
        lessons = {r["status"]: r["n"] for r in cur.fetchall()}
        cur.execute(
            "select least(width_bucket(score, 0, 1, 5), 5) as b, count(*) as n from learn.trust_scores"
            " where subject_kind = 'feedback' group by b order by b"
        )
        buckets = ["0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"]
        trust = {
            buckets[r["b"] - 1]: r["n"] for r in cur.fetchall() if 1 <= r["b"] <= 5
        }
        cur.execute(
            "select l.id::text, l.type, l.status, l.issue_class, l.question, l.reason,"
            " cardinality(l.sessions) as sessions, cardinality(l.created_from) as feedback,"
            " l.knowledge_version, l.proposal_id::text, l.created_at, l.live_at, l.rolled_back_at"
            " from learn.lessons l order by l.updated_at desc limit %s",
            (limit,),
        )
        recent = cur.fetchall()
        cur.execute(
            "select m.lesson_id::text, m.metrics, m.measured_at from learn.lesson_metrics m"
            " where m.phase = 'gate' order by m.measured_at desc limit %s",
            (limit,),
        )
        gates = cur.fetchall()
        cur.execute(
            "select version, lesson_id::text, action, note, at from learn.knowledge_versions"
            " order by version desc limit %s",
            (limit,),
        )
        versions = cur.fetchall()
    return {
        "enabled": settings.self_learning_enabled,
        "feedback": {"by_class": by_class, "by_status": by_status},
        "lessons": {"by_status": lessons, "recent": recent},
        "gate_results": gates,
        "rollbacks": [v for v in versions if v["action"] == "rollback"],
        "knowledge_versions": versions,
        "trust_distribution": trust,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Self-learning report for one college")
    ap.add_argument("--tenant-id", required=True)
    a = ap.parse_args()
    s = Settings()
    if not s.database_url:
        sys.exit("Set KB_DATABASE_URL (source local.env.sh)")
    print(json.dumps(build(s, a.tenant_id), indent=2, default=str))
