"""Lead score bands. Module B owns the scoring rules (PRD F5.4) and normally sends the score;
this fallback only applies when it does not, so the lead list still sorts sensibly."""

from __future__ import annotations


def fallback_score(
    program_interest: list[str],
    has_phone: bool,
    has_email: bool,
    has_qualification: bool,
    wants_callback: bool,
) -> int:
    score = 20
    score += 15 if program_interest else 0
    score += 15 if has_phone else 0
    score += 10 if has_email else 0
    score += 10 if has_qualification else 0
    score += 20 if wants_callback else 0
    return min(score, 100)


def band(score: int) -> str:
    """PRD F5.4: hot / warm / cold bands."""
    if score >= 70:
        return "hot"
    if score >= 40:
        return "warm"
    return "cold"
