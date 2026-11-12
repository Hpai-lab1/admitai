"""Tenant Pack import, validation and readiness (PRD F10.1-F10.3, F10.6, 17.2).

A pack is a zip of the folder layout in packs/<slug>/ (pack.yaml, catalogue/, curated_qa.csv,
documents/, eval/questions.csv). Everything is validated before anything is loaded: a pack with
problems is rejected with the full list, and nothing is half-imported.
"""

from __future__ import annotations

import csv
import io
import tempfile
import zipfile
from datetime import date
from pathlib import Path, PurePosixPath

import yaml

from . import catalogue, ingest, pack
from .config import Settings
from .db import tx
from .embedder import Embedder

MAX_PACK_BYTES = 200 * 1024 * 1024
MAX_FILES = 500
ALLOWED_TOP = {"pack.yaml", "catalogue", "curated_qa.csv", "documents", "eval"}
CURATED_COLS = {"question", "answer", "academic_year", "valid_to"}
EVAL_COLS = {"id", "question", "kind"}
PRD_MIN_TEST_QUESTIONS = (
    150  # F10.3: at least 150 real questions from the college's own enquiries
)


class PackError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def unpack(data: bytes, into: Path) -> Path:
    """Extract safely: size and count limits, no absolute paths or '..' (zip slip), no links."""
    if len(data) > MAX_PACK_BYTES:
        raise PackError(["The pack is larger than 200 MB."])
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PackError(["The upload is not a zip file."]) from None
    names = [
        n
        for n in zf.namelist()
        if not n.endswith("/")
        and "__MACOSX" not in n
        and not PurePosixPath(n).name.startswith(".")
    ]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise PackError([f"The same file appears twice in the zip: {', '.join(dupes)}"])
    if len(names) > MAX_FILES:
        raise PackError([f"The pack has more than {MAX_FILES} files."])
    # A pack zipped from its folder has one top-level directory; accept both layouts.
    tops = {PurePosixPath(n).parts[0] for n in names}
    prefix = (
        tops.pop()
        if len(tops) == 1
        and not any(PurePosixPath(n).parts[0] in ALLOWED_TOP for n in names)
        else ""
    )
    problems = []
    for name in names:
        p = PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts:
            problems.append(f"Unsafe path in the zip: {name}")
            continue
        info = zf.getinfo(name)
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            problems.append(f"Links are not allowed in a pack: {name}")
            continue
        rel = PurePosixPath(*p.parts[1:]) if prefix else p
        if not rel.parts or rel.parts[0] not in ALLOWED_TOP:
            problems.append(f"Unexpected file (not part of a Tenant Pack): {name}")
            continue
        target = into / Path(*rel.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zf.read(name))
    if problems:
        raise PackError(problems)
    return into


def validate(root: Path) -> dict:
    """Check every part of the pack; raises PackError listing every problem at once."""
    problems: list[str] = []
    meta = {}
    if not (root / "pack.yaml").exists():
        problems.append("pack.yaml is missing.")
    else:
        try:
            meta = (
                yaml.safe_load((root / "pack.yaml").read_text(encoding="utf-8")) or {}
            )
        except yaml.YAMLError as exc:
            problems.append(f"pack.yaml is not valid YAML ({exc.__class__.__name__}).")
        college = meta.get("college") or {}
        for key in ("slug", "name", "academic_year"):
            if not college.get(key):
                problems.append(f"pack.yaml: college.{key} is required.")
        if college.get("slug") and not str(college["slug"]).replace("-", "").isalnum():
            problems.append(
                "pack.yaml: college.slug may contain only letters, digits and '-'."
            )
    cat = root / "catalogue"
    if (cat / "programs.csv").exists():

        def read(n: str) -> str:
            return (
                (cat / n).read_text(encoding="utf-8-sig") if (cat / n).exists() else ""
            )

        try:
            catalogue.parse_catalogue(
                read("programs.csv"), read("fees.csv"), read("dates.csv")
            )
        except catalogue.CatalogueError as exc:
            problems.extend(exc.problems)
    qa = root / "curated_qa.csv"
    if qa.exists():
        rows = list(csv.DictReader(qa.open(encoding="utf-8-sig")))
        missing = CURATED_COLS - set(rows[0].keys() if rows else [])
        if rows and missing:
            problems.append(
                f"curated_qa.csv: missing column(s) {', '.join(sorted(missing))}"
            )
        for n, r in enumerate(rows, start=2):
            if not missing and (not r["question"].strip() or not r["answer"].strip()):
                problems.append(
                    f"curated_qa.csv line {n}: question and answer are required"
                )
            if not missing:
                try:
                    date.fromisoformat(r["valid_to"])
                except ValueError:
                    problems.append(
                        f"curated_qa.csv line {n}: valid_to {r['valid_to']!r} is not a date"
                    )
    manifest = root / "documents" / "documents.yaml"
    if manifest.exists():
        entries = yaml.safe_load(manifest.read_text(encoding="utf-8")) or []
        for e in entries:
            f = root / "documents" / str(e.get("file", ""))
            if not e.get("file") or not f.exists():
                problems.append(
                    f"documents.yaml: file {e.get('file')!r} is not in the pack"
                )
                continue
            try:
                ingest.mime_for(f.name)
            except ingest.IngestError as exc:
                problems.append(f"documents/{f.name}: {exc}")
    ev = root / "eval" / "questions.csv"
    if ev.exists():
        rows = list(csv.DictReader(ev.open(encoding="utf-8-sig")))
        missing = EVAL_COLS - set(rows[0].keys() if rows else [])
        if missing:
            problems.append(
                f"eval/questions.csv: missing column(s) {', '.join(sorted(missing))}"
            )
    if problems:
        raise PackError(problems)
    return meta


