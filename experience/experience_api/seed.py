"""Local demo data: two fake colleges, staff accounts and a few sample enquiries.

    python -m experience_api.seed            # uses ADMIN_DATABASE_URL from experience/.env

Fake data only (@example.com addresses, +1555 numbers). Re-running replaces the demo colleges.
The staff password is DEMO_PASSWORD from .env, or a new random one that is printed once.
"""

from __future__ import annotations

import os
import secrets
import sys
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import Settings
from .security import hash_password, password_problem

DEMO = {
    "slug": "demo-college",
    "name": "Demo College of Engineering",
    "public_key": "pk_demo_college_0001",
    "config": {
        "assistant_name": "Admissions Assistant",
        "greeting": "Hello! Ask me about courses, fees, hostels or how to apply.",
        "starter_questions": [
            "What is the fee for B.Tech?",
            "Is there a hostel?",
            "What is the last date to apply?",
        ],
        "departments": ["Computer Science", "Mechanical", "Civil"],
        "response_time_hours": 24,
        "office_hours": "Mon-Sat 09:00-17:00",
        "consent_text_version": "2026-10-v1",
        # Look and features modelled on a real university admissions site (colours and font only;
        # no third-party name, logo or images in this repo).
        "primary_color": "#E34A21",
        "secondary_color": "#992361",
        "accent_color": "#361C6A",
        "font": "montserrat",
        "apply_url": "https://admissions.example.com/apply",
        "announcement": "Admissions open for 2026 - UG and PG programmes",
        "topics": [
            "UG Programmes",
            "PG Programmes",
            "Lateral Entry",
            "Admission Process",
            "Scholarships",
            "Loan Assistance",
            "Hostel",
            "Fees",
        ],
    },
}
OTHER = {
    "slug": "other-college",
    "name": "Other Test College",
    "public_key": "pk_demo_college_0002",
    "config": {"departments": ["Commerce"], "response_time_hours": 48},
}
STAFF = [
    ("admin@example.com", "Asha Admin", "college_admin", []),
    ("officer@example.com", "Omar Officer", "admission_officer", []),
    ("cs.faculty@example.com", "Farah Faculty", "faculty", ["Computer Science"]),
    ("mech.faculty@example.com", "Manoj Faculty", "faculty", ["Mechanical"]),
    ("marketing@example.com", "Mira Marketing", "marketing_manager", []),
    ("platform@example.com", "Priya Platform", "super_admin", []),
]


def _password() -> tuple[str, bool]:
    pw = os.environ.get("DEMO_PASSWORD", "")
    if pw and not password_problem(pw):
        return pw, False
    while True:
        pw = secrets.token_urlsafe(12) + "Aa1"
        if not password_problem(pw):
            return pw, True


def _tenant(cur, t: dict) -> str:
    cur.execute("select id from app.tenants where slug = %s", (t["slug"],))
    old = cur.fetchone()
    if old:
        tid = old["id"]
        for table in (
            "conversation_events",
            "break_glass",
            "notifications",
            "message_outbox",
            "outbox_events",
            "corrections",
            "feedback",
            "lead_events",
            "handoff_events",
            "handoffs",
            "consents",
            "guardian_verifications",
            "leads",
            "visitor_sessions",
            "refresh_tokens",
            "profile_departments",
            "profiles",
        ):
            cur.execute(f"delete from app.{table} where tenant_id = %s", (tid,))
        # audit_logs is append-only by design; the old demo tenant's rows stay
        cur.execute(
            "update app.tenants set name = %s, public_key = %s, config = %s, status = 'live' where id = %s",
            (t["name"], t["public_key"], Jsonb(t["config"]), tid),
        )
        return tid
    cur.execute(
        "insert into app.tenants (slug, name, public_key, config) values (%s, %s, %s, %s) returning id",
        (t["slug"], t["name"], t["public_key"], Jsonb(t["config"])),
    )
    return cur.fetchone()["id"]


