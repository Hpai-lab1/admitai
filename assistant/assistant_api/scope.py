"""A question about one programme is answered from that programme's sources and the college-wide
ones, never from another programme's page.

"What is the fee for BCA?" used to cite the MCA programme page now and then (similar wording,
similar numbers). When the turn is about exactly one programme, a search result whose title names
other programmes, and not this one, is left out. Sources that name no programme (hostel rules,
scholarships, admission FAQs) are shared by all programmes and always stay.
"""

from __future__ import annotations

from .router import match_programs

# More results are asked for when some may be left out, so the answer still gets enough sources.
FOCUSED_TOP_K = 10


def about_other_programmes(hit: dict, code: str, programs: list[dict]) -> bool:
    """True when the result's title names programmes and the focused one is not among them."""
    title = (hit.get("citation") or {}).get("title") or ""
    named = {p["code"] for p in match_programs(title, programs)}
    return bool(named) and code not in named


def focus(hits: list[dict], code: str | None, programs: list[dict]) -> list[dict]:
    """The results that may be cited for a turn about programme `code` (all of them if None)."""
    if not code:
        return hits
    return [h for h in hits if not about_other_programmes(h, code, programs)]
