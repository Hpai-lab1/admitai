"""E-mail and WhatsApp to guardians, applicants and staff, the overdue-callback check and the weekly
approval digest.

PRD F5.3 guardian codes, F3.4 callback acknowledgements, F3.6 staff replies, F7.5 staff
notifications, and the F3 error rule for callbacks not actioned in time: remind the queue,
escalate to the College Admin and send the applicant an honest update.

Messages are queued in app.message_outbox inside the transaction that caused them, so a rolled-back
request sends nothing. The worker sends them through the college's SMTP server (SMTP_* settings)
and blanks the address and body once sent: the outbox is a queue, not an archive. Staff messages
name an audience ('admission', 'faculty:<department>', 'college_admin') and are addressed when
sent, so a deactivated account is never mailed. Guardian codes are the exception: they are sent at
once and stored nowhere, not even here. Without SMTP settings nothing is sent and nothing is lost;
messages wait until a server is configured.

python -m experience_api.messaging run [--every 15]   # overdue callbacks, weekly digest, then send what is due
"""

from __future__ import annotations

import argparse
import smtplib
import ssl
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from uuid import UUID

import psycopg

from .config import Settings
from .db import Context, Jsonb, audit, tx
from .webhooks import GIVE_UP_AFTER, _backoff


class NotConfigured(RuntimeError):
    pass


def email_configured(settings: Settings) -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


def smtp_send(settings: Settings, to: str, subject: str, body: str) -> None:
    """Send one plain-text e-mail. Raises OSError or smtplib.SMTPException on failure."""
    if not email_configured(settings):
        raise NotConfigured("SMTP_HOST and SMTP_FROM are not set")
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as s:
        if settings.smtp_starttls:
            s.starttls(context=ssl.create_default_context())
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(msg)


# Tests replace this; everything else calls send() so there is one place that talks to SMTP.
SEND = smtp_send


def send(settings: Settings, to: str, subject: str, body: str) -> None:
    SEND(settings, to, subject, body)


def queue(
    cur,
    tenant_id: UUID,
    template: str,
    subject: str,
    body: str,
    *,
    to: str | None = None,
    audience: str | None = None,
    lead_id: UUID | None = None,
    handoff_id: UUID | None = None,
    channel: str = "email",
) -> None:
    """Queue one message to a person (`to`) or to a staff audience, in the caller's transaction.
    WhatsApp messages go to a person only, through Module B."""
    cur.execute(
        "insert into app.message_outbox (tenant_id, channel, template, destination, audience, subject,"
        " body, lead_id, handoff_id) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            tenant_id,
            channel,
            template,
            to,
            audience,
            subject,
            body,
            lead_id,
            handoff_id,
        ),
    )


def cancel_for_lead(cur, lead_id) -> int:
    cur.execute("select app.cancel_lead_messages(%s) as n", (str(lead_id),))
    return cur.fetchone()["n"]


def applicant_email(lead: dict | None) -> str | None:
    """Where to write to about a lead: the applicant's own e-mail, or for a minor the guardian's
    verified e-mail. A phone-only lead gets no e-mail (staff call them)."""
    if not lead:
        return None
    if lead.get("is_minor"):
        g = lead.get("guardian_contact") or {}
        return g.get("destination") if g.get("channel") == "email" else None
    return lead.get("email")


def applicant_whatsapp(lead: dict | None, preferred_channel: str | None) -> str | None:
    """Where to write to on WhatsApp: a minor's guardian who verified on WhatsApp, or an adult who
    asked to be contacted on WhatsApp. Module B still refuses anyone who opted out, and outside the
    24-hour window it sends only the college's approved template."""
    if not lead:
        return None
    if lead.get("is_minor"):
        g = lead.get("guardian_contact") or {}
        return g.get("destination") if g.get("channel") == "whatsapp" else None
    return lead.get("phone") if preferred_channel == "whatsapp" else None


def staff_reply_whatsapp(lead: dict | None) -> str | None:
    """A staff member chose to answer on WhatsApp: the adult's own number, or the guardian's
    WhatsApp number for a minor."""
    if not lead:
        return None
    if lead.get("is_minor"):
        g = lead.get("guardian_contact") or {}
        return g.get("destination") if g.get("channel") == "whatsapp" else None
    return lead.get("phone")


# Module B's message kinds for each template (POST /channels/whatsapp/notify).
WHATSAPP_KIND = {
    "callback_ack": "callback_ack",
    "callback_delayed": "callback_reminder",
    "staff_reply": "staff_reply",
}


