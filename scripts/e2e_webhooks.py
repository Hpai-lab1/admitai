"""Live check of PRD 12.2 and 12.3 across the running services: a College Admin adds a webhook, a
visitor's lead reaches a local receiver (signed, verifiable), errors are problem+json with trace
ids, and erasure tells the receiver to forget the lead.

    source local.env.sh && scripts/run_local.sh && python scripts/e2e_webhooks.py

The receiver listens on 127.0.0.1:8231 (Module C's port range). Needs the webhook worker that
scripts/run_local.sh starts.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

C, A = "http://127.0.0.1:8200", "http://127.0.0.1:8100"
PK = "pk_demo_college_0001"
PORT = 8231
received: list[tuple[dict, bytes]] = []
results: list[tuple[bool, str]] = []


def check(ok: bool, what: str, detail: object = "") -> None:
    results.append((ok, what))
    print(
        f"{'PASS' if ok else 'FAIL'}  {what}"
        + (f"  [{detail}]" if detail and not ok else "")
    )


class Receiver(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers["Content-Length"]))
        received.append(({k.lower(): v for k, v in self.headers.items()}, body))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a):
        pass


def wait_for(pred, seconds=45):
    end = time.time() + seconds
    while time.time() < end:
        hit = next((r for r in received if pred(r)), None)
        if hit:
            return hit
        time.sleep(1)
    return None


def admin_headers(s: httpx.Client) -> dict:
    r = s.post(
        f"{C}/auth/login",
        json={
            "tenant": "demo-college",
            "email": "admin@example.com",
            "password": os.environ["DEMO_PASSWORD"],
        },
    ).json()
    if r["status"] != "ok":
        r = s.post(
            f"{C}/auth/mfa",
            json={
                "mfa_token": r["mfa_token"],
                "code": os.environ.get("DEV_MFA_CODE", ""),
            },
        ).json()
    return {"Authorization": f"Bearer {r['access_token']}"}


def main() -> int:
    server = HTTPServer(("127.0.0.1", PORT), Receiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    s = httpx.Client(timeout=30)

    # PRD 12.3: problem details with a trace id that follows the request.
    r = s.get(
        f"{C}/leads/00000000-0000-0000-0000-000000000000",
        headers={"X-Trace-Id": "e2e-trace-0001"},
    )
    check(
        r.headers.get("content-type") == "application/problem+json"
        and r.json().get("trace_id") == "e2e-trace-0001" == r.headers.get("x-trace-id"),
        "C answers errors as problem+json carrying the caller's trace id",
        r.text[:200],
    )
    r = s.post(f"{A}/kb/search", json={})
    check(
        r.status_code == 401
        and r.headers.get("content-type") == "application/problem+json",
        "A does too, even before a service key is checked",
        r.text[:200],
    )

    admin = admin_headers(s)
    hook = s.post(
        f"{C}/webhooks",
        headers=admin,
        json={
            "url": f"http://127.0.0.1:{PORT}/crm",
            "events": ["lead.created", "lead.updated"],
        },
    ).json()
    check(
        hook.get("secret", "").startswith("whsec_"),
        "a College Admin adds a webhook and gets its secret once",
        hook,
    )

    sess = s.post(f"{C}/widget/session", json={"public_key": PK}).json()
    v = {"Authorization": f"Bearer {sess['session_token']}"}
    s.post(
        f"{C}/consents",
        headers=v,
        json={"purpose": "contact", "granted": True, "text_version": "2026-10-v1"},
    )
    lead = s.post(
        f"{C}/leads",
        headers=v,
        json={"name": "Webhook Check", "email": "hook@example.com"},
    ).json()

    got = wait_for(lambda r: json.loads(r[1]).get("data", {}).get("id") == lead["id"])
    check(got is not None, "the new lead reaches the college's system")
    if got:
        h, body = got
        mac = hmac.new(
            hook["secret"].encode(),
            f"{h['x-admitai-timestamp']}.".encode() + body,
            hashlib.sha256,
        )
        check(
            hmac.compare_digest(h["x-admitai-signature"], "v1=" + mac.hexdigest()),
            "its signature verifies with the endpoint's secret",
        )
        check(
            h["x-admitai-event-id"] == json.loads(body)["id"],
            "it carries a stable event id",
        )

    s.post(f"{C}/privacy/erase", headers=v)
    forgot = wait_for(
        lambda r: json.loads(r[1]).get("type") == "lead.updated"
        and json.loads(r[1])["data"] == {"id": lead["id"], "anonymised": True}
    )
    check(
        forgot is not None,
        "erasure tells the college's system to forget the lead, without its details",
    )

    s.delete(
        f"{C}/webhooks/{hook['id']}", headers=admin
    )  # leave the demo college clean
    server.shutdown()
    failed = [w for ok, w in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
