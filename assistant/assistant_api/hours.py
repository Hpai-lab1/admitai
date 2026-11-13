"""Office hours (PRD F3.1: rules decide first, including office hours; F3.4 callback-first at all
hours). Outside office hours a handoff is still a callback, but the visitor is told when the team
will be back, and the handoff event says so for Module C.

The Tenant Pack gives office hours as text, e.g. "Mon-Sat 09:00-17:00" or
"Mon-Fri 09:30-17:30, Sat 09:00-13:00", and optionally `timezone` (IANA; default Asia/Kolkata).
Text that cannot be read means "no office-hours rule" rather than "always closed".
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DEFAULT_TZ = "Asia/Kolkata"
_PART = re.compile(
    r"(?P<d1>mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?(?:\s*(?:-|–|to)\s*(?P<d2>mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?)?"
    r"\s*,?\s*(?P<h1>\d{1,2})[:.](?P<m1>\d{2})\s*(?:-|–|to)\s*(?P<h2>\d{1,2})[:.](?P<m2>\d{2})",
    re.I,
)

Schedule = dict[int, tuple[time, time]]


def parse(text: str | None) -> Schedule:
    """{weekday: (opens, closes)}; empty when nothing can be read."""
    out: Schedule = {}
    for m in _PART.finditer(text or ""):
        a = DAYS.index(m["d1"].lower()[:3])
        b = DAYS.index(m["d2"].lower()[:3]) if m["d2"] else a
        try:
            opens = time(int(m["h1"]), int(m["m1"]))
            closes = time(int(m["h2"]), int(m["m2"]))
        except ValueError:
            continue
        if opens >= closes:
            continue
        day = a
        while True:
            out[day] = (opens, closes)
            if day == b:
                break
            day = (day + 1) % 7
    return out


def zone(name: str | None) -> tzinfo:
    """The college's time zone. Windows has no system time-zone database: the `tzdata` package
    (in requirements.txt) provides it; without it, UTC rather than a crash on every handoff."""
    for candidate in (name, DEFAULT_TZ):
        try:
            return ZoneInfo(candidate or DEFAULT_TZ)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return timezone.utc


def zone_name(z: tzinfo) -> str:
    return getattr(z, "key", None) or "UTC"


def is_open(schedule: Schedule, now: datetime) -> bool:
    hours = schedule.get(now.weekday())
    return bool(hours) and hours[0] <= now.time() < hours[1]


def next_opening(schedule: Schedule, now: datetime) -> datetime | None:
    if not schedule:
        return None
    for ahead in range(8):
        day = now + timedelta(days=ahead)
        hours = schedule.get(day.weekday())
        if not hours:
            continue
        opens = day.replace(
            hour=hours[0].hour, minute=hours[0].minute, second=0, microsecond=0
        )
        if opens > now:
            return opens
    return None


def status(
    office_hours: str | None, tz: str | None, now: datetime | None = None
) -> dict | None:
    """{"open": bool, "hours": text, "next_open": iso or None, "timezone": name}, or None when the
    college has no readable office hours."""
    schedule = parse(office_hours)
    if not schedule:
        return None
    z = zone(tz)
    local = (now or datetime.now(z)).astimezone(z)
    open_now = is_open(schedule, local)
    nxt = None if open_now else next_opening(schedule, local)
    return {
        "open": open_now,
        "hours": office_hours,
        "next_open": nxt.isoformat() if nxt else None,
        "timezone": zone_name(z),
    }


def spoken_time(iso: str, lang: str = "en", now: datetime | None = None) -> str:
    """'today at 9:00', 'tomorrow at 9:00' or 'Monday at 9:00' (local time of the college)."""
    at = datetime.fromisoformat(iso)
    ref = (now or datetime.now(at.tzinfo)).astimezone(at.tzinfo)
    clock = f"{at.hour}:{at.minute:02d}"
    days = (at.date() - ref.date()).days
    if lang == "hi":
        day = {0: "आज", 1: "कल"}.get(days) or [
            "सोमवार",
            "मंगलवार",
            "बुधवार",
            "गुरुवार",
            "शुक्रवार",
            "शनिवार",
            "रविवार",
        ][at.weekday()]
        return f"{day} {clock} बजे"
    if lang == "kn":
        day = {0: "ಇಂದು", 1: "ನಾಳೆ"}.get(days) or [
            "ಸೋಮವಾರ",
            "ಮಂಗಳವಾರ",
            "ಬುಧವಾರ",
            "ಗುರುವಾರ",
            "ಶುಕ್ರವಾರ",
            "ಶನಿವಾರ",
            "ಭಾನುವಾರ",
        ][at.weekday()]
        return f"{day} {clock} ಕ್ಕೆ"
    day = {0: "today", 1: "tomorrow"}.get(days) or at.strftime("%A")
    return f"{day} at {clock}"
