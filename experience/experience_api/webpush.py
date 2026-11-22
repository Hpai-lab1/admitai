"""Browser push for staff (PRD F7.5), Web Push with VAPID (RFC 8030, 8292).

The push has no payload: the browser shows "Something new is waiting for you in the AdmitAI
console" and opens the console. So no applicant detail passes through Google's, Mozilla's or
Apple's push services, and no payload encryption (RFC 8291) is needed.

The server only ever calls the browser vendors' push services: a subscription naming any other
address is refused, so a crafted subscription cannot make the server call an internal address.
"""

from __future__ import annotations

import base64
import time
from functools import lru_cache
from urllib.parse import urlsplit

import httpx
import jwt
import psycopg
from psycopg.rows import dict_row

from .config import Settings

PUSH_HOSTS = (
    "fcm.googleapis.com",  # Chrome, Edge (FCM)
    "updates.push.services.mozilla.com",  # Firefox
    ".push.apple.com",  # Safari
    ".notify.windows.com",  # older Edge
)
TTL_S = 3600  # a push that cannot be delivered within an hour is dropped by the push service


def allowed_endpoint(url: str) -> bool:
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host or u.port not in (None, 443):
        return False
    return any(
        host == h or (h.startswith(".") and host.endswith(h)) for h in PUSH_HOSTS
    )


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@lru_cache(maxsize=4)
def _keys(path: str):
    from cryptography.hazmat.primitives import serialization

    with open(path, "rb") as f:
        key = serialization.load_pem_private_key(f.read(), password=None)
    public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return key, _b64url(public)


def public_key(settings: Settings) -> str | None:
    """The VAPID public key the browser subscribes with; None when push is not set up."""
    if not settings.vapid_private_key_file:
        return None
    return _keys(settings.vapid_private_key_file)[1]


def send(settings: Settings, endpoint: str, client: httpx.Client | None = None) -> int:
    """One empty push. Returns the push service's status (201 sent; 404/410 subscription gone)."""
    key, pub = _keys(settings.vapid_private_key_file)
    u = urlsplit(endpoint)
    token = jwt.encode(
        {
            "aud": f"{u.scheme}://{u.netloc}",
            "exp": int(time.time()) + 12 * 3600,
            "sub": settings.vapid_subject,
        },
        key,
        algorithm="ES256",
    )
    headers = {
        "TTL": str(TTL_S),
        "Urgency": "high",
        "Authorization": f"vapid t={token}, k={pub}",
        "Content-Length": "0",
    }
    c = client or httpx.Client(timeout=10, follow_redirects=False)
    return c.post(endpoint, headers=headers, content=b"").status_code


# The same rule as the RLS policy on app.notifications: who may see a notification gets its push.
DUE = """
select distinct s.id as subscription_id, s.endpoint
from app.notifications n
join app.push_subscriptions s on s.tenant_id = n.tenant_id
join app.profiles p on p.id = s.profile_id and p.is_active
where n.pushed_at is null and n.created_at > now() - interval '30 minutes'
  and (p.role = 'college_admin'
       or (n.audience = 'admission' and app.policy_role(p.role) = 'admission_officer')
       or (n.audience like 'faculty:%%' and app.policy_role(p.role) = 'faculty'
           and split_part(n.audience, ':', 2) in
               (select d.department from app.profile_departments d where d.profile_id = p.id)))
"""


SEND = send  # tests replace this


def deliver(settings: Settings) -> dict:
    """Push once to each subscription with something new; forget subscriptions the browser
    dropped (404/410). Runs in the messaging worker, with the admin connection (all colleges)."""
    if not settings.vapid_private_key_file:
        return {}
    counts = {"sent": 0, "gone": 0, "failed": 0}
    with psycopg.connect(settings.worker_database_url, row_factory=dict_row) as conn:
        due = conn.execute(DUE).fetchall()
        conn.execute(
            "update app.notifications set pushed_at = now() where pushed_at is null"
        )
        for row in due:
            try:
                status = SEND(settings, row["endpoint"])
            except httpx.HTTPError:
                counts["failed"] += 1
                continue
            if status in (404, 410):
                conn.execute(
                    "delete from app.push_subscriptions where id = %s",
                    (row["subscription_id"],),
                )
                counts["gone"] += 1
            elif 200 <= status < 300:
                conn.execute(
                    "update app.push_subscriptions set last_ok_at = now() where id = %s",
                    (row["subscription_id"],),
                )
                counts["sent"] += 1
            else:
                counts["failed"] += 1
    return {k: v for k, v in counts.items() if v}
