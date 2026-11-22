"""Demo of the self-learning knowledge layer on a throw-away demo college.

    source local.env.sh            # from the repository root (needs the local PostgreSQL)
    cd knowledge
    python scripts/demo_self_learning.py [--keep]

1. asks a question that currently fails (the assistant cites the wrong page);
2. three different sessions rate the answer down with a comment;
3. the worker classifies, checks trust and agreement, finds the evidence on the college's own
   page, runs the evaluation gate and makes the lesson live as a new knowledge version;
4. a new session asks the same question and gets the right, cited page;
5. a fake fee claim ("fees are ₹10") is checked against the programme page and rejected;
6. prints the lesson log.

Offline and repeatable: the deterministic test embedder, and a small stand-in for Module B that
answers from Module A's search (as the gate's own tests do). With the real services, turn on
KNOWLEDGE_SELF_LEARNING_ENABLED and LEARNING_SESSION_SALT and use the widget instead; the steps
are the same. The demo college is deleted afterwards unless --keep is given.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["KNOWLEDGE_SELF_LEARNING_ENABLED"] = "true"

import psycopg  # noqa: E402

from knowledge_api import documents, ingest, migrate, search  # noqa: E402
from knowledge_api.config import Settings  # noqa: E402
from knowledge_api.db import Jsonb, tx  # noqa: E402
from knowledge_api.embedder import HashEmbedder  # noqa: E402
from knowledge_api.selflearn import evidence, report, worker  # noqa: E402

QUESTION = "What scholarship do I get for 90 percent marks?"
PAGES = {
    "Admissions brochure": "Scholarship marks percent: scholarship marks percent details for 90 "
    "percent marks are printed in the scholarship section of the admissions brochure.",
    "Merit scholarship policy": "Merit scholarship: 90 percent marks get a 50 percent tuition "
    "fee waiver.",
    "Hostel rules": "Hostel: the campus hostel has 400 rooms for boys and girls, with mess food.",
}
PROGRAMME_URL = "https://demo-college.example/programmes/btech-cse"


def say(step: str, text: str = "") -> None:
    print(f"\n=== {step}\n{text}" if text else f"\n=== {step}")


class StandInAssistant:
    """Answers with the best page Module A's search finds, and cites it (like Module B would)."""

    def __init__(self, settings, embedder):
        self.settings, self.embedder = settings, embedder
        self.messages: dict[str, dict] = {}
        self.config: dict[str, dict] = {}

    def ask(self, tenant, conversation_id, text, channel="web_chat"):
        res = search.search(
            self.settings.database_url, self.embedder, tenant, text, top_k=1
        )
        if not res["items"] or res["items"][0]["vector_score"] < 0.3:
            mid = str(uuid.uuid4())  # Module B keeps a no-answer turn too
            self.messages[mid] = {
                "id": mid,
                "question": text,
                "answer": "I don't know.",
                "sources": [],
                "outcome": "no_answer",
                "trace_id": None,
            }
            return {
                "text": "I don't know.",
                "done": {
                    "outcome": "no_answer",
                    "message_id": mid,
                    "sources": [],
                    "route": {},
                },
            }
        hit = res["items"][0]
        c = hit["citation"]
        mid = str(uuid.uuid4())
        source = {
            "source_type": c["source_type"],
            "title": c["title"],
            "ref_id": c["qa_id"] or c["document_id"],
        }
        self.messages[mid] = {
            "id": mid,
            "question": text,
            "answer": hit["content"],
            "sources": [source],
            "outcome": "answered",
            "trace_id": None,
        }
        return {
            "text": hit["content"],
            "done": {
                "outcome": "answered",
                "message_id": mid,
                "sources": [source],
                "route": {"routes": [{"role": "admission"}]},
            },
        }

    def message(self, tenant, message_id):
        return self.messages.get(message_id)

    def get_settings(self, tenant):
        return dict(self.config.get(tenant, {"sensitive_topics": []}))

    def put_settings(self, tenant, config):
        self.config[tenant] = dict(config)

    def delete_sandbox(self, tenant):
        self.config.pop(tenant, None)

    def signals(self, tenant, since):
        return []

    def unsupported(self, tenant, since):
        return []

    def review(self, tenant, message_ids):
        return {
            m: {"verdict": "supported", "claims": 1, "supported": 1, "unsupported": []}
            for m in message_ids
        }

    def versions(self, tenant):
        raise RuntimeError("no versions in the demo")


