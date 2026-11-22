"""Curriculum summaries (PRD F4.2, extended): what share of a programme is mathematics?

A programme page that lists its courses semester by semester, with a credits column, says how
much of the programme each subject takes, but only as dozens of table rows spread over many
chunks; no single search result holds the answer to "what percentage of the B.Tech. in
Mathematics and Computing is maths?". This module adds those numbers up into one short summary
document that search can find:

- total credits, and credits per semester (or year);
- credits per subject area, with the share of the programme and the courses counted;
- elective slots and projects, which are counted separately because their subject varies.

Every figure is a sum of credits the page states; the summary names its source page and says
how it counted, so staff can check it. Subject areas come from course titles ("Linear Algebra" is
mathematics), not from anything about one college.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .ingest import Block, Table, tables

# Subject areas, tried in order; the first whose pattern matches the course title wins. Projects
# and electives first: "Project Work in Machine Learning" is project work, not a computing course.
AREAS: list[tuple[str, re.Pattern]] = [
    (
        "Projects, internships and seminars",
        re.compile(r"project|internship|dissertation|thesis|seminar|capstone", re.I),
    ),
    (
        "Electives (subject chosen by the student)",
        re.compile(r"elective|online course|innovation course|minor\b", re.I),
    ),
    (
        "Mathematics and statistics",
        re.compile(
            r"math|statistic|calculus|algebra|probabilit|numerical|stochastic|"
            r"differential equation|integral transform|optimi[sz]ation|graph theory|"
            r"number theory|real analysis|complex analysis|topology|linear programming|"
            r"operations research|biostatistic|quantitative (techniques|methods|aptitude)|"
            r"measure theory|functional analysis|differential geometry|geometry|fluid dynamics|"
            r"mathematical|fourier|tensor|game theory|actuarial|econometric",
            re.I,
        ),
    ),
    (
        "Computing",
        re.compile(
            r"comput|programming|software|algorithm|data struct|database|operating system|"
            r"network|machine learning|artificial intelligence|deep learning|cyber|"
            r"information security|cryptograph|\bweb\b|cloud|data science|data analytics|"
            r"data mining|logic design|microprocessor|automat|formal language|python|java|coding",
            re.I,
        ),
    ),
    (
        "Sciences",
        re.compile(
            r"physic|chemi|biolog|life science|biochem|microbio|anatomy|physiol", re.I
        ),
    ),
    (
        "Humanities, communication and management",
        re.compile(
            r"communicat|english|language|\blaw\b|constitution|ethic|environment|entrepreneur|"
            r"management|economic|innovation|soft skill|human rights|psycholog|sociolog|kannada",
            re.I,
        ),
    ),
    (
        "Engineering",
        re.compile(r"engineering|mechanic|drawing|electr|design|workshop", re.I),
    ),
]
OTHER = "Other subjects"

_PERIOD = re.compile(
    r"\b(semester|sem|term|trimester|year)\s*[-:.]?\s*(\d{1,2}|[ivx]{1,4})\b"
    r"|\b(\d{1,2})(?:st|nd|rd|th)\s+(semester|sem|term|trimester|year)\b",
    re.I,
)
_ROMAN = {
    "i": 1,
    "ii": 2,
    "iii": 3,
    "iv": 4,
    "v": 5,
    "vi": 6,
    "vii": 7,
    "viii": 8,
    "ix": 9,
    "x": 10,
}


@dataclass
class Course:
    code: str
    title: str
    credits: Decimal


@dataclass
class Period:
    heading: str  # "Semester 1 (Physics Cycle)"
    number: int
    unit: str  # "Semester"
    variant: str  # "Physics Cycle", or "" when the period has one table
    courses: list[Course] = field(default_factory=list)

    @property
    def credits(self) -> Decimal:
        return sum((c.credits for c in self.courses), Decimal(0))


def _number(text: str) -> tuple[int, str] | None:
    m = _PERIOD.search(text)
    if not m:
        return None
    if m.group(1):
        raw, unit = m.group(2).lower(), m.group(1)
    else:
        raw, unit = m.group(3), m.group(4)
    n = int(raw) if raw.isdigit() else _ROMAN.get(raw)
    if not n:
        return None
    unit = {"sem": "semester"}.get(unit.lower(), unit.lower())
    return n, unit.capitalize()


def _variant(heading: str) -> str:
    m = re.search(r"\(([^)]+)\)", heading)
    if m:
        return m.group(1).strip()
    rest = _PERIOD.sub(" ", heading)
    return " ".join(re.sub(r"[-–:|]", " ", rest).split())


def _column(table: Table, pattern: str, avoid: str | None = None) -> int | None:
    cols = [
        c
        for c, h in table.header.items()
        if re.search(pattern, h, re.I) and not (avoid and re.search(avoid, h, re.I))
    ]
    if not cols:
        return None
    total = [c for c in cols if re.search(r"total", table.header[c], re.I)]
    return (total or cols)[-1] if pattern == "credit" else cols[0]


def _credits(text: str) -> Decimal | None:
    m = re.fullmatch(r"\s*(\d{1,2}(?:\.\d+)?)\s*", text or "")
    if not m:
        return None
    try:
        return Decimal(m.group(1))
    except InvalidOperation:
        return None


def periods(blocks: list[Block]) -> list[Period]:
    """Every table with a course-title and a credits column, under a heading that names a
    semester or year ("Semester 3", "II Year", "1st Semester")."""
    out: list[Period] = []
    for start, table in tables(blocks):
        heading = next(
            (
                b.text.strip()
                for b in reversed(blocks[:start])
                if b.level and b.text.strip()
            ),
            "",
        )
        found = _number(heading)
        cred = _column(table, "credit")
        title = _column(table, r"title|name|subject|paper|course", avoid=r"code|type")
        if not found or cred is None or title is None:
            continue
        code = _column(table, r"code")
        p = Period(heading, found[0], found[1], _variant(heading))
        for row in table.rows:
            cells = {col: text for col, span, text in row if span == 1}
            name, value = cells.get(title, "").strip(), _credits(cells.get(cred, ""))
            if not name or value is None or re.match(r"total\b", name, re.I):
                continue
            p.courses.append(
                Course(cells.get(code, "") if code is not None else "", name, value)
            )
        if p.courses:
            out.append(p)
    return out


def periods_without_credits(blocks: list[Block]) -> list[str]:
    """Headings of semester or year tables that list courses but give no credits."""
    out = []
    for start, table in tables(blocks):
        heading = next(
            (
                b.text.strip()
                for b in reversed(blocks[:start])
                if b.level and b.text.strip()
            ),
            "",
        )
        has_title = _column(
            table, r"title|name|subject|paper|course", avoid=r"code|type"
        )
        if (
            _number(heading)
            and has_title is not None
            and _column(table, "credit") is None
        ):
            out.append(heading)
    return out


def plan(found: list[Period]) -> list[Period]:
    """The tables one student takes. A semester shown as alternatives ("Physics Cycle",
    "Chemistry Cycle") is taken once; when the next semester offers the same alternatives, the
    student takes the other one there (one cycle in each), so they are assigned in turn."""
    by_number: dict[tuple[str, int], list[Period]] = {}
    for p in found:
        by_number.setdefault((p.unit, p.number), []).append(p)
    chosen: list[Period] = []
    turn: dict[tuple[str, ...], int] = {}
    for key in sorted(by_number, key=lambda k: (k[0], k[1])):
        options = by_number[key]
        if len(options) == 1:
            chosen.append(options[0])
            continue
        labels = tuple(sorted(o.variant.lower() for o in options))
        i = turn.get(labels, 0)
        chosen.append(options[i % len(options)])
        turn[labels] = i + 1
    return chosen


def area(title: str) -> str:
    for name, pattern in AREAS:
        if pattern.search(title):
            return name
    return OTHER


def _fmt(d: Decimal) -> str:
    return f"{d.normalize():f}"


def _pct(part: Decimal, whole: Decimal) -> str:
    return f"{(part * 100 / whole).quantize(Decimal('0.1'))}%"


def summary(
    name: str, url: str, blocks: list[Block], duration_months: int | None = None
) -> list[Block] | None:
    """The summary document's blocks, or None when the page has no curriculum table with
    credits for at least two semesters or years. With the programme's duration, a table that
    lists only some of its semesters says so instead of calling its sum the programme's total."""
    found = periods(blocks)
    taken = plan(found)
    if len({(p.unit, p.number) for p in taken}) < 2:
        return None
    total = sum((p.credits for p in taken), Decimal(0))
    if not total:
        return None
    by_area: dict[str, list[Course]] = {}
    for p in taken:
        for c in p.courses:
            by_area.setdefault(area(c.title), []).append(c)
    order = [a for a, _ in AREAS] + [OTHER]
    unit = taken[0].unit.lower()
    out = [Block(f"{name}: curriculum, credits and subject share", level=1)]

    maths = sum((c.credits for c in by_area.get(AREAS[2][0], [])), Decimal(0))
    per = {"semester": 6, "trimester": 4, "term": 6, "year": 12}.get(unit)
    expected = duration_months // per if duration_months and per else None
    listed = {p.number for p in taken}
    whole = "of the programme"
    no_credits = periods_without_credits(blocks)
    if no_credits:
        opening = (
            f"The curriculum of the {name} on the page gives credits for "
            f"{', '.join(p.heading for p in taken)} only ({_fmt(total)} credits); "
            f"{', '.join(no_credits)} list their courses without credits. The figures below "
            f"cover the {unit}s with credits only, not the whole programme. "
        )
        whole = f"of the {unit}s with credits"
    elif expected and not set(range(1, expected + 1)) <= listed:
        opening = (
            f"The curriculum table on the page lists only {len(listed)} of the {expected} "
            f"{unit}s of the {name} ({', '.join(p.heading for p in taken)}), with "
            f"{_fmt(total)} credits; the other {unit}s are not listed, so the figures below "
            f"cover the listed {unit}s only, not the whole programme. "
        )
        whole = f"of the listed {unit}s"
    elif expected:
        opening = (
            f"The {name} has {_fmt(total)} credits in total over {len(taken)} {unit}s. "
        )
    else:
        opening = (
            f"The curriculum of the {name} on the page lists {len(taken)} {unit}s with "
            f"{_fmt(total)} credits in total. "
        )
    out.append(
        Block(
            opening
            + f"Mathematics and statistics (maths) courses carry {_fmt(maths)} of the "
            f"{_fmt(total)} credits, which is {_pct(maths, total)} {whole}"
            + (
                ", not counting electives, whose subject the student chooses."
                if by_area.get(AREAS[1][0])
                else "."
            )
        )
    )
    out.append(Block("Credits by subject area", level=2))
    for a in order:
        courses = by_area.get(a)
        if not courses:
            continue
        cr = sum((c.credits for c in courses), Decimal(0))
        listed = ", ".join(f"{c.title} ({_fmt(c.credits)})" for c in courses)
        out.append(
            Block(
                f"{a}: {_fmt(cr)} of {_fmt(total)} credits ({_pct(cr, total)}). Courses: {listed}."
            )
        )
    out.append(Block(f"Credits per {unit}", level=2))
    for p in taken:
        n = len(p.courses)
        out.append(
            Block(
                f"{p.heading}: {_fmt(p.credits)} credits, {n} course{'s' if n != 1 else ''}."
            )
        )
    alternatives = [p for p in found if all(p is not t for t in taken)]
    note = (
        f"The credits are added up from the curriculum table on {url}. "
        "Subject areas are read from the course titles."
    )
    if alternatives:
        other = ", ".join(
            f"{p.heading} ({_fmt(p.credits)} credits)" for p in alternatives
        )
        note += (
            " Where a semester is offered as alternatives (cycles), a student takes one of them;"
            f" counted above: {', '.join(p.heading for p in taken if p.variant)}. Not counted: {other}."
        )
    out.append(Block("How this was counted", level=2))
    out.append(Block(note))
    return out
