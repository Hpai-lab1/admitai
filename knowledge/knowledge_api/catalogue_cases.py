"""Launch test cases generated from the program catalogue (PRD F10.3, 20: "supplemented by
catalogue-generated cases").

For every approved program of the current academic year: its fees (exact amount expected in the
answer), intake (exact number), eligibility and program dates (the catalogue must be cited), and
the college-wide dates. Cases are marked `source = 'catalogue'`; they never count towards the
150 real questions the PRD requires before a launch. Regenerating replaces them: cases for a
program or fee that no longer exists are switched off.
"""

from __future__ import annotations

from decimal import Decimal

from .config import Settings
from .db import tx

MAX_PER_PROGRAM = 6
MAX_CASES = 300
CATEGORY_WORDS = {
    "general": None,
    "management": "management quota",
    "nri": "NRI",
    "government": "government quota",
    "sc": "SC",
    "st": "ST",
    "obc": "OBC",
    "ews": "EWS",
}


def amount_text(amount: Decimal | float | int) -> str:
    """The amount as Module B quotes it (exact; the grader ignores thousands separators)."""
    d = Decimal(str(amount))
    return str(int(d)) if d == d.to_integral_value() else format(d.normalize(), "f")


def _fee_question(name: str, component: str, category: str) -> str:
    what = "fee" if component in ("tuition", "fee") else f"{component} fee"
    who = CATEGORY_WORDS.get(category, category.upper())
    q = f"What is the {what} for {name}"
    return q + (f" under the {who} category?" if who else "?")


def build(cur) -> list[dict]:
    cur.execute(
        "select max(academic_year) y from kb.programs where status = 'approved'"
    )
    year = cur.fetchone()["y"]
    if not year:
        return []
    cur.execute(
        "select id, code, name, intake, eligibility from kb.programs"
        " where status = 'approved' and academic_year = %s order by code",
        (year,),
    )
    programs = cur.fetchall()
    cur.execute(
        "select program_id, category, component, amount from kb.program_fees"
        " where program_id = any(%s) order by component <> 'tuition', category <> 'general', component, category",
        ([p["id"] for p in programs],),
    )
    fees: dict = {}
    for f in cur.fetchall():
        fees.setdefault(f["program_id"], []).append(f)
    cur.execute(
        "select program_id, label from kb.program_dates where academic_year = %s order by on_date",
        (year,),
    )
    dates = cur.fetchall()
    cases: list[dict] = []
    for p in programs:
        ref = f"catalogue:{p['code']}"
        mine: list[dict] = []
        for f in fees.get(p["id"], []):
            mine.append(
                {
                    "ext_id": f"cat-{p['code']}-fee-{f['component']}-{f['category']}",
                    "question": _fee_question(p["name"], f["component"], f["category"]),
                    "expected_refs": [ref],
                    "expected_facts": [amount_text(f["amount"])],
                    # A wrong fee is a P1 incident (18.1): the general tuition fee must never fail.
                    "critical": f["component"] == "tuition"
                    and f["category"] == "general",
                }
            )
        if p["intake"]:
            mine.append(
                {
                    "ext_id": f"cat-{p['code']}-intake",
                    "question": f"How many seats are there in {p['name']}?",
                    "expected_refs": [ref],
                    "expected_facts": [str(p["intake"])],
                    "critical": False,
                }
            )
        if p["eligibility"]:
            mine.append(
                {
                    "ext_id": f"cat-{p['code']}-eligibility",
                    "question": f"What is the eligibility for {p['name']}?",
                    "expected_refs": [ref],
                    "expected_facts": [],
                    "critical": False,
                }
            )
        for d in dates:
            if d["program_id"] == p["id"]:
                mine.append(
                    {
                        "ext_id": f"cat-{p['code']}-date-{_slug(d['label'])}",
                        "question": f"When is the '{d['label']}' date for {p['name']}?",
                        "expected_refs": [ref],
                        "expected_facts": [],
                        "critical": False,
                    }
                )
        cases += mine[:MAX_PER_PROGRAM]
    for d in dates:
        if d["program_id"] is None:
            cases.append(
                {
                    "ext_id": f"cat-general-date-{_slug(d['label'])}",
                    "question": f"When is the '{d['label']}' date?",
                    "expected_refs": ["catalogue:*"],
                    "expected_facts": [],
                    "critical": False,
                }
            )
    return cases[:MAX_CASES]


def _slug(text: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in text.lower())
    return "-".join(x for x in out.split("-") if x)[:40]


def generate(settings: Settings, tenant: str) -> dict:
    with tx(settings.database_url, tenant) as cur:
        cases = build(cur)
        for c in cases:
            cur.execute(
                "insert into learn.eval_cases (tenant_id, ext_id, question, kind, expected_refs, expected_facts,"
                " expected_role, critical, source, active) values (%s,%s,%s,'direct',%s,%s,'admission',%s,"
                " 'catalogue', true) on conflict (tenant_id, ext_id) do update set question = excluded.question,"
                " expected_refs = excluded.expected_refs, expected_facts = excluded.expected_facts,"
                " critical = excluded.critical, active = true"
                " where learn.eval_cases.source = 'catalogue'",
                (
                    tenant,
                    c["ext_id"],
                    c["question"],
                    c["expected_refs"],
                    c["expected_facts"],
                    c["critical"],
                ),
            )
        cur.execute(
            "update learn.eval_cases set active = false where source = 'catalogue' and active"
            " and not (ext_id = any(%s))",
            ([c["ext_id"] for c in cases],),
        )
        retired = cur.rowcount
    return {
        "generated": len(cases),
        "critical": sum(c["critical"] for c in cases),
        "retired": retired,
    }
