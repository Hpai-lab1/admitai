"""Program catalogue (PRD F4.3): the authoritative source of every fee, date, intake and duration.
Values are stored and returned exactly as given; nothing is rounded or reformatted."""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation

import psycopg

LEVELS = {"UG", "PG", "PhD", "Diploma"}
PROGRAM_COLS = [
    "code",
    "name",
    "department",
    "level",
    "duration_months",
    "intake",
    "eligibility",
    "academic_year",
]
FEE_COLS = ["program_code", "category", "component", "amount"]
DATE_COLS = ["label", "date", "academic_year"]


class CatalogueError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _read(text: str, name: str, required: list[str], problems: list[str]) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    missing = [c for c in required if c not in (reader.fieldnames or [])]
    if missing:
        problems.append(f"{name}: missing column(s) {', '.join(missing)}")
        return []
    return [{k: (v or "").strip() for k, v in row.items() if k} for row in reader]


def _int(v: str, where: str, problems: list[str]) -> int | None:
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        problems.append(f"{where}: '{v}' is not a whole number")
        return None


def _date(v: str, where: str, problems: list[str]) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        problems.append(f"{where}: '{v}' is not a date (use YYYY-MM-DD)")
        return None


def parse_catalogue(programs_csv: str, fees_csv: str = "", dates_csv: str = "") -> dict:
    """Validate the three CSV templates. Raises CatalogueError listing every problem at once."""
    problems: list[str] = []
    programs = _read(programs_csv, "programs.csv", PROGRAM_COLS, problems)
    fees = _read(fees_csv, "fees.csv", FEE_COLS, problems) if fees_csv.strip() else []
    dates = (
        _read(dates_csv, "dates.csv", DATE_COLS, problems) if dates_csv.strip() else []
    )
    codes = set()
    for n, p in enumerate(programs, start=2):
        where = f"programs.csv line {n}"
        if not p["code"] or not p["name"]:
            problems.append(f"{where}: code and name are required")
        if p["code"] in codes:
            problems.append(f"{where}: duplicate code {p['code']}")
        codes.add(p["code"])
        if p["level"] not in LEVELS:
            problems.append(
                f"{where}: level must be one of {', '.join(sorted(LEVELS))}"
            )
        if not p["academic_year"]:
            problems.append(f"{where}: academic_year is required")
        p["duration_months"] = _int(p["duration_months"], where, problems)
        p["intake"] = _int(p["intake"], where, problems)
        p["aliases"] = [a.strip() for a in p.get("aliases", "").split("|") if a.strip()]
    for n, f in enumerate(fees, start=2):
        where = f"fees.csv line {n}"
        if f["program_code"] not in codes:
            problems.append(f"{where}: unknown program_code {f['program_code']!r}")
        try:
            f["amount"] = Decimal(f["amount"].replace(",", ""))
        except InvalidOperation:
            problems.append(f"{where}: amount {f['amount']!r} is not a number")
        f["valid_from"] = _date(f.get("valid_from", ""), where, problems)
        f["valid_to"] = _date(f.get("valid_to", ""), where, problems)
    for n, d in enumerate(dates, start=2):
        where = f"dates.csv line {n}"
        if d.get("program_code") and d["program_code"] not in codes:
            problems.append(f"{where}: unknown program_code {d['program_code']!r}")
        d["on_date"] = _date(d["date"], where, problems)
        if d["on_date"] is None and d["date"] == "":
            problems.append(f"{where}: date is required")
    if problems:
        raise CatalogueError(problems)
    return {"programs": programs, "fees": fees, "dates": dates}


def import_catalogue(cur: psycopg.Cursor, tenant_id: str, data: dict) -> dict:
    """Replace the catalogue for the academic years in the import (idempotent re-import)."""
    years = sorted(
        {p["academic_year"] for p in data["programs"]}
        | {d["academic_year"] for d in data["dates"]}
    )
    cur.execute("delete from kb.program_dates where academic_year = any(%s)", (years,))
    cur.execute("delete from kb.programs where academic_year = any(%s)", (years,))
    ids: dict[str, str] = {}
    for p in data["programs"]:
        cur.execute(
            "insert into kb.programs (tenant_id, code, name, aliases, department, level, duration_months,"
            " intake, eligibility, contact, academic_year) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
            (
                tenant_id,
                p["code"],
                p["name"],
                p["aliases"],
                p["department"] or None,
                p["level"],
                p["duration_months"],
                p["intake"],
                p["eligibility"] or None,
                p.get("contact") or None,
                p["academic_year"],
            ),
        )
        ids[p["code"]] = cur.fetchone()["id"]
    for f in data["fees"]:
        cur.execute(
            "insert into kb.program_fees (tenant_id, program_id, category, component, amount, currency,"
            " period, valid_from, valid_to) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                tenant_id,
                ids[f["program_code"]],
                f["category"] or "general",
                f["component"] or "tuition",
                f["amount"],
                (f.get("currency") or "INR").upper(),
                f.get("period") or "per year",
                f["valid_from"],
                f["valid_to"],
            ),
        )
    for d in data["dates"]:
        cur.execute(
            "insert into kb.program_dates (tenant_id, program_id, label, on_date, academic_year)"
            " values (%s,%s,%s,%s,%s)",
            (
                tenant_id,
                ids.get(d.get("program_code") or ""),
                d["label"],
                d["on_date"],
                d["academic_year"],
            ),
        )
    return {
        "programs": len(data["programs"]),
        "fees": len(data["fees"]),
        "dates": len(data["dates"]),
        "academic_years": years,
    }