def college_name(cur) -> str:
    cur.execute("select name from app.tenants where id = app.current_tenant()")
    row = cur.fetchone()
    return row["name"] if row else "the college"


def staff_addresses(cur, audience: str) -> list[str]:
    """Active staff behind an audience; the College Admin when a queue has nobody."""
    if audience.startswith("faculty:"):
        cur.execute(
            "select p.email from app.profiles p join app.profile_departments d on d.profile_id = p.id"
            " where p.is_active and p.role = 'faculty' and d.department = %s order by p.email",
            (audience.split(":", 1)[1],),
        )
    elif audience == "approvers":
        # the weekly approval digest (PRD 8.3, F7.7): everyone who decides college-wide proposals
        cur.execute(
            "select email from app.profiles where is_active"
            " and role in ('college_admin', 'admission_officer') order by email"
        )
    else:
        role = "admission_officer" if audience == "admission" else "college_admin"
        cur.execute(
            "select email from app.profiles where is_active and role = %s order by email",
            (role,),
        )
    found = [r["email"] for r in cur.fetchall()]
    if found or audience == "college_admin":
        return found
    return staff_addresses(cur, "college_admin")


def deliver(settings: Settings, tenant_id: UUID, limit: int = 50) -> dict:
    """Send what is due for one college. Safe to run from several workers at once."""
    counts = {"sent": 0, "retry": 0, "failed": 0, "cancelled": 0}
    with tx(settings.database_url, Context(tenant_id, "system")) as cur:
        cur.execute(
            "select * from app.message_outbox where status = 'pending' and channel = 'email'"
            " and next_attempt_at <= now() order by id limit %s for update skip locked",
            (limit,),
        )
        for m in cur.fetchall():
            to = (
                [m["destination"]]
                if m["destination"]
                else staff_addresses(cur, m["audience"])
            )
            if not to:
                cur.execute(
                    "update app.message_outbox set status = 'cancelled', body = null,"
                    " last_error = 'nobody to send to' where id = %s",
                    (m["id"],),
                )
                counts["cancelled"] += 1
                continue
            try:
                for addr in to:
                    send(settings, addr, m["subject"], m["body"])
            except (OSError, smtplib.SMTPException) as exc:
                attempts = m["attempts"] + 1
                expired = datetime.now(timezone.utc) - m["created_at"] > GIVE_UP_AFTER
                cur.execute(
                    "update app.message_outbox set status = %s, attempts = %s, last_error = %s,"
                    " next_attempt_at = now() + %s where id = %s",
                    (
                        "failed" if expired else "pending",
                        attempts,
                        exc.__class__.__name__,
                        _backoff(attempts),
                        m["id"],
                    ),
                )
                counts["failed" if expired else "retry"] += 1
                continue
            cur.execute(
                "update app.message_outbox set status = 'sent', sent_at = now(), attempts = attempts + 1,"
                " destination = null, body = null, last_error = null where id = %s",
                (m["id"],),
            )
            counts["sent"] += 1
    return counts


def deliver_whatsapp(settings: Settings, tenant_id: UUID, limit: int = 50) -> dict:
    """Send queued WhatsApp messages through Module B (PRD F3.4, F3.6). Inside the 24-hour window B
    sends the text; outside it, the college's approved template or a refusal. A refusal (opted
    out, no template, no WhatsApp number) is final and staff see why; an outage is retried with
    backoff for 24 hours like e-mail. Sent and refused rows forget the number and the text."""
    from .clients import AssistantRefused, UpstreamError, assistant_post

    counts = {"sent": 0, "retry": 0, "failed": 0}
    with tx(settings.database_url, Context(tenant_id, "system")) as cur:
        cur.execute(
            "select * from app.message_outbox where status = 'pending' and channel = 'whatsapp'"
            " and next_attempt_at <= now() order by id limit %s for update skip locked",
            (limit,),
        )
        for m in cur.fetchall():
            try:
                assistant_post(
                    settings,
                    "/channels/whatsapp/notify",
                    tenant_id,
                    {
                        "to": m["destination"],
                        "kind": WHATSAPP_KIND.get(m["template"], "staff_reply"),
                        "text": m["body"],
                    },
                )
            except AssistantRefused as exc:
                cur.execute(
                    "update app.message_outbox set status = 'failed', attempts = attempts + 1,"
                    " destination = null, body = null, last_error = %s where id = %s",
                    (f"refused: {exc}"[:300], m["id"]),
                )
                _note_refusal(cur, tenant_id, m, str(exc))
                counts["failed"] += 1
                continue
            except UpstreamError as exc:
                attempts = m["attempts"] + 1
                expired = datetime.now(timezone.utc) - m["created_at"] > GIVE_UP_AFTER
                cur.execute(
                    "update app.message_outbox set status = %s, attempts = %s, last_error = %s,"
                    " next_attempt_at = now() + %s where id = %s",
                    (
                        "failed" if expired else "pending",
                        attempts,
                        str(exc)[:300],
                        _backoff(attempts),
                        m["id"],
                    ),
                )
                counts["failed" if expired else "retry"] += 1
                continue
            cur.execute(
                "update app.message_outbox set status = 'sent', sent_at = now(), attempts = attempts + 1,"
                " destination = null, body = null, last_error = null where id = %s",
                (m["id"],),
            )
            counts["sent"] += 1
    return counts


