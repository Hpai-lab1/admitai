"""Live checks for the channels (PRD F1.11-F1.12 WhatsApp, F2 voice, F1.4b grounding review).
Run after bootstrap_local.py and run_local.sh:

    source local.env.sh && python scripts/e2e_channels.py

WhatsApp: a signed Meta-style webhook goes to Module B; replies land in chat.wa_outbox because no
Cloud API token is configured locally. Voice: a question spoken by Piper is sent as a recording.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import sys
import time
import uuid
import wave
from pathlib import Path

import httpx
import psycopg

C, B = "http://127.0.0.1:8200", "http://127.0.0.1:8150"
PNID, NUMBER = "demo-test-pnid-0001", "15550109876"  # a fictional test number
results: list[bool] = []


def check(ok: bool, what: str, detail: object = "") -> None:
    results.append(ok)
    print(
        f"{'PASS' if ok else 'FAIL'}  {what}"
        + (f"  [{str(detail)[:300]}]" if not ok and detail else "")
    )


def webhook(s: httpx.Client, message: dict) -> None:
    body = json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": PNID},
                                "messages": [message],
                            }
                        }
                    ]
                }
            ],
        }
    ).encode()
    sig = (
        "sha256="
        + hmac.new(
            os.environ["WHATSAPP_APP_SECRET"].encode(), body, hashlib.sha256
        ).hexdigest()
    )
    r = s.post(
        f"{B}/channels/whatsapp/webhook",
        content=body,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text


def outbox(after: int) -> list[dict]:
    with psycopg.connect(os.environ["CHAT_ADMIN_DATABASE_URL"]) as conn:
        return [
            {"id": r[0], **r[1]}
            for r in conn.execute(
                "select id, payload from chat.wa_outbox where id > %s order by id",
                (after,),
            )
        ]


def wait_outbox(after: int, n: int = 1, timeout: float = 90) -> list[dict]:
    t0 = time.time()
    while time.time() - t0 < timeout:
        got = outbox(after)
        if len(got) >= n:
            return got
        time.sleep(0.5)
    return outbox(after)


def whatsapp(s: httpx.Client) -> None:
    print("\n-- WhatsApp")
    start = max([0] + [m["id"] for m in outbox(0)])
    run = uuid.uuid4().hex[:6]
    webhook(
        s,
        {
            "id": f"wamid.{run}.1",
            "from": NUMBER,
            "type": "text",
            "text": {"body": "What is the tuition fee for B.Tech CSE?"},
        },
    )
    msgs = wait_outbox(start)
    first = msgs[0] if msgs else {}
    check(
        # Amounts are written as on the college's website ("₹2,50,000"): compare the digits.
        "250000" in first.get("text", "").replace(",", ""),
        "answer arrives as WhatsApp text with the catalogue fee",
        first,
    )
    check(
        "Sources:" in first.get("text", "") and "**" not in first.get("text", ""),
        "plain text with numbered sources, no Markdown",
        first.get("text"),
    )
    check(
        [b["id"] for b in first.get("buttons", [])] == ["fb:up", "fb:down", "human"],
        "quick-reply buttons: helpful, not helpful, talk to a person",
        first.get("buttons"),
    )
    webhook(
        s,
        {
            "id": f"wamid.{run}.1",
            "from": NUMBER,
            "type": "text",
            "text": {"body": "What is the tuition fee for B.Tech CSE?"},
        },
    )  # Meta retry
    time.sleep(2)
    check(len(outbox(start)) == 1, "a retried delivery is answered once")
    webhook(
        s,
        {
            "id": f"wamid.{run}.2",
            "from": NUMBER,
            "type": "interactive",
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": "human", "title": "Talk to a person"},
            },
        },
    )
    ask = wait_outbox(start, 2)[-1]
    check(
        "Reply YES or NO" in ask.get("text", ""),
        "talking to a person first asks for consent",
        ask,
    )
    webhook(
        s,
        {
            "id": f"wamid.{run}.3",
            "from": NUMBER,
            "type": "text",
            "text": {"body": "Yes"},
        },
    )
    ack = wait_outbox(start, 3)[-1]
    check(
        "will contact you on this number" in ack.get("text", ""),
        "consent gives a callback acknowledgement",
        ack,
    )
    officer = s.post(
        f"{C}/auth/login",
        json={
            "tenant": "demo-college",
            "email": "officer@example.com",
            "password": os.environ["DEMO_PASSWORD"],
        },
    ).json()
    queue = s.get(
        f"{C}/handoffs", headers={"Authorization": f"Bearer {officer['access_token']}"}
    ).json()
    mine = [
        h
        for h in queue
        if h["preferred_channel"] == "whatsapp"
        and h["conversation_id"].startswith("wa-")
    ]
    latest = max(mine, key=lambda h: h["created_at"]) if mine else None
    check(
        bool(
            latest and latest["lead"] and latest["lead"]["phone"].endswith(NUMBER[-6:])
        ),
        "the officer sees a WhatsApp callback with the lead's number",
        latest,
    )
    check(
        bool(latest and latest["context"].get("ai_draft")),
        "the callback carries the handoff package",
    )
    with psycopg.connect(os.environ["CHAT_ADMIN_DATABASE_URL"]) as conn:
        leaked = conn.execute(
            "select count(*) from chat.conversations c where c::text like %s",
            (f"%{NUMBER}%",),
        ).fetchone()[0]
    check(leaked == 0, "Module B stored no WhatsApp number")


def voice(s: httpx.Client) -> None:
    print("\n-- Voice")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "assistant"))
    from piper import PiperVoice

    model = Path(os.environ["VOICE_MODEL_DIR"]) / "piper" / "en_US-lessac-medium.onnx"
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        PiperVoice.load(str(model)).synthesize_wav(
            "Is there a hostel for first year students?", w
        )
    sess = s.post(
        f"{C}/widget/session", json={"public_key": "pk_demo_college_0001"}
    ).json()
    conv = f"voice-{uuid.uuid4().hex[:6]}"
    tok = s.post(
        f"{B}/voice/token",
        json={"conversation_id": conv},
        headers={"Authorization": f"Bearer {sess['session_token']}"},
    ).json()["token"]
    r = s.post(
        f"{B}/voice/turn",
        data={"token": tok},
        files={"audio": ("q.wav", buf.getvalue(), "audio/wav")},
        timeout=180,
    ).json()
    print(
        f"      heard: {r['transcript']!r}\n      answer: {r['answer']!r}\n      timings: {r['timings']}"
    )
    check(
        "hostel" in r["transcript"].lower(),
        "speech-to-text hears the question",
        r["transcript"],
    )
    check(
        r["outcome"] in ("answered", "partial") and "[1]" in r["answer"],
        "the same checked, cited answer as chat (a partial answer is still checked)",
        r,
    )
    check(bool(r["audio_wav_base64"]), "the answer is spoken (Piper)")
    check(
        len([x for x in r["answer"].split(". ") if x.strip()]) <= 4,
        "voice style keeps the answer short",
        r["answer"],
    )
    print(
        f"      end of speech -> audio ready: {r['timings']['total_ms'] / 1000:.1f} s "
        "(PRD target 3.5 s p95 on the lab GPUs; this is a laptop CPU)"
    )


def review(s: httpx.Client) -> None:
    print("\n-- Grounding review (F1.4b)")
    tenant = s.post(
        f"{C}/widget/session", json={"public_key": "pk_demo_college_0001"}
    ).json()["tenant"]["id"]
    h = {"X-Service-Key": os.environ["KEY_OPS"], "X-Tenant-Id": tenant}
    counts = s.post(f"{B}/reviews/run?limit=40", headers=h, timeout=900).json()
    print(f"      reviewed: {counts}")
    check(sum(counts.values()) > 0, "stored answers are reviewed off the hot path")
    items = s.get(f"{B}/reviews/unsupported", headers=h).json()["items"]
    for it in items[:3]:
        bad = [x for x in it["sentences"] if x.get("supported") is False]
        print(
            f"      unsupported: {it['question']!r} -> {bad[0]['sentence'][:100]!r} new words {bad[0].get('new_words')}"
        )


def main() -> int:
    s = httpx.Client(timeout=60)
    whatsapp(s)
    voice(s)
    review(s)
    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
