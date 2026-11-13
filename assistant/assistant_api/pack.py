"""Load the agent part of a Tenant Pack (PRD F10.1, 7.3) into chat.tenant_settings.

python -m assistant_api.pack load packs/demo-college --tenant-id <uuid>

Tenants may set persona, tone, languages, sensitive topics and escalation thresholds; the safety
rules in prompts.py are platform-locked and cannot be overridden here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from . import leads, policy, store
from .config import Settings


def agent_config(pack: dict) -> dict:
    college = pack.get("college", {})
    branding = pack.get("branding", {})
    persona = pack.get("persona", {})
    cfg = {
        "college": college.get("name", "the college"),
        # Short names visitors use for the college itself are not topic words ("Does RUAS offer ...").
        "college_aliases": [college.get("slug", ""), *college.get("aliases", [])],
        "academic_year": college.get("academic_year"),
        "assistant_name": branding.get("assistant_name", "Admissions Assistant"),
        "tone": persona.get("tone", store.DEFAULT_CONFIG["tone"]),
        "languages": pack.get("languages", ["en"]),
        "sensitive_topics": pack.get(
            "sensitive_topics", store.DEFAULT_CONFIG["sensitive_topics"]
        ),
        "escalation": pack.get("escalation", {}),
        "office_hours": pack.get("office_hours"),
        # F3.1 office-hours rule: the college's own time zone (IANA name; default Asia/Kolkata).
        "timezone": college.get("timezone") or pack.get("timezone"),
        "departments": pack.get("departments", []),
        "model_policy": pack.get("model_policy", {}),
        # F1.11: the number visitors see, and the approved utility templates by message kind
        # ({kind: {name, language, params}}) for messages outside the 24-hour window.
        "whatsapp": {
            "display_number": (pack.get("whatsapp") or {}).get("display_number"),
            "templates": (pack.get("whatsapp") or {}).get("templates") or {},
        },
        # PRD 11.4: transcript retention is configurable per tenant.
        "retention": pack.get("retention", {}),
        # F5.4: lead-score weights and bands (leads.DEFAULT_SCORING when absent).
        "lead_scoring": pack.get("lead_scoring") or {},
    }
    return cfg


def load(settings: Settings, tenant: str, pack_dir: Path) -> dict:
    return apply(
        settings,
        tenant,
        yaml.safe_load((pack_dir / "pack.yaml").read_text(encoding="utf-8")),
    )


class PackProblems(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def validate(settings: Settings, pack: dict) -> list[str]:
    out = []
    if not isinstance(pack.get("college"), dict) or not pack["college"].get("name"):
        out.append("pack.yaml needs college.name")
    out += leads.scoring_problems(pack.get("lead_scoring"))
    return out + policy.problems(pack.get("model_policy"), settings)


def apply(settings: Settings, tenant: str, pack: dict) -> dict:
    found = validate(settings, pack)
    if found:
        raise PackProblems(found)
    result = store.save_tenant_config(settings.database_url, tenant, agent_config(pack))
    wa = (pack.get("whatsapp") or {}).get("phone_number_id")
    if wa:  # F1.11: the college's WhatsApp business number routes to this tenant
        store.register_channel(settings.database_url, tenant, "whatsapp", str(wa))
        result["whatsapp"] = str(wa)
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["load"])
    ap.add_argument("pack", type=Path)
    ap.add_argument("--tenant-id", required=True)
    a = ap.parse_args()
    s = Settings()
    if not s.database_url:
        sys.exit("Set CHAT_DATABASE_URL (source local.env.sh)")
    report = load(s, a.tenant_id, a.pack)
    # Only what an operator needs: the business number's id stays out of logs.
    whatsapp = "registered" if "whatsapp" in report else "not configured"
    print({"version": report["version"], "whatsapp": whatsapp})
