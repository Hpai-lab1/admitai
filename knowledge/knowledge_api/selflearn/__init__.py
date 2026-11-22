"""Self-learning knowledge layer (docs/self-learning/README.md).

Each new feedback is understood (classify), weighed (trust), grouped with agreeing feedback from
other sessions (agree), checked against the college's own sources (evidence), turned into a lesson
(draft), tested by the existing evaluation gate and only then made live as a new knowledge version
(apply); live lessons are watched and rolled back when they make things worse (monitor).

Feedback is a signal, never the truth. Numbers are never written from feedback. Everything runs
only when KNOWLEDGE_SELF_LEARNING_ENABLED is on.
"""

import logging

log = logging.getLogger("admitai.selflearn")


def event(stage: str, **fields) -> None:
    """One structured log line per stage, with the tenant, feedback and lesson ids."""
    log.info(
        "selflearn %s %s",
        stage,
        " ".join(f"{k}={v}" for k, v in fields.items() if v is not None),
    )
