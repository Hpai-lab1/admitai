"""Load the knowledge part of a Tenant Pack (PRD F10.1) from a folder:

    pack.yaml                      college profile, branding, rules (read by Module C)
    catalogue/programs.csv         F4.3 templates (fees.csv, dates.csv optional)
    curated_qa.csv                 question, variants (| separated), answer, category, owner_role,
                                   department, academic_year, valid_to, status, external_ref
    documents/documents.yaml       list of {file, title, academic_year, department, owner, valid_to, status}
    documents/<files>

python -m knowledge_api.pack load packs/demo-college --tenant-id <uuid>

Loading is idempotent: curated answers are matched by external_ref and documents by checksum.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import yaml

from . import catalogue, curated, documents, search
from .config import Settings
from .db import tx
from .embedder import Embedder, embed_ml, make_embedder


def load_knowledge(
    settings: Settings, embedder: Embedder, tenant_id: str, pack: Path
) -> dict:
    report: dict = {"catalogue": None, "curated_qa": 0, "documents": 0, "skipped": []}
    cat = pack / "catalogue"
    if (cat / "programs.csv").exists():

        def read(name: str) -> str:
            path = cat / name
            return path.read_text(encoding="utf-8-sig") if path.exists() else ""

        data = catalogue.parse_catalogue(
            read("programs.csv"), read("fees.csv"), read("dates.csv")
        )
        with tx(settings.database_url, tenant_id) as cur:
            report["catalogue"] = catalogue.import_catalogue(cur, tenant_id, data)

    qa_file = pack / "curated_qa.csv"
    if qa_file.exists():
        rows = list(csv.DictReader(qa_file.open(encoding="utf-8-sig")))
        with tx(settings.database_url, tenant_id) as cur:
            for row in rows:
                status = row.get("status") or "approved"
                data = {
                    "question": row["question"],
                    "variants": [
                        v for v in (row.get("variants") or "").split("|") if v.strip()
                    ],
                    "answer": row["answer"],
                    "category": row.get("category") or None,
                    "owner_role": row.get("owner_role") or "admission_officer",
                    "department": row.get("department") or None,
                    "academic_year": row["academic_year"],
                    "valid_to": row["valid_to"],
                    "external_ref": row.get("external_ref") or None,
                }
                curated.create(
                    cur, embedder, tenant_id, data, actor="tenant-pack", status=status
                )
                report["curated_qa"] += 1

    manifest = pack / "documents" / "documents.yaml"
    if manifest.exists():
        for entry in yaml.safe_load(manifest.read_text(encoding="utf-8")) or []:
            path = pack / "documents" / entry["file"]
            data = path.read_bytes()
            with tx(settings.database_url, tenant_id) as cur:
                # Re-loading a pack must not resurrect documents the college has since expired.
                cur.execute(
                    "select 1 from kb.documents where checksum = %s",
                    (documents.checksum(data),),
                )
                if cur.fetchone():
                    report["skipped"].append(f"{entry['file']}: already loaded")
                    continue
                row, duplicate = documents.create(
                    cur, tenant_id, path.name, data, entry
                )
            if duplicate:
                report["skipped"].append(f"{entry['file']}: already loaded")
                continue
            row = documents.process(
                settings, embedder, tenant_id, str(row["id"]), path.name, data
            )
            wanted = entry.get("status", "approved")
            if wanted in ("approved", "expired") and row["status"] == "draft":
                with tx(settings.database_url, tenant_id) as cur:
                    if wanted == "approved":
                        documents.act(
                            cur, str(row["id"]), "approve", actor="tenant-pack"
                        )
                    else:
                        documents.act(
                            cur,
                            str(row["id"]),
                            "expire",
                            actor="tenant-pack",
                            reason=entry.get("reason", "superseded"),
                        )
            report["documents"] += 1
    from . import gate

    report["eval_cases"] = gate.load_cases(settings, tenant_id, pack)
    from . import catalogue_cases

    report["catalogue_cases"] = catalogue_cases.generate(settings, tenant_id)
    search.invalidate(tenant_id)
    return report


def reembed_missing(settings: Settings, embedder: Embedder, tenant_id: str) -> dict:
    """Fill multilingual vectors for content stored before the partner model was configured."""
    if embedder.ml is None:
        return {
            "chunks": 0,
            "curated_qa": 0,
            "note": "no multilingual model configured",
        }
    done = {}
    with tx(settings.database_url, tenant_id) as cur:
        cur.execute(
            "select id, heading_path || E'\\n' || content as text from kb.chunks where embedding_ml is null"
        )
        chunks = cur.fetchall()
        cur.execute(
            "select id, kb.qa_text(question, variants, answer) as text from kb.curated_qa"
            " where embedding_ml is null"
        )
        qas = cur.fetchall()
    for table, rows in (("chunks", chunks), ("curated_qa", qas)):
        vectors = embed_ml(embedder, [r["text"].strip() for r in rows])
        with tx(settings.database_url, tenant_id) as cur:
            for r, v in zip(rows, vectors):
                cur.execute(
                    f"update kb.{table} set embedding_ml = %s where id = %s",
                    (v, r["id"]),
                )
        done[table] = len(rows)
    search.invalidate(tenant_id)
    return done


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    load = sub.add_parser("load")
    load.add_argument("pack", type=Path)
    load.add_argument("--tenant-id", required=True)
    reembed = sub.add_parser("reembed", help="fill missing multilingual vectors")
    reembed.add_argument("--tenant-id", required=True)
    args = ap.parse_args(argv)
    settings = Settings()
    if not settings.database_url:
        sys.exit("Set KB_DATABASE_URL (source local.env.sh)")
    embedder = make_embedder(settings)
    if args.cmd == "reembed":
        print(reembed_missing(settings, embedder, args.tenant_id))
    else:
        print(load_knowledge(settings, embedder, args.tenant_id, args.pack))


if __name__ == "__main__":
    main(sys.argv[1:])
