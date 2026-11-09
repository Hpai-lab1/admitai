"""Outbound webhooks (PRD 12.2).

Events: lead.created, lead.updated, handoff.created, handoff.resolved (from this module) and
improvement.deployed, kb.sync.completed (relayed from Module A). conversation.closed is not sent:
Module B does not close conversations in Level 1.

Each delivery is a POST of {"id", "type", "created_at", "data"} with headers
    X-AdmitAI-Event-Id   the event id (stable across retries: de-duplicate on it)
    X-AdmitAI-Event      the event type
    X-AdmitAI-Timestamp  unix seconds
    X-AdmitAI-Signature  v1=<hex HMAC-SHA256 of "<timestamp>.<body>" with the endpoint's secret>
Receivers should reject a signature that does not match or a timestamp older than 5 minutes.

Retries back off exponentially (1, 2, 4 ... minutes, at most an hour apart) for 24 hours, then the
delivery is marked failed. Redirects are not followed.

Safety: a webhook URL is chosen by a College Admin, so it must not reach this platform's own
network (SSRF). Outside development, only https URLs whose host resolves to public addresses are
accepted, checked when the endpoint is saved and again before every delivery.

python -m experience_api.webhooks run [--every 15]   # the delivery worker
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import psycopg

from .config import Settings
from .db import Context, Jsonb, tx

EVENTS = (
    "lead.created",
    "lead.updated",
    "handoff.created",
    "handoff.resolved",
    "improvement.deployed",
    "kb.sync.completed",
)
FROM_KNOWLEDGE = ("improvement.deployed", "kb.sync.completed")
GIVE_UP_AFTER = timedelta(hours=24)
TIMEOUT = httpx.Timeout(5.0)


class UnsafeUrl(ValueError):
    pass


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def check_url(url: str, settings: Settings) -> None:
    """Raise UnsafeUrl unless `url` may be called (see the module docstring)."""
    parts = urlsplit(url)
    dev = settings.app_env == "development"
    if parts.scheme not in (("https", "http") if dev else ("https",)):
        raise UnsafeUrl("The URL must start with https://")
    if not parts.hostname or parts.username or parts.password:
        raise UnsafeUrl("The URL needs a host name and no user name or password")
    if dev:
        return
    try:
        infos = socket.getaddrinfo(
            parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP
        )
    except socket.gaierror:
        raise UnsafeUrl("The host name does not resolve") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise UnsafeUrl("The URL must point to a public address")


def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return "v1=" + mac.hexdigest()


def emit(cur, event_type: str, data: dict, lead_id=None, event_id=None) -> int:
    """Queue an event for the current college's subscribed endpoints, in the caller's own
    transaction (so it is sent only if the change it describes is committed)."""
    eid = str(event_id or uuid.uuid4())
    body = {
        "id": eid,
        "type": event_type,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data": json.loads(json.dumps(data, default=str)),
    }
    cur.execute(
        "select app.enqueue_webhook(%s, %s, %s, %s) as n",
        (event_type, eid, Jsonb(body), str(lead_id) if lead_id else None),
    )
    return cur.fetchone()["n"]


def cancel_for_lead(cur, lead_id) -> int:
    cur.execute("select app.cancel_lead_webhooks(%s) as n", (str(lead_id),))
    return cur.fetchone()["n"]


def lead_data(row: dict) -> dict:
    keys = (
        "id", "name", "phone", "email", "city", "program_interest", "qualification",
        "is_minor", "guardian_contact", "score", "band", "stage", "source", "created_at",
        "updated_at",
    )  # fmt: skip
    return {k: row.get(k) for k in keys}


def handoff_data(row: dict) -> dict:
    keys = (
        "id", "conversation_id", "queue", "department", "kind", "reason", "status",
        "language", "preferred_channel", "lead_id", "sla_due_at", "created_at", "resolved_at",
    )  # fmt: skip
    return {k: row.get(k) for k in keys}


def _backoff(attempts: int) -> timedelta:
    return timedelta(minutes=min(60, 2 ** max(0, attempts - 1)))


def deliver(settings: Settings, tenant_id: UUID, limit: int = 100) -> dict:
    """Send what is due for one college. Safe to run from several workers at once."""
    counts = {"delivered": 0, "retry": 0, "failed": 0}
    ctx = Context(tenant_id, "system")
    with tx(settings.database_url, ctx) as cur:
        cur.execute(
            "select d.*, e.url, e.secret from app.webhook_deliveries d"
            " join app.webhook_endpoints e on e.id = d.endpoint_id"
            " where d.status = 'pending' and d.next_attempt_at <= now()"
            " order by d.id limit %s for update of d skip locked",
            (limit,),
        )
        for d in cur.fetchall():
            status, error = None, None
            try:
                check_url(d["url"], settings)
                body = json.dumps(d["payload"], separators=(",", ":")).encode()
                ts = int(time.time())
                r = httpx.post(
                    d["url"],
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "AdmitAI-Webhooks/1",
                        "X-AdmitAI-Event-Id": str(d["event_id"]),
                        "X-AdmitAI-Event": d["type"],
                        "X-AdmitAI-Timestamp": str(ts),
                        "X-AdmitAI-Signature": sign(d["secret"], ts, body),
                    },
                    timeout=TIMEOUT,
                    follow_redirects=False,
                )
                status = r.status_code
                ok = 200 <= r.status_code < 300
                error = None if ok else f"HTTP {r.status_code}"
            except UnsafeUrl as exc:
                ok, error = False, str(exc)
            except httpx.HTTPError as exc:
                ok, error = False, exc.__class__.__name__
            attempts = d["attempts"] + 1
            if ok:
                cur.execute(
                    "update app.webhook_deliveries set status = 'delivered', attempts = %s,"
                    " last_status = %s, last_error = null, delivered_at = now() where id = %s",
                    (attempts, status, d["id"]),
                )
                counts["delivered"] += 1
                continue
            expired = datetime.now(timezone.utc) - d["created_at"] > GIVE_UP_AFTER
            cur.execute(
                "update app.webhook_deliveries set status = %s, attempts = %s, last_status = %s,"
                " last_error = %s, next_attempt_at = now() + %s where id = %s",
                (
                    "failed" if expired else "pending",
                    attempts,
                    status,
                    error,
                    _backoff(attempts),
                    d["id"],
                ),
            )
            counts["failed" if expired else "retry"] += 1
    return counts


def deliver_all(settings: Settings) -> dict:
    with psycopg.connect(settings.admin_database_url) as conn:
        tenants = [
            r[0]
            for r in conn.execute(
                "select distinct tenant_id from app.webhook_deliveries"
                " where status = 'pending' and next_attempt_at <= now()"
            )
        ]
    return {str(t): deliver(settings, t) for t in tenants}


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
        done = deliver_all(s)
        if done:
            print(done, flush=True)
        if not a.every:
            break
        time.sleep(a.every)