def import_pack(
    settings: Settings, embedder: Embedder, tenant: str, data: bytes
) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = unpack(data, Path(tmp))
        meta = validate(root)
        report = pack.load_knowledge(settings, embedder, tenant, root)
        if embedder.ml is not None:
            report["multilingual"] = pack.reembed_missing(settings, embedder, tenant)
    return {
        "college": meta.get("college", {}),
        "loaded": report,
        "readiness": readiness(settings, tenant, meta.get("launch") or {}),
    }


def readiness(
    settings: Settings, tenant: str, launch: dict | None = None
) -> list[dict]:
    """The knowledge part of the readiness checklist (F10.2, F10.6, 17.2). Each item says what is
    missing; `blocking` items stop the launch."""
    launch = launch or {}
    items: list[dict] = []

    def item(
        key: str, label: str, ok: bool, detail: str = "", blocking: bool = True
    ) -> None:
        items.append(
            {
                "key": key,
                "label": label,
                "ok": ok,
                "detail": detail,
                "blocking": blocking,
            }
        )

    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select max(academic_year) as y from kb.programs where status = 'approved'"
        )
        year = cur.fetchone()["y"]
        cur.execute(
            "select p.name, p.eligibility, (select count(*) from kb.program_fees f where f.program_id = p.id) fees,"
            " (select count(*) from kb.program_dates d where d.program_id = p.id or d.program_id is null"
            "  and d.academic_year = p.academic_year) dates"
            " from kb.programs p where p.status = 'approved' and p.academic_year = %s",
            (year,),
        )
        programs = cur.fetchall()
        cur.execute("select count(*) n from kb.curated_qa where status = 'approved'")
        curated_n = cur.fetchone()["n"]
        cur.execute(
            "select title, academic_year, owner, department from kb.documents where status = 'approved'"
        )
        docs = cur.fetchall()
        cur.execute("select count(*) n from kb.curated_qa where status = 'suspended'")
        conflicts = cur.fetchone()["n"]
        cur.execute(
            "select count(*) filter (where source <> 'catalogue') n,"
            " count(*) filter (where critical) crit,"
            " count(*) filter (where source = 'catalogue') generated"
            " from learn.eval_cases where active"
        )
        ev = cur.fetchone()
    if programs:
        no_fees = [p["name"] for p in programs if not p["fees"]]
        no_dates = [p["name"] for p in programs if not p["dates"]]
        no_elig = [p["name"] for p in programs if not p["eligibility"]]
        item(
            "fees", f"Fees for every program ({year})", not no_fees, ", ".join(no_fees)
        )
        item(
            "dates",
            f"Important dates for every program ({year})",
            not no_dates,
            ", ".join(no_dates),
        )
        item(
            "eligibility",
            "Eligibility for every program",
            not no_elig,
            ", ".join(no_elig),
        )
    else:
        item(
            "catalogue",
            "Program catalogue",
            False,
            "No programs: answers about fees and dates will come only from documents and curated answers",
            blocking=False,
        )
    item(
        "knowledge",
        "Approved knowledge to answer from",
        bool(docs) or curated_n > 0,
        f"{len(docs)} documents, {curated_n} curated answers",
    )
    no_owner = [d["title"] for d in docs if not (d["owner"] or d["department"])]
    item(
        "owners",
        "An owner for every document (17.1)",
        not no_owner,
        ", ".join(no_owner),
    )
    if year:
        other = [
            d["title"]
            for d in docs
            if d["academic_year"] and d["academic_year"] != year
        ]
        item(
            "current_year",
            f"Documents are for the current academic year ({year})",
            not other,
            ", ".join(other),
            blocking=False,
        )
    item(
        "conflicts",
        "No curated answer disagrees with the catalogue",
        conflicts == 0,
        f"{conflicts} suspended",
    )
    need = int(launch.get("min_test_questions", PRD_MIN_TEST_QUESTIONS))
    # F10.3: catalogue-generated cases supplement the real questions; they do not count towards them.
    detail = (
        f"{ev['n']} questions, {ev['crit']} critical, plus {ev['generated']} generated from the"
        f" catalogue; needs {need} real questions"
    )
    if need < PRD_MIN_TEST_QUESTIONS:
        detail += f" (the PRD asks for {PRD_MIN_TEST_QUESTIONS} real questions before a real launch)"
    item("test_set", "Launch test set (F10.3)", ev["n"] >= need, detail)
    return items


def launch_verdict(
    scores: dict, results: list[dict], launch: dict | None = None
) -> dict:
    """F10.3: launch blocked until the test set passes the threshold and no critical case fails."""
    launch = launch or {}
    need_acc = float(launch.get("min_accuracy", 0.85))
    need_ref = float(launch.get("min_refusal", 0.9))
    reasons = []
    if (scores.get("accuracy") or 0) < need_acc:
        reasons.append(
            f"accuracy {scores.get('accuracy') or 0:.0%} is below {need_acc:.0%}"
        )
    if scores.get("refusal") is not None and scores["refusal"] < need_ref:
        reasons.append(f"refusals {scores['refusal']:.0%} are below {need_ref:.0%}")
    critical = [r["id"] for r in results if r["critical"] and not r["passed"]]
    if critical:
        reasons.append(f"critical cases failing: {', '.join(critical)}")
    return {"passed": not reasons, "reasons": reasons}
