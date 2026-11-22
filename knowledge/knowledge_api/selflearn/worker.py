"""The real-time learning worker. Started by the API only when KNOWLEDGE_SELF_LEARNING_ENABLED is on.

Two threads:
- feedback: woken by PostgreSQL NOTIFY (migration 010's trigger on learn.events) within a second
  of a new feedback event, and every LEARNING_POLL_S seconds in case a notification was missed.
  It runs stages 1-5 for each new feedback; one feedback is processed once (SKIP LOCKED claim,
  retried up to 3 times with back-off).
- lessons: runs the evaluation gate for drafted lessons one at a time (the gate already queues
  behind other test suites and yields to visitors), records staff decisions from the approval
  inbox, and every MONITOR_EVERY_S checks live lessons and rolls back the ones that degrade.

    python -m knowledge_api.selflearn.worker once --tenant-id <uuid>   # everything, now, then exit
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from dataclasses import replace

import psycopg

from ..assistant_client import Assistant, AssistantClient
from ..config import Settings
from ..embedder import Embedder, make_embedder
from . import event, log, monitor, pipeline

MONITOR_EVERY_S = 600


def classifier_model(settings: Settings):
    if settings.learning_classifier != "llm":
        return None
    from ..drafting import GatewayDraftModel

    return GatewayDraftModel(
        replace(settings, draft_model=settings.learning_classifier_model)
    )


def tenants_with_work(settings: Settings) -> list[str]:
    with psycopg.connect(settings.worker_database_url) as conn:
        rows = conn.execute(
            "select distinct tenant_id::text from learn.events e where e.type = 'feedback.created'"
            " and e.received_at > now() - make_interval(secs => %s)"
            " and not exists (select 1 from learn.feedback_events f where f.event_ref = e.id)"
            " union select distinct tenant_id::text from learn.feedback_events"
            "  where status in ('new', 'processing')"
            " union select distinct tenant_id::text from learn.lessons"
            "  where status in ('gated', 'verifying', 'pending_approval', 'live')"
            "  and tenant_id not in (select sandbox_id from kb.sandboxes)",
            (settings.learning_backfill_h * 3600,),
        ).fetchall()
    return [r[0] for r in rows]


def feedback_round(
    settings: Settings,
    embedder: Embedder,
    assistant: Assistant,
    tenant: str,
    model=None,
    fetch=None,
) -> list[dict]:
    pipeline.take_new_events(settings, tenant)
    done = []
    while rows := pipeline.claim(
        settings, tenant
    ):  # in batches, until nothing is waiting
        for row in rows:
            try:
                done.append(
                    pipeline.process(
                        settings,
                        embedder,
                        assistant,
                        tenant,
                        row,
                        model=model,
                        fetch=fetch,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - retried with back-off, then 'failed'
                log.exception("selflearn feedback %s failed", row["id"])
                pipeline.fail(settings, tenant, row["id"], row["attempts"], str(exc))
    return done


def lesson_round(
    settings: Settings,
    embedder: Embedder,
    assistant: Assistant,
    tenant: str,
    model=None,
    fetch=None,
) -> list[dict]:
    out = []
    pipeline.sync_decisions(settings, tenant)
    while (lesson_id := pipeline.next_for_gate(settings, tenant)) is not None:
        out.append(
            pipeline.gate_lesson(
                settings,
                embedder,
                assistant,
                tenant,
                lesson_id,
                model=model,
                fetch=fetch,
            )
        )
    pipeline.sync_decisions(settings, tenant)
    return out


def once(
    settings: Settings,
    embedder: Embedder,
    assistant: Assistant,
    tenant: str,
    model=None,
    fetch=None,
    check_live: bool = True,
) -> dict:
    """Process everything waiting for one college, synchronously (tests, demo, nightly)."""
    report = {
        "feedback": feedback_round(settings, embedder, assistant, tenant, model, fetch),
        "lessons": lesson_round(settings, embedder, assistant, tenant, model, fetch),
    }
    if check_live:
        report["rolled_back"] = monitor.check(settings, embedder, assistant, tenant)
    return report


class Worker:
    def __init__(self, settings: Settings, embedder: Embedder, assistant: Assistant):
        self.settings, self.embedder, self.assistant = settings, embedder, assistant
        self.model = classifier_model(settings)
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        for name, target in (
            ("selflearn-listen", self._listen),
            ("selflearn-feedback", self._feedback),
            ("selflearn-lessons", self._lessons),
        ):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self.threads.append(t)
        event("worker_started", poll_s=self.settings.learning_poll_s)

    def close(self) -> None:
        self.stop.set()
        self.wake.set()

    def _listen(self) -> None:
        while not self.stop.is_set():
            try:
                with psycopg.connect(
                    self.settings.worker_database_url, autocommit=True
                ) as conn:
                    conn.execute("listen learn_feedback")
                    while not self.stop.is_set():
                        for _n in conn.notifies(timeout=1.0, stop_after=1):
                            self.wake.set()
            except Exception:  # noqa: BLE001 - polling still runs; try listening again soon
                log.exception("selflearn listen")
                self.stop.wait(10)

    def _feedback(self) -> None:
        while not self.stop.is_set():
            self.wake.wait(self.settings.learning_poll_s)
            self.wake.clear()
            try:
                for tenant in tenants_with_work(self.settings):
                    feedback_round(
                        self.settings, self.embedder, self.assistant, tenant, self.model
                    )
            except Exception:  # noqa: BLE001 - next round
                log.exception("selflearn feedback round")

    def _lessons(self) -> None:
        last_monitor = 0.0
        while not self.stop.wait(self.settings.learning_poll_s):
            try:
                tenants = tenants_with_work(self.settings)
                for tenant in tenants:
                    lesson_round(
                        self.settings, self.embedder, self.assistant, tenant, self.model
                    )
                if time.monotonic() - last_monitor > MONITOR_EVERY_S:
                    last_monitor = time.monotonic()
                    for tenant in tenants:
                        monitor.check(
                            self.settings, self.embedder, self.assistant, tenant
                        )
            except Exception:  # noqa: BLE001 - next round
                log.exception("selflearn lesson round")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Self-learning worker")
    ap.add_argument("cmd", choices=["once"])
    ap.add_argument("--tenant-id", required=True)
    a = ap.parse_args()
    s = Settings()
    if not s.self_learning_enabled:
        sys.exit("KNOWLEDGE_SELF_LEARNING_ENABLED is off")
    if not s.learning_session_salt:
        sys.exit("Set LEARNING_SESSION_SALT")
    print(
        once(s, make_embedder(s), AssistantClient(s), a.tenant_id, classifier_model(s))
    )