def setup(settings, embedder, tenant) -> dict:
    ids = {}
    for title, text in PAGES.items():
        data = text.encode()
        with tx(settings.database_url, tenant) as cur:
            row, _ = documents.create(cur, tenant, "page.txt", data, {"title": title})
        documents.process(settings, embedder, tenant, str(row["id"]), "page.txt", data)
        with tx(settings.database_url, tenant) as cur:
            documents.act(cur, str(row["id"]), "approve", "demo")
        ids[title] = str(row["id"])
    with psycopg.connect(settings.admin_database_url) as conn:
        for ext, q, ref in (
            ("h1", "Is there a hostel on campus for girls?", "Hostel rules"),
            ("h2", "How many hostel rooms are there?", "Hostel rules"),
        ):
            conn.execute(
                "insert into learn.eval_cases (tenant_id, ext_id, question, kind, expected_refs)"
                " values (%s,%s,%s,'direct',%s)",
                (tenant, ext, q, [f"doc:{ref}"]),
            )
        pid = conn.execute(
            "insert into kb.programs (tenant_id, code, name, aliases, level, academic_year, intake,"
            " source_url) values (%s, 'BTECH-CSE', 'B.Tech in Computer Science and Engineering',"
            " '{CSE,B.Tech CSE}', 'UG', '2026-27', 120, %s) returning id",
            (tenant, PROGRAMME_URL),
        ).fetchone()[0]
        conn.execute(
            "insert into kb.program_fees (tenant_id, program_id, category, component, amount)"
            " values (%s, %s, 'general', 'Total fee', 250000)",
            (tenant, pid),
        )
    return ids


def programme_page(url: str):
    """The demo college's programme page (served here instead of over the network)."""
    html = """<h1>B.Tech in Computer Science and Engineering</h1><h2>Eligibility</h2>
    <p>Pass in PUC with 60 percent.</p><h2>Intake</h2><p>120 seats</p>
    <h2>Fee Structure 2026-27</h2><table><tr><th>Category</th><th>Fee per year</th></tr>
    <tr><td>General</td><td>INR 2,50,000</td></tr></table>"""
    blocks, title, _ = ingest.parse_html(html)
    text = "\n".join(b.text for b in blocks)
    return evidence.Fetched(
        url, title or "B.Tech CSE", blocks, text, evidence.digest(text)
    )


def feedback(settings, assistant, tenant, n, conversation, text, comment, reason):
    r = assistant.ask(tenant, conversation, text)
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "insert into learn.events (tenant_id, event_id, type, payload) values (%s,%s,%s,%s)",
            (
                tenant,
                f"demo-{n}",
                "feedback.created",
                Jsonb(
                    {
                        "conversation_id": conversation,
                        "message_id": r["done"].get("message_id"),
                        "kind": "thumb",
                        "value": -1,
                        "reason": reason,
                        "comment": comment,
                        "actor": "visitor",
                    }
                ),
            ),
        )
    return r