def _sample_enquiries(cur, tid) -> None:
    now = datetime.now(timezone.utc)
    samples = [
        (
            "Test Student A",
            "+15550100001",
            None,
            ["B.Tech Computer Science"],
            "admission",
            None,
            2,
            "hot",
            82,
        ),
        (
            "Test Student B",
            None,
            "student.b@example.com",
            ["B.Tech Mechanical"],
            "faculty",
            "Mechanical",
            30,
            "warm",
            55,
        ),
        (
            "Test Student C",
            "+15550100003",
            "student.c@example.com",
            ["B.Tech Civil"],
            "admission",
            None,
            5,
            "cold",
            30,
        ),
        (
            "Test Student D",
            "+15550100004",
            None,
            ["B.Tech Computer Science"],
            "faculty",
            "Computer Science",
            1,
            "hot",
            74,
        ),
    ]
    for i, (
        name,
        phone,
        email,
        programs,
        queue,
        dept,
        hours_ago,
        band,
        score,
    ) in enumerate(samples):
        created = now - timedelta(hours=hours_ago)
        cur.execute(
            "insert into app.visitor_sessions (tenant_id, channel, utm, created_at, expires_at)"
            " values (%s, 'web_chat', %s, %s, %s) returning id",
            (
                tid,
                Jsonb(
                    {"utm_source": ["instagram", "google", "direct", "newspaper"][i]}
                ),
                created,
                created + timedelta(hours=24),
            ),
        )
        sid = cur.fetchone()["id"]
        cur.execute(
            "insert into app.consents (tenant_id, session_id, purpose, granted, text_version, captured_at)"
            " values (%s, %s, 'contact', true, '2026-10-v1', %s)",
            (tid, sid, created),
        )
        cur.execute(
            "insert into app.leads (tenant_id, session_id, name, phone, email, program_interest, score, band,"
            " source, created_at) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
            (
                tid,
                sid,
                name,
                phone,
                email,
                programs,
                score,
                band,
                Jsonb(
                    {
                        "channel": "web_chat",
                        "conversation_id": f"demo-conv-{i + 1}",
                        "utm": {
                            "utm_source": [
                                "instagram",
                                "google",
                                "direct",
                                "newspaper",
                            ][i]
                        },
                    }
                ),
                created,
            ),
        )
        lid = cur.fetchone()["id"]
        question = [
            "Can I get a hostel room in first year?",
            "Is the workshop open on Saturdays?",
            "Do you accept the state entrance rank for lateral entry?",
            "Which electives are offered in the third year?",
        ][i]
        context = {
            "transcript": [
                {"role": "user", "text": question},
                {"role": "assistant", "text": "I'm not sure about that one."},
            ],
            "ai_draft": "I'm not sure about that one.",
            "sources": [],
            "confidence": 0.31,
        }
        cur.execute(
            "insert into app.handoffs (tenant_id, conversation_id, session_id, queue, department, kind, reason,"
            " context, preferred_channel, lead_id, sla_due_at, created_at)"
            " values (%s, %s, %s, %s, %s, 'callback', %s, %s, 'phone', %s, %s, %s) returning id",
            (
                tid,
                f"demo-conv-{i + 1}",
                sid,
                queue,
                dept,
                "Low confidence: " + question,
                Jsonb(context),
                lid,
                created + timedelta(hours=24),
                created,
            ),
        )
        hid = cur.fetchone()["id"]
        cur.execute(
            "insert into app.handoff_events (tenant_id, handoff_id, kind, body, at) values"
            " (%s, %s, 'created', %s, %s)",
            (tid, hid, question, created),
        )
        cur.execute(
            "insert into app.notifications (tenant_id, audience, kind, payload) values (%s, %s, 'callback.created',"
            " %s)",
            (
                tid,
                f"faculty:{dept}" if dept else "admission",
                Jsonb({"handoff_id": str(hid)}),
            ),
        )
    for value, reason in [
        (1, None),
        (1, None),
        (1, None),
        (-1, "outdated"),
        (-1, "wrong"),
        (1, None),
    ]:
        cur.execute(
            "insert into app.feedback (tenant_id, conversation_id, message_id, kind, value, reason, actor)"
            " values (%s, 'demo-conv-1', %s, 'thumb', %s, %s, 'visitor')",
            (tid, secrets.token_hex(4), value, reason),
        )