def _note_refusal(cur, tenant_id: UUID, m: dict, reason: str) -> None:
    """Staff must know a WhatsApp message did not go, so they can phone or e-mail instead."""
    if not m["handoff_id"]:
        return
    cur.execute(
        "insert into app.handoff_events (tenant_id, handoff_id, kind, body) values (%s, %s, 'messaged', %s)",
        (
            tenant_id,
            m["handoff_id"],
            f"WhatsApp message not sent: {reason}. Please phone or e-mail instead."[
                :1000
            ],
        ),
    )


def _audience(queue_name: str, department: str | None) -> str:
    return f"faculty:{department}" if queue_name == "faculty" else "admission"


def escalate_overdue(settings: Settings, tenant_id: UUID) -> int:
    """Callbacks past their promised time, each handled once: the queue is reminded, the College
    Admin is told, and the applicant gets an honest update if we have an e-mail for them."""
    ctx = Context(tenant_id, "system")
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "select h.id, h.queue, h.department, h.lead_id, h.sla_due_at, h.preferred_channel,"
            " l.email, l.phone, l.is_minor, l.guardian_contact"
            " from app.handoffs h left join app.leads l on l.id = h.lead_id and l.anonymised_at is null"
            " where h.status <> 'resolved' and h.sla_due_at < now() and h.escalated_at is null"
            " order by h.sla_due_at limit 200 for update of h skip locked"
        )
        rows = cur.fetchall()
        if not rows:
            return 0
        college = college_name(cur)
        link = settings.web_origin.rstrip("/") + "/console/handoffs/"
        for h in rows:
            audience = _audience(h["queue"], h["department"])
            payload = Jsonb(
                {"handoff_id": str(h["id"]), "due": h["sla_due_at"].isoformat()}
            )
            for who, kind in (
                (audience, "callback.overdue"),
                ("college_admin", "callback.escalated"),
            ):
                cur.execute(
                    "insert into app.notifications (tenant_id, audience, kind, payload) values (%s, %s, %s, %s)",
                    (tenant_id, who, kind, payload),
                )
            subject = "Overdue: a callback was promised and is not done"
            body = (
                f"A callback in the {audience.replace(':', ' / ')} queue was due by"
                f" {h['sla_due_at']:%d %b %Y, %H:%M} UTC and is still open.\n\n{link}{h['id']}\n"
            )
            queue(
                cur,
                tenant_id,
                "callback_overdue",
                subject,
                body,
                audience=audience,
                handoff_id=h["id"],
            )
            queue(
                cur,
                tenant_id,
                "callback_escalated",
                subject,
                body,
                audience="college_admin",
                handoff_id=h["id"],
            )
            apology = (
                f"We are sorry: we promised to contact you by {h['sla_due_at']:%d %b %Y, %H:%M} UTC"
                f" and have not done so yet. Your request is still open and has been passed to a senior"
                f" member of the admissions team, who will contact you as soon as possible.\n\n{college}\n"
            )
            to = applicant_email(h)
            if to:
                queue(
                    cur,
                    tenant_id,
                    "callback_delayed",
                    f"{college}: we have not called you back yet",
                    apology,
                    to=to,
                    lead_id=h["lead_id"],
                    handoff_id=h["id"],
                )
            wa = applicant_whatsapp(h, h["preferred_channel"])
            if wa:
                queue(
                    cur,
                    tenant_id,
                    "callback_delayed",
                    "",
                    apology,
                    to=wa,
                    lead_id=h["lead_id"],
                    handoff_id=h["id"],
                    channel="whatsapp",
                )
            told = [w for w, ok in (("e-mailed", to), ("sent a WhatsApp", wa)) if ok]
            note = "Overdue: queue reminded, College Admin told" + (
                f", applicant {' and '.join(told)} an update"
                if told
                else "; no e-mail or WhatsApp on file, please phone the applicant"
            )
            cur.execute(
                "insert into app.handoff_events (tenant_id, handoff_id, kind, body) values (%s, %s, 'escalated', %s)",
                (tenant_id, h["id"], note),
            )
            cur.execute(
                "update app.handoffs set escalated_at = now() where id = %s", (h["id"],)
            )
            audit(
                cur,
                ctx,
                "callback.escalated",
                "handoff",
                h["id"],
                after={"applicant_told": bool(to or wa)},
            )
        return len(rows)