def cleanup(settings, tenant):
    with psycopg.connect(settings.admin_database_url) as conn:
        for t in (
            "learn.knowledge_versions",
            "learn.lesson_metrics",
            "learn.lesson_evidence",
            "learn.retrieval_boosts",
            "learn.lesson_aliases",
            "learn.trust_scores",
            "learn.feedback_analysis",
            "learn.feedback_events",
            "learn.lessons",
            "learn.eval_runs",
            "learn.eval_cases",
            "learn.proposals",
            "learn.events",
            "kb.chunks",
            "kb.documents",
            "kb.program_fees",
            "kb.programs",
        ):
            conn.execute(f"delete from {t} where tenant_id = %s", (tenant,))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--keep", action="store_true", help="keep the demo college afterwards"
    )
    a = ap.parse_args()
    base = Settings()
    if not (base.database_url and base.admin_database_url):
        sys.exit("Set KB_DATABASE_URL and KB_ADMIN_DATABASE_URL (source local.env.sh)")
    import logging

    # The demo has no Module C to relay "improvement deployed" to; that is expected here.
    logging.getLogger("admitai").setLevel(logging.ERROR)
    migrate.migrate(base.admin_database_url)
    settings = replace(
        base,
        embedder="hash",
        self_learning_enabled=True,
        learning_session_salt=base.learning_session_salt or "demo-only-salt",
        learning_classifier="rules",
        gate_mode="full",
    )
    embedder = HashEmbedder()
    assistant = StandInAssistant(settings, embedder)
    tenant = str(uuid.uuid4())
    try:
        setup(settings, embedder, tenant)
        say("1. A question that fails today", QUESTION)
        r = assistant.ask(tenant, "before", QUESTION)
        print(
            "cited:", [s["title"] for s in r["done"]["sources"]], "->", r["text"][:90]
        )

        say("2. Three visitors in different sessions rate it down")
        for n, conv in enumerate(["session-a", "session-b", "session-c"]):
            feedback(
                settings,
                assistant,
                tenant,
                n,
                conv,
                QUESTION,
                "wrong page, not what I asked",
                "not_what_i_asked",
            )
            print(f"  {conv}: thumbs-down, 'wrong page, not what I asked'")

        say("3. The worker: classify -> trust -> agreement -> evidence -> gate -> live")
        out = worker.once(settings, embedder, assistant, tenant, fetch=programme_page)
        for f in out["feedback"]:
            print(
                f"  feedback {f['feedback_id']}: {f['class']}, trust {f['trust']}, {f.get('note') or f.get('status')}"
            )
        for lesson in out["lessons"]:
            g = lesson.get("gate") or {}
            print(
                f"  lesson: {lesson['status']}, knowledge version {lesson.get('knowledge_version')}"
            )
            if g.get("retrieval"):
                print(
                    f"  gate: passed={g['passed']} retrieval {g['retrieval']['before']} -> {g['retrieval']['after']}"
                )

        say("4. A new session asks the same question")
        r = assistant.ask(tenant, "new-session", QUESTION)
        print(
            "cited:", [s["title"] for s in r["done"]["sources"]], "->", r["text"][:90]
        )

        say("5. A fake fact claim: 'fees are ₹10 for B.Tech CSE'")
        feedback(
            settings,
            assistant,
            tenant,
            9,
            "session-z",
            "What is the fee for B.Tech CSE?",
            "fees are ₹10 for B.Tech CSE",
            "wrong",
        )
        worker.once(settings, embedder, assistant, tenant, fetch=programme_page)
        with tx(settings.database_url, tenant) as cur:
            cur.execute(
                "select status, reason from learn.lessons where issue_class = 'fact'"
            )
            fact = cur.fetchone()
            cur.execute("select amount from kb.program_fees")
            fee = cur.fetchone()["amount"]
        print(f"  lesson {fact['status']}: {fact['reason']}")
        print(f"  catalogue fee unchanged: {fee}")

        say("6. Lesson log")
        rep = report.build(settings, tenant)
        print(
            json.dumps(
                {k: rep[k] for k in ("feedback", "trust_distribution")},
                indent=2,
                default=str,
            )
        )
        for lesson in rep["lessons"]["recent"]:
            print(
                f"  [{lesson['status']}] {lesson['type'] or lesson['issue_class']}: "
                f"{(lesson['question'] or '')[:60]} | sessions {lesson['sessions']} | {lesson['reason']}"
            )
        for v in rep["knowledge_versions"]:
            print(f"  knowledge version {v['version']}: {v['action']} {v['note'][:70]}")
    finally:
        if a.keep:
            print(f"\nDemo college kept: tenant {tenant}")
        else:
            cleanup(settings, tenant)
    return 0


if __name__ == "__main__":
    sys.exit(main())
