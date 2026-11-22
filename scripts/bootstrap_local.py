"""Set up the local prototype end to end (the guide's job on the lab servers).

    source local.env.sh
    python scripts/bootstrap_local.py [--fresh]

1. Migrations for Module C (app), A (kb, learn) and B (chat).
2. Module C seed: demo-college and other-college with staff; RUAS is added as a third tenant.
3. For each pack in packs/: copy branding and rules from pack.yaml into Module C's tenant config,
   load the knowledge into Module A (with multilingual vectors) and the agent settings into
   Module B, all under Module C's tenant id.

--fresh empties Modules A and B first (Module C's seed always resets its demo tenants).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
import yaml
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parent.parent
for module in ("experience", "knowledge", "assistant"):
    sys.path.insert(0, str(ROOT / module))

from assistant_api import migrate as chat_migrate  # noqa: E402
from assistant_api import pack as chat_pack  # noqa: E402
from assistant_api.config import Settings as ChatSettings  # noqa: E402
from experience_api import migrate as app_migrate  # noqa: E402
from experience_api import seed as app_seed  # noqa: E402
from experience_api.security import hash_password  # noqa: E402
from knowledge_api import migrate as kb_migrate  # noqa: E402
from knowledge_api import pack as kb_pack  # noqa: E402
from knowledge_api.config import Settings as KbSettings  # noqa: E402
from knowledge_api.embedder import make_embedder  # noqa: E402

# The RUAS pack is kept in the private prototype only; without it, only the demo college is set up.
HAS_RUAS = (Path(__file__).resolve().parents[1] / "packs/ruas/pack.yaml").exists()
PACKS = ["demo-college", "ruas"] if HAS_RUAS else ["demo-college"]
RUAS_STAFF = [
    ("admin@ruas.example", "RUAS Admin", "college_admin", []),
    ("officer@ruas.example", "RUAS Officer", "admission_officer", []),
    ("finance@ruas.example", "RUAS Finance", "faculty", ["Finance"]),
]


def c_config(pack: dict, existing: dict) -> dict:
    """The widget-facing part of a Tenant Pack, merged over what Module C already has."""
    b = pack.get("branding", {})
    cfg = dict(existing)
    cfg.update(
        {
            "assistant_name": b.get("assistant_name", cfg.get("assistant_name")),
            "greeting": b.get("greeting", cfg.get("greeting")),
            "starter_questions": pack.get(
                "starter_questions", cfg.get("starter_questions", [])
            ),
            "departments": pack.get("departments", cfg.get("departments", [])),
            "office_hours": pack.get("office_hours", cfg.get("office_hours")),
            "response_time_hours": pack.get(
                "response_time_hours", cfg.get("response_time_hours", 24)
            ),
            "consent_text_version": cfg.get("consent_text_version", "2026-10-v1"),
        }
    )
    for key in ("primary_color", "secondary_color", "accent_color", "font"):
        if b.get(key):
            cfg[key] = b[key]
    cfg.setdefault("topics", pack.get("starter_questions", [])[:6])
    return cfg


def ensure_ruas(cur, pack: dict, password: str) -> None:
    t = {
        "slug": pack["college"]["slug"],
        "name": pack["college"]["name"],
        "public_key": "pk_ruas_local_0001",
        "config": c_config(pack, {}),
    }
    tid = app_seed._tenant(cur, t)
    for email, name, role, depts in RUAS_STAFF:
        cur.execute(
            "insert into app.profiles (tenant_id, email, full_name, role, password_hash)"
            " values (%s, %s, %s, %s, %s) returning id",
            (tid, email, name, role, hash_password(password)),
        )
        pid = cur.fetchone()["id"]
        for d in depts:
            cur.execute(
                "insert into app.profile_departments (tenant_id, profile_id, department) values (%s, %s, %s)",
                (tid, pid, d),
            )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--fresh", action="store_true")
    args = ap.parse_args()
    admin = os.environ["ADMIN_DATABASE_URL"]
    password = os.environ.get("DEMO_PASSWORD", "")

    print("migrations:")
    print("  app :", app_migrate.migrate(admin, bootstrap_role=True) or "up to date")
    print(
        "  kb  :",
        kb_migrate.migrate(os.environ["KB_ADMIN_DATABASE_URL"]) or "up to date",
    )
    print(
        "  chat:",
        chat_migrate.migrate(os.environ["CHAT_ADMIN_DATABASE_URL"]) or "up to date",
    )

    if args.fresh:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(
                "truncate kb.chunks, kb.documents, kb.sources, kb.curated_qa_history, kb.curated_qa,"
                " kb.program_dates, kb.program_fees, kb.programs, kb.sandboxes, learn.events, learn.signals,"
                " learn.proposals, learn.eval_cases, learn.eval_runs cascade"
            )
            conn.execute(
                "truncate chat.message_citations, chat.traces, chat.messages, chat.conversations,"
                " chat.tenant_settings, chat.grounding_reviews, chat.wa_messages, chat.wa_outbox, chat.channels"
                " cascade"
            )
        print("emptied Modules A and B")
        # Colleges created through the platform (e.g. Riverside) are archived, so Module C never keeps
        # a live college whose knowledge was just emptied. Their audit history is append-only (PRD
        # 8.4) and stays; the college is suspended and renamed so its pack can be onboarded again.
        with psycopg.connect(admin, autocommit=True) as conn:
            extra = [
                r[0]
                for r in conn.execute(
                    "select id from app.tenants where slug not in ('demo-college', 'other-college', 'ruas')"
                    " and status <> 'suspended'"
                )
            ]
            tables = [
                r[0]
                for r in conn.execute(
                    "select c.table_name from information_schema.columns c join information_schema.tables t"
                    " on t.table_schema = c.table_schema and t.table_name = c.table_name"
                    " where c.table_schema = 'app' and c.column_name = 'tenant_id' and t.table_type = 'BASE TABLE'"
                    " and c.table_name not in ('tenants', 'audit_logs')"
                )
            ]
            for tid in extra:
                for _ in range(
                    len(tables)
                ):  # children first: retry until nothing references the college
                    left = 0
                    for table in tables:
                        try:
                            conn.execute(
                                f"delete from app.{table} where tenant_id = %s", (tid,)
                            )
                        except psycopg.errors.ForeignKeyViolation:
                            left += 1
                    if not left:
                        break
                suffix = f"-archived-{str(tid)[:8]}"
                conn.execute(
                    "update app.tenants set status = 'suspended', slug = slug || %s, public_key = public_key || %s"
                    " where id = %s",
                    (suffix, suffix, tid),
                )
            if extra:
                print(
                    f"archived {len(extra)} platform-onboarded college(s) in Module C"
                )

    shown = app_seed.seed(admin)
    kb_settings, chat_settings = KbSettings(), ChatSettings()
    embedder = make_embedder(kb_settings)
    with psycopg.connect(admin, row_factory=dict_row) as conn, conn.cursor() as cur:
        if HAS_RUAS:
            ensure_ruas(
                cur,
                yaml.safe_load((ROOT / "packs/ruas/pack.yaml").read_text()),
                password,
            )
        conn.commit()
        tenants = {}
        for slug in PACKS:
            pack = yaml.safe_load((ROOT / "packs" / slug / "pack.yaml").read_text())
            cur.execute(
                "select id, config, public_key from app.tenants where slug = %s",
                (slug,),
            )
            row = cur.fetchone()
            cur.execute(
                "update app.tenants set config = %s where id = %s",
                (Jsonb(c_config(pack, row["config"] or {})), row["id"]),
            )
            tenants[slug] = (str(row["id"]), row["public_key"])
        conn.commit()

    for slug, (tid, _) in tenants.items():
        report = kb_pack.load_knowledge(
            kb_settings, embedder, tid, ROOT / "packs" / slug
        )
        ml = kb_pack.reembed_missing(kb_settings, embedder, tid)
        chat_pack.load(chat_settings, tid, ROOT / "packs" / slug)
        print(
            f"{slug}: knowledge {report['curated_qa']} curated answers, {report['documents']} documents"
            f" ({len(report['skipped'])} already loaded), multilingual vectors added {ml}"
        )

    print("\nTenants (Module C id, widget public key):")
    for slug, (tid, key) in tenants.items():
        print(f"  {slug:13} {tid}  {key}")
    print("\nStaff sign-in (college slug + e-mail):")
    print("  demo-college:", ", ".join(s[0] for s in app_seed.STAFF))
    print("  ruas        :", ", ".join(s[0] for s in RUAS_STAFF))
    print("  password    :", shown)


if __name__ == "__main__":
    main()
