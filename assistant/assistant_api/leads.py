"""Lead Agent rules (PRD F5.1, F5.3 detection, F5.4): no extra model call.

- Minor detection: a stated age under 18 or Class 11-12 means the student's own contact details
  are never asked for; the widget is told to use guardian verification instead (Module C).
- Lead score 0-100 from engagement, intent signals and program fit, banded hot/warm/cold;
  weights and cut-offs are per tenant (`lead_scoring` in pack.yaml), with DEFAULT_SCORING below.
- When to ask: after the visitor has had useful answers, once per conversation, never during a
  sensitive topic or when they are a minor without guardian consent.
"""

from __future__ import annotations

import re

_AGE = re.compile(
    r"\b(?:i am|i'm|im|my age is|age)\s*(\d{1,2})\b(?:\s*(?:years?|yrs?)(?: old)?)?"
    r"|\b(\d{1,2})\s*(?:years?|yrs?)\s*old\b"
    r"|(?:मैं|मेरी उम्र)\s*(\d{1,2})\s*(?:साल|वर्ष)"
    r"|\b(\d{1,2})\s*(?:saal|sal)\s*(?:ka|ki)\b",
    re.IGNORECASE,
)
_SCHOOL = re.compile(
    r"\b(?:class|std|standard|grade)\s*(?:11|12|xi|xii)(?:th)?\b|\b(?:11|12)(?:th)\s*(?:class|std|standard|grade)?\b"
    r"|\b(?:first|second)\s*(?:year\s*)?(?:puc|pu)\b|\bpuc\b|(?:11|12)\s*वीं|कक्षा\s*(?:11|12)",
    re.IGNORECASE,
)
_ADULT_CONTEXT = re.compile(
    r"\b(?:passed|completed|finished|after)\s+(?:my\s+)?(?:12|12th|class 12|puc)\b",
    re.I,
)


def detect_minor(text: str) -> bool | None:
    """True if the visitor says they are under 18 or in Class 11-12; False if they state an adult
    age; None if nothing is said. 'I passed 12th' is not a minor signal."""
    for m in _AGE.finditer(text):
        age = int(next(g for g in m.groups() if g))
        if 10 <= age < 18:
            return True
        if 18 <= age <= 80:
            return False
    if _SCHOOL.search(text) and not _ADULT_CONTEXT.search(text):
        return True
    return None


HIGH_INTENT = {"fees", "dates", "process", "eligibility", "documents", "scholarship"}

# F5.4 gives the scale (0-100) and the bands (hot/warm/cold) but no weights or cut-offs. These are the
# Level 1 defaults chosen for the prototype; a college (or the guide) changes them in pack.yaml under
# `lead_scoring`, without a code change. Parts add up to at most 100.
DEFAULT_SCORING: dict = {
    "engagement_per_turn": 6,
    "engagement_max": 30,
    "intent_per_signal": 10,
    "asked_human_bonus": 15,
    "intent_max": 45,
    "program_fit": 25,
    "bands": {"hot": 70, "warm": 40},
    "prompt_after_answers": 2,
}


def scoring_problems(cfg: object) -> list[str]:
    """What is wrong with a tenant's `lead_scoring` block (empty list when it is usable)."""
    if cfg in (None, {}):
        return []
    if not isinstance(cfg, dict):
        return ["lead_scoring must be a mapping"]
    out = []
    for key, value in cfg.items():
        if key not in DEFAULT_SCORING:
            out.append(f"lead_scoring.{key} is not a known setting")
        elif key == "bands":
            if not isinstance(value, dict) or set(value) - {"hot", "warm"}:
                out.append("lead_scoring.bands takes only hot and warm")
            elif any(
                not isinstance(v, int) or not 0 <= v <= 100 for v in value.values()
            ):
                out.append(
                    "lead_scoring.bands values must be whole numbers from 0 to 100"
                )
        elif not isinstance(value, int) or isinstance(value, bool) or value < 0:
            out.append(f"lead_scoring.{key} must be a whole number of 0 or more")
    if out:
        return out
    r = scoring_rules(cfg)
    if r["engagement_max"] + r["intent_max"] + r["program_fit"] > 100:
        out.append(
            "lead_scoring: engagement_max + intent_max + program_fit must not exceed 100"
        )
    if r["bands"]["hot"] <= r["bands"]["warm"]:
        out.append("lead_scoring.bands.hot must be above bands.warm")
    if r["prompt_after_answers"] < 1:
        out.append("lead_scoring.prompt_after_answers must be at least 1")
    return out


def scoring_rules(cfg: dict | None) -> dict:
    """The defaults with the tenant's own values over them (call scoring_problems first)."""
    rules = {**DEFAULT_SCORING, "bands": dict(DEFAULT_SCORING["bands"])}
    for key, value in (cfg or {}).items():
        if key == "bands" and isinstance(value, dict):
            rules["bands"].update(value)
        elif key in rules:
            rules[key] = value
    return rules


def score_lead(
    user_turns: int,
    intents: list[str],
    program_codes: list[str],
    asked_human: bool,
    rules: dict | None = None,
) -> dict:
    """Rule-based score (F5.4) from engagement, intent signals and program fit, banded."""
    r = rules or DEFAULT_SCORING
    engagement = min(r["engagement_max"], user_turns * r["engagement_per_turn"])
    intent = min(
        r["intent_max"],
        r["intent_per_signal"] * len(HIGH_INTENT & set(intents))
        + (r["asked_human_bonus"] if asked_human else 0),
    )
    fit = r["program_fit"] if program_codes else 0
    score = min(100, engagement + intent + fit)
    band = (
        "hot"
        if score >= r["bands"]["hot"]
        else "warm"
        if score >= r["bands"]["warm"]
        else "cold"
    )
    return {
        "score": score,
        "band": band,
        "parts": {"engagement": engagement, "intent": intent, "fit": fit},
    }


def should_prompt(
    state: dict,
    answered: bool,
    sensitive: bool,
    after: int = DEFAULT_SCORING["prompt_after_answers"],
) -> bool:
    """Ask for consent/contact once, after enough answered questions (two by default)."""
    if (
        state.get("lead_prompted")
        or state.get("lead_created")
        or sensitive
        or not answered
    ):
        return False
    if state.get("is_minor") and not state.get("guardian_verified"):
        return False
    return state.get("answered_turns", 0) >= after