DIGEST_EVERY = "7 days"


def weekly_digest(settings: Settings, tenant_id: UUID) -> str | None:
    """PRD 8.3, F7.7: once a week, e-mail the approvers Module A's digest (top proposals by
    impact, the overdue count, second reviews waiting). The text holds proposal titles and counts
    only, never visitors' questions. Also asks A to draw this month's 10% second-review sample
    (safe to repeat). One outbox row per week records that the week was handled, sent or not;
    if Module A cannot be reached nothing is recorded and the next run tries again.
    Returns 'queued', 'nothing', or None when not due or A is unavailable."""
    from .clients import UpstreamError, knowledge_get, knowledge_post

    ctx = Context(tenant_id, "system")
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "select 1 from app.message_outbox where template = 'weekly_digest'"
            " and created_at > now() - %s::interval limit 1",
            (DIGEST_EVERY,),
        )
        if cur.fetchone():
            return None
    try:
        knowledge_post(settings, "/improvements/second-reviews/sample", tenant_id, {})
    except UpstreamError:
        pass  # the digest still goes; the sample is drawn on a later run
    try:
        d = knowledge_get(settings, "/reports/digest", tenant_id)
    except UpstreamError:
        return None
    link = settings.web_origin.rstrip("/") + "/console/approvals"
    with tx(settings.database_url, ctx) as cur:
        if d.get("send"):
            queue(
                cur,
                tenant_id,
                "weekly_digest",
                str(d.get("subject") or "Improvement proposals to approve")[:300],
                f"{d.get('text', '')}\n\n{link}\n",
                audience="approvers",
            )
            return "queued"
        cur.execute(
            "insert into app.message_outbox (tenant_id, channel, template, status, last_error)"
            " values (%s, 'email', 'weekly_digest', 'cancelled', 'nothing to approve')",
            (tenant_id,),
        )
        return "nothing"


def run_once(settings: Settings) -> dict:
    with psycopg.connect(settings.admin_database_url) as conn:
        tenants = [
            r[0]
            for r in conn.execute(
                "select id from app.tenants where status <> 'suspended'"
            )
        ]
        waiting = conn.execute(
            "select count(*) from app.message_outbox where status = 'pending' and channel = 'email'"
        ).fetchone()[0]
    out: dict = {}
    for t in tenants:
        n = escalate_overdue(settings, t)
        if n:
            out.setdefault(str(t), {})["escalated"] = n
        if weekly_digest(settings, t) == "queued":
            out.setdefault(str(t), {})["digest"] = "queued"
        if settings.key_to_assistant:
            wa = deliver_whatsapp(settings, t)
            if any(wa.values()):
                out.setdefault(str(t), {})["whatsapp"] = wa
    if not email_configured(settings):
        if waiting:
            out["waiting_for_smtp"] = waiting
        return out
    for t in tenants:
        done = deliver(settings, t)
        if any(done.values()):
            out.setdefault(str(t), {}).update(done)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["run"])
    ap.add_argument(
        "--every", type=float, default=0, help="seconds between runs; 0 = once"
    )
    a = ap.parse_args()
    s = Settings()
    if not (s.database_url and s.admin_database_url):
        sys.exit("Set DATABASE_URL and ADMIN_DATABASE_URL (source local.env.sh)")
    while True:
        done = run_once(s)
        if done:
            print(done, flush=True)
        if not a.every:
            break
        time.sleep(a.every)
