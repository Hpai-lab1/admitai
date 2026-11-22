"""Stage 2, trust: feedback is not trusted by default.

A score from 0 to 1 per feedback, from: rate limits and floods per session, duplicates, whether
the comment fits the conversation (did the assistant say what the visitor complains about?),
abuse and injection patterns, and that session's history. Feedback below LEARNING_MIN_TRUST is
stored and logged but never used for a lesson.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from .classify import Analysis

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
# "you said 5 lakh", "it says the deadline is 30 June", "bot told me 60 seats"
_QUOTING = re.compile(
    r"\b(you said|you say|it said|it says|bot said|bot says|the answer said|the answer says|"
    r"assistant said|told me|you told|answer was|you wrote|it wrote)\b(?P<rest>.{0,80})",
    re.I,
)


def session_fp(salt: str, tenant: str, payload: dict) -> str:
    """A salted hash of the visitor's session. Module C's own hash when it sends one, else the
    conversation id (a reload is then a new 'session'; the trust score covers some of that)."""
    raw = payload.get("session_fp") or f"conv:{payload.get('conversation_id') or ''}"
    return hashlib.sha256(f"{salt}|{tenant}|{raw}".encode()).hexdigest()[:32]


@dataclass
class History:
    last_hour: int = 0  # this session's feedback in the last hour, before this one
    duplicates: int = 0  # same session, same message or same comment, last 24 h
    past_total: int = 0
    past_bad: int = (
        0  # past feedback from this session that was junk/abuse or low trust
    )
    flip_flop: bool = False  # this session rated the same answer both up and down


@dataclass
class Trust:
    score: float
    factors: dict = field(default_factory=dict)

    def trusted(self, minimum: float) -> bool:
        return self.score >= minimum


def _digits(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUMBER.findall(text or "")}


def consistent(fb: dict) -> bool | None:
    """Does the complaint fit what the assistant actually said? False when the visitor quotes a
    figure the answer never contained; None when it cannot be told."""
    m = _QUOTING.search(fb.get("comment") or "")
    if not m:
        return None
    quoted = _digits(m.group("rest"))
    if not quoted:
        return None
    return bool(quoted & _digits(fb.get("answer") or ""))


def score(
    fb: dict, analysis: Analysis, history: History, max_per_hour: int = 10
) -> Trust:
    if fb.get("actor") == "staff":
        return Trust(1.0, {"staff": 1.0})
    s, f = 0.8, {"base": 0.8}

    def take(name: str, amount: float) -> None:
        nonlocal s
        s -= amount
        f[name] = -round(amount, 3)

    if "injection" in analysis.flags or "abuse" in analysis.flags:
        take("abuse_or_injection", 1.0)
    if "link" in analysis.flags:
        take("link_in_comment", 0.3)
    if analysis.cls == "junk_abuse":
        take("junk", 0.5)
    if history.last_hour >= max_per_hour:
        take("flood", 0.6)
    elif history.last_hour >= max_per_hour // 2:
        take("high_rate", 0.2)
    if history.duplicates:
        take("duplicate", min(0.6, 0.3 * history.duplicates))
    if history.flip_flop:
        take("contradictory", 0.3)
    if history.past_total >= 3:
        bad = history.past_bad / history.past_total
        if bad:
            take("bad_history", 0.5 * bad)
    c = consistent(fb)
    if c is False:
        take("inconsistent_with_answer", 0.4)
    elif c is True:
        s += 0.05
        f["quotes_answer"] = 0.05
    if not fb.get("question"):
        take("conversation_not_found", 0.3)
    return Trust(round(max(0.0, min(1.0, s)), 3), f)


HISTORY_SQL = """
select
  count(*) filter (where f.created_at > %(at)s - interval '1 hour') as last_hour,
  count(*) filter (where f.created_at > %(at)s - interval '24 hours'
                     and (f.message_id = %(mid)s
                          or (%(comment)s <> '' and lower(coalesce(f.comment, '')) = %(comment)s)))
    as duplicates,
  count(*) as past_total,
  count(*) filter (where a.class = 'junk_abuse' or t.score < %(min)s) as past_bad,
  bool_or(f.message_id = %(mid)s and f.rating is distinct from %(rating)s) as flip_flop
from learn.feedback_events f
left join learn.feedback_analysis a on a.feedback_id = f.id
left join learn.trust_scores t on t.subject_kind = 'feedback' and t.subject_id = f.id::text
where f.session_fp = %(fp)s and f.id <> %(id)s and f.id < %(id)s
"""


def history(cur, fb: dict, minimum: float) -> History:
    cur.execute(
        HISTORY_SQL,
        {
            "at": fb["created_at"],
            "mid": fb.get("message_id") or "",
            "comment": (fb.get("comment") or "").strip().lower(),
            "min": minimum,
            "rating": fb.get("rating"),
            "fp": fb["session_fp"],
            "id": fb["id"],
        },
    )
    r = cur.fetchone()
    return History(
        last_hour=r["last_hour"] or 0,
        duplicates=r["duplicates"] or 0,
        past_total=r["past_total"] or 0,
        past_bad=r["past_bad"] or 0,
        flip_flop=bool(r["flip_flop"]),
    )