def _sample_conversations(cur, tid) -> None:
    """Conversation events so the PRD 3.5 metrics have something to count (fake)."""
    old = datetime.now(timezone.utc) - timedelta(hours=3)
    plans = [
        # (conversation, user messages, rephrase at end, thumbs-down final, asked human)
        ("demo-conv-1", 3, False, False, False),
        ("demo-conv-2", 2, False, False, True),
        ("demo-conv-3", 2, True, False, False),
        ("demo-conv-5", 4, False, False, False),
        ("demo-conv-6", 2, False, True, False),
        ("demo-conv-7", 1, False, False, False),
        ("demo-conv-8", 3, False, False, False),
    ]
    for conv, n, rephrase, down, human in plans:
        t = old
        last = None
        for i in range(n):
            t += timedelta(minutes=1)
            cur.execute(
                "insert into app.conversation_events (tenant_id, conversation_id, kind, created_at)"
                " values (%s, %s, 'user_message', %s)",
                (tid, conv, t),
            )
            if rephrase and i == n - 1:
                cur.execute(
                    "insert into app.conversation_events (tenant_id, conversation_id, kind, created_at)"
                    " values (%s, %s, 'rephrase', %s)",
                    (tid, conv, t),
                )
            t += timedelta(seconds=20)
            last = f"{conv}-a{i}"
            cur.execute(
                "insert into app.conversation_events (tenant_id, conversation_id, kind, message_id,"
                " created_at) values (%s, %s, 'answer', %s, %s)",
                (tid, conv, last, t),
            )
            if conv != "demo-conv-8" or i < n - 1:  # conv-8 ends unrated: abandoned
                cur.execute(
                    "insert into app.feedback (tenant_id, conversation_id, message_id, kind, value,"
                    " reason, actor, created_at) values (%s, %s, %s, 'thumb', %s, %s, 'visitor', %s)",
                    (
                        tid,
                        conv,
                        last,
                        -1 if down and i == n - 1 else 1,
                        "outdated" if down and i == n - 1 else None,
                        t,
                    ),
                )
        if human:
            cur.execute(
                "insert into app.conversation_events (tenant_id, conversation_id, kind, created_at)"
                " values (%s, %s, 'talk_to_human', %s)",
                (tid, conv, t),
            )
    cur.execute(
        "update app.feedback set department = 'Computer Science', comment = %s"
        " where conversation_id = 'demo-conv-6' and value = -1",
        ("The elective list changed this year",),
    )
    for value in (5, 4, 4, 3, 5):
        cur.execute(
            "insert into app.feedback (tenant_id, conversation_id, message_id, kind, value, actor)"
            " values (%s, 'demo-conv-1', 'conversation', 'rating', %s, 'visitor')",
            (tid, value),
        )


def seed(admin_url: str) -> str:
    pw, generated = _password()
    with psycopg.connect(admin_url, row_factory=dict_row) as conn, conn.cursor() as cur:
        tid = _tenant(cur, DEMO)
        for email, name, role, depts in STAFF:
            cur.execute(
                "insert into app.profiles (tenant_id, email, full_name, role, password_hash)"
                " values (%s, %s, %s, %s, %s) returning id",
                (tid, email, name, role, hash_password(pw)),
            )
            pid = cur.fetchone()["id"]
            for d in depts:
                cur.execute(
                    "insert into app.profile_departments (tenant_id, profile_id, department)"
                    " values (%s, %s, %s)",
                    (tid, pid, d),
                )
        _sample_enquiries(cur, tid)
        _sample_conversations(cur, tid)
        other = _tenant(cur, OTHER)
        cur.execute(
            "insert into app.profiles (tenant_id, email, full_name, role, password_hash)"
            " values (%s, 'admin@example.org', 'Other Admin', 'college_admin', %s)",
            (other, hash_password(pw)),
        )
    return pw if generated else "(DEMO_PASSWORD from .env)"


if __name__ == "__main__":
    url = Settings().admin_database_url
    if not url:
        sys.exit("Set ADMIN_DATABASE_URL in experience/.env")
    shown = seed(url)
    print("Seeded demo-college and other-college.")
    print(
        "Staff sign-in: college 'demo-college', e-mail one of",
        ", ".join(s[0] for s in STAFF),
    )
    print("Password:", shown)
    print("Widget public key:", DEMO["public_key"])