def _amount(v: Decimal) -> str:
    """Exact amount as stored, without trailing .00 noise: 250000.00 -> '250000', 1250.50 -> '1250.50'."""
    return str(v.quantize(Decimal(1))) if v == v.to_integral_value() else str(v)


def _program_facts(cur: psycopg.Cursor, p: dict, today: date) -> dict:
    cur.execute(
        "select category, component, amount, currency, period, valid_from, valid_to from kb.program_fees"
        " where program_id = %s and (valid_to is null or valid_to >= %s) order by category, component",
        (p["id"], today),
    )
    fees = [
        {
            **f,
            "amount": _amount(f["amount"]),
            "valid_from": f["valid_from"].isoformat() if f["valid_from"] else None,
            "valid_to": f["valid_to"].isoformat() if f["valid_to"] else None,
        }
        for f in cur.fetchall()
    ]
    cur.execute(
        "select label, on_date from kb.program_dates where (program_id = %s or program_id is null)"
        " and academic_year = %s order by on_date",
        (p["id"], p["academic_year"]),
    )
    dates = [
        {"label": d["label"], "date": d["on_date"].isoformat()} for d in cur.fetchall()
    ]
    return {
        "id": str(p["id"]),
        "code": p["code"],
        "name": p["name"],
        "aliases": p["aliases"],
        "department": p["department"],
        "level": p["level"],
        "duration_months": p["duration_months"],
        "intake": p["intake"],
        "eligibility": p["eligibility"],
        "contact": p["contact"],
        "academic_year": p["academic_year"],
        "fees": fees,
        "dates": dates,
        "citation": {
            "source_type": "catalogue",
            "title": f"Program catalogue {p['academic_year']}: {p['name']}",
            "academic_year": p["academic_year"],
        },
    }


def list_programs(
    cur: psycopg.Cursor,
    academic_year: str | None = None,
    with_facts: bool = False,
    today: date | None = None,
) -> list[dict]:
    cur.execute(
        "select * from kb.programs where status = 'approved' and (%s::text is null or academic_year = %s)"
        " order by level, name",
        (academic_year, academic_year),
    )
    rows = cur.fetchall()
    if with_facts:
        return [_program_facts(cur, p, today or date.today()) for p in rows]
    return [
        {
            "id": str(p["id"]),
            "code": p["code"],
            "name": p["name"],
            "aliases": p["aliases"],
            "department": p["department"],
            "level": p["level"],
            "academic_year": p["academic_year"],
        }
        for p in rows
    ]


def program_facts(
    cur: psycopg.Cursor, ref: str, today: date | None = None
) -> dict | None:
    """ref is the program id, code or an alias such as "CSE" (latest academic year)."""
    cur.execute(
        "select * from kb.programs where status = 'approved' and (id::text = %s or lower(code) = lower(%s)"
        " or exists (select 1 from unnest(aliases) a where lower(a) = lower(%s)))"
        " order by academic_year desc, (lower(code) = lower(%s)) desc limit 1",
        (ref, ref, ref, ref),
    )
    p = cur.fetchone()
    return _program_facts(cur, p, today or date.today()) if p else None


def general_dates(cur: psycopg.Cursor, academic_year: str | None = None) -> list[dict]:
    cur.execute(
        "select label, on_date, academic_year from kb.program_dates where program_id is null"
        " and (%s::text is null or academic_year = %s) order by on_date",
        (academic_year, academic_year),
    )
    return [
        {
            "label": d["label"],
            "date": d["on_date"].isoformat(),
            "academic_year": d["academic_year"],
        }
        for d in cur.fetchall()
    ]
