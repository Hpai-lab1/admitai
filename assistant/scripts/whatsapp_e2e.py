"""Simulated WhatsApp end-to-end test (PRD F1.11, F1.12): signed Meta-style webhooks into the
running Module B, through the real pipeline, with replies CAPTURED in chat.wa_outbox instead of
sent (Module B's OutboxSender, used whenever WHATSAPP_TOKEN is not set). No Meta credentials and no
real numbers: the sender is a random fictional number generated for this run, and only its salted
hash is stored, as for a real one.

    source local.env.sh && cd assistant
    python scripts/whatsapp_e2e.py --tenant-id <uuid> --out benchmarks/whatsapp-e2e-<date>.json

Checks: subscription verification (right and wrong token), unsigned and wrongly signed posts,
payloads without messages, a normal question answered, a duplicate delivery ignored, two quick
messages answered in order, and STOP. Afterwards the test conversation is erased and the test
channel removed.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assistant_api import privacy, store, whatsapp  # noqa: E402
from assistant_api.config import Settings  # noqa: E402
from assistant_api.db import tx  # noqa: E402
from assistant_api.experience_client import ExperienceClient, ExperienceError  # noqa: E402


def payload(phone_number_id: str, sender: str, msg_id: str, text: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": phone_number_id},
                            "messages": [
                                {
                                    "from": sender,
                                    "id": msg_id,
                                    "type": "text",
                                    "text": {"body": text},
                                    "timestamp": str(int(time.time())),
                                }
                            ],
                        },
                    }
                ]
            }
        ],
    }


class Run:
    def __init__(self, a):
        self.a = a
        self.db = os.environ["CHAT_DATABASE_URL"]
        self.secret = os.environ["WHATSAPP_APP_SECRET"]
        self.salt = os.environ["CONTACT_SALT"]
        self.http = httpx.Client(base_url=a.assistant, timeout=30)
        self.pnid = "sim-" + secrets.token_hex(6)
        # Fictional: +1 555 01xx is reserved for fiction; generated, never written down.
        self.sender = "1555010" + "".join(
            secrets.choice("0123456789") for _ in range(4)
        )
        self.contact = whatsapp.contact_hash(a.tenant_id, self.sender, self.salt)
        self.conv = f"wa-{self.contact[:24]}"
        self.checks: list[dict] = []

    def check(self, name: str, ok: bool, **detail) -> None:
        self.checks.append({"check": name, "ok": bool(ok), **detail})
        print(("PASS " if ok else "FAIL ") + name, detail or "")

    def post(self, body: dict, sign: str | None = "good"):
        raw = json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        if sign == "good":
            mac = hmac.new(self.secret.encode(), raw, hashlib.sha256).hexdigest()
            headers["X-Hub-Signature-256"] = "sha256=" + mac
        elif sign == "bad":
            headers["X-Hub-Signature-256"] = "sha256=" + "0" * 64
        return self.http.post(
            "/channels/whatsapp/webhook", content=raw, headers=headers
        )

    def outbox(self) -> list[dict]:
        with tx(self.db, self.a.tenant_id) as cur:
            cur.execute(
                "select id, payload, created_at from chat.wa_outbox where contact_hash = %s"
                " order by id",
                (self.contact,),
            )
            return list(cur.fetchall())

    def wait_outbox(self, more_than: int, timeout: float = 60) -> tuple[list, float]:
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            rows = self.outbox()
            if len(rows) > more_than:
                time.sleep(1.0)  # the answer may come in several messages
                return self.outbox(), time.monotonic() - t0
            time.sleep(0.2)
        return self.outbox(), None

    def run(self) -> dict:
        a = self.a
        store.register_channel(self.db, a.tenant_id, "whatsapp", self.pnid)
        try:
            self._checks()
        finally:
            # Test traffic: never a visitor in analytics or learning signals, and erased in
            # both modules (Module C through its own API), with the test channel removed.
            with tx(self.db, a.tenant_id) as cur:
                cur.execute(
                    "select state from chat.conversations where id = %s", (self.conv,)
                )
                row = cur.fetchone() or {}
                c_session = (row.get("state") or {}).get("c_session")
                cur.execute(
                    "update chat.conversations set origin = 'operator' where id = %s",
                    (self.conv,),
                )
                privacy.erase_conversations(cur, [self.conv])
                cur.execute(
                    "delete from chat.channels where channel = 'whatsapp' and external_id = %s",
                    (self.pnid,),
                )
            if c_session:
                try:
                    ExperienceClient(Settings()).erase(a.tenant_id, c_session)
                except ExperienceError:
                    print("could not erase the Module C session", file=sys.stderr)
        return {
            "date": time.strftime("%Y-%m-%d"),
            "mode": "simulated: signed webhooks in, replies captured in chat.wa_outbox (not sent)",
            "passed": sum(c["ok"] for c in self.checks),
            "total": len(self.checks),
            "checks": self.checks,
        }

    def _checks(self) -> None:
        vt = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
        r = self.http.get(
            "/channels/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": vt,
                "hub.challenge": "4242",
            },
        )
        self.check(
            "verification with the right token echoes the challenge", r.text == "4242"
        )
        r = self.http.get(
            "/channels/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong",
                "hub.challenge": "1",
            },
        )
        self.check("verification with a wrong token is refused", r.status_code == 403)

        body = payload(
            self.pnid, self.sender, "wamid.sim-" + secrets.token_hex(8), "hi"
        )
        self.check(
            "an unsigned post is refused", self.post(body, sign=None).status_code == 401
        )
        self.check(
            "a wrongly signed post is refused",
            self.post(body, sign="bad").status_code == 401,
        )
        r = self.post(
            {
                "object": "whatsapp_business_account",
                "entry": [{"changes": [{"value": {}}]}],
            }
        )
        self.check(
            "a payload without messages is accepted and ignored",
            r.status_code == 200 and r.json() == {"accepted": 0},
        )
        self.check("nothing was sent so far", self.outbox() == [])

        q = payload(
            self.pnid,
            self.sender,
            "wamid.sim-" + secrets.token_hex(8),
            "What is the application fee?",
        )
        t0 = time.monotonic()
        r = self.post(q)
        quick = time.monotonic() - t0
        self.check(
            "a normal question is acknowledged at once (Meta retries slow replies)",
            r.status_code == 200 and r.json() == {"accepted": 1} and quick < 2,
            ack_s=round(quick, 3),
        )
        rows, waited = self.wait_outbox(0)
        texts = [row["payload"].get("text") or "" for row in rows]
        self.check(
            "the question is answered on WhatsApp",
            waited is not None and any("2000" in t or "2,000" in t for t in texts),
            answer_s=round(waited, 2) if waited else None,
            messages=len(rows),
        )
        n = len(rows)
        self.post(q)  # Meta delivers the same message again
        time.sleep(4)
        self.check(
            "a duplicate delivery is not answered twice", len(self.outbox()) == n
        )

        first = payload(
            self.pnid,
            self.sender,
            "wamid.sim-" + secrets.token_hex(8),
            "Is there a hostel?",
        )
        second = payload(
            self.pnid,
            self.sender,
            "wamid.sim-" + secrets.token_hex(8),
            "What is the COMEDK code?",
        )
        self.post(first)
        self.post(second)
        rows, _ = self.wait_outbox(n + 1, timeout=90)
        time.sleep(8)
        new = [row["payload"].get("text") or "" for row in self.outbox()[n:]]
        hostel = next((i for i, t in enumerate(new) if "hostel" in t.lower()), None)
        code = next(
            (i for i, t in enumerate(new) if "E197" in t or "COMEDK" in t), None
        )
        self.check(
            "two quick messages are answered in the order sent",
            hostel is not None and code is not None and hostel < code,
            order=[hostel, code],
        )
        n = len(self.outbox())
        self.post(
            payload(self.pnid, self.sender, "wamid.sim-" + secrets.token_hex(8), "STOP")
        )
        rows, _ = self.wait_outbox(n - 1 if n else 0, timeout=30)
        time.sleep(2)
        with tx(self.db, self.a.tenant_id) as cur:
            cur.execute(
                "select state from chat.conversations where id = %s", (self.conv,)
            )
            row = cur.fetchone() or {}
        self.check(
            "STOP opts the person out", bool((row.get("state") or {}).get("opted_out"))
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant-id", required=True)
    ap.add_argument("--assistant", default="http://127.0.0.1:8150")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    report = Run(a).run()
    print(f"{report['passed']}/{report['total']} checks passed")
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(
            json.dumps(report, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8",
        )
        print("written", a.out)
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
