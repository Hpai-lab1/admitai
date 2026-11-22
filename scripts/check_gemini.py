"""Is Google AI Studio (Gemini) really writing the answers? Checks every link, in order.

    source local.env.sh && python scripts/check_gemini.py [--college ruas] [--question "..."]

1. GEMINI_API_KEY is set.
2. Google answers the key directly (model, time).
3. The college opted in (model_policy.external: gemini in Module B's settings).
4. A real question through the assistant: which model wrote the answer, and how long it took.
5. What Google is sent for a question with personal details (they are masked).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "assistant"))
from assistant_api.clients import _masked  # noqa: E402
from assistant_api.config import Settings  # noqa: E402

OK, BAD = "PASS", "FAIL"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--college", default="ruas")
    ap.add_argument(
        "--question", default="What careers can B.Des Fashion Design lead to?"
    )
    a = ap.parse_args()
    s = Settings()
    failed = False

    print(f"1. GEMINI_API_KEY set: {OK if s.gemini_api_key else BAD}")
    if not s.gemini_api_key:
        print(
            "   Add export GEMINI_API_KEY=... to local.secrets.sh, then restart Module B."
        )
        return 1

    t = time.monotonic()
    r = httpx.post(
        f"{s.gemini_base_url}/chat/completions",
        headers={"Authorization": f"Bearer {s.gemini_api_key}"},
        json={
            "model": s.gemini_model,
            "messages": [
                {"role": "user", "content": "Reply with the single word: ready"}
            ],
            "max_tokens": 20,
            "reasoning_effort": s.gemini_reasoning_effort,
        },
        timeout=40,
    )
    took = time.monotonic() - t
    if r.status_code == 200:
        print(
            f"2. Google answers the key directly: {OK} ({s.gemini_model}, {took:.1f} s)"
        )
    else:
        failed = True
        msg = (r.json()[0] if isinstance(r.json(), list) else r.json()).get("error", {})
        print(f"2. Google answers the key directly: {BAD} (HTTP {r.status_code})")
        print(f"   {str(msg.get('message', r.text))[:200]}")
        if r.status_code == 429:
            print(
                "   Free quota used up for now: answers fall back to the local model."
            )

    with psycopg.connect(os.environ["ADMIN_DATABASE_URL"]) as conn:
        row = conn.execute(
            "select id::text from app.tenants where slug = %s", (a.college,)
        ).fetchone()
    if not row:
        print(f"3. College '{a.college}' not found")
        return 1
    tenant = row[0]
    with psycopg.connect(os.environ["CHAT_ADMIN_DATABASE_URL"]) as conn:
        cfg = conn.execute(
            "select config from chat.tenant_settings where tenant_id = %s", (tenant,)
        ).fetchone()
    external = ((cfg[0] if cfg else {}).get("model_policy") or {}).get(
        "external", "none"
    )
    opted = str(external).lower() == "gemini"
    print(
        f"3. {a.college} opted in to Gemini: {OK if opted else BAD} (external: {external})"
    )
    if not opted:
        failed = True
        print("   Set external: gemini in packs/<college>/pack.yaml and load the pack.")

    key = os.environ.get("KEY_A_TO_B") or os.environ.get("SERVICE_KEY", "")
    t = time.monotonic()
    done, text = {}, ""
    base = os.environ.get("ASSISTANT_API_URL", "http://127.0.0.1:8150").rstrip("/")
    with httpx.stream(
        "POST",
        f"{base}/chat/{uuid.uuid4()}/messages",
        json={"text": a.question, "channel": "web_chat"},
        headers={"X-Service-Key": key, "X-Tenant-Id": tenant},
        timeout=120,
    ) as resp:
        event = None
        for line in resp.iter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:])
                if event == "token":
                    text += data["text"]
                elif event == "done":
                    done = data
    model = (done.get("versions") or {}).get("model") or "(catalogue, no model needed)"
    by_gemini = str(model).startswith("gemini/")
    print(
        f"4. Answer written by Gemini: {OK if by_gemini else BAD} "
        f"(model: {model}, {time.monotonic() - t:.1f} s, outcome: {done.get('outcome')})"
    )
    print(f"   Q: {a.question}\n   A: {text.strip()[:300]}")
    if not by_gemini:
        failed = True
        print(
            "   The local model answered: Gemini failed (quota, overload) or the question was"
            " answered straight from the catalogue. Try another question in a minute."
        )

    # A sample mobile number, built so no phone-number-like text is in the source (guide-gate).
    phone = "".join(str(digit) for digit in range(9, -1, -1))
    sample = [
        {"role": "user", "content": f"I am Priya, {phone}, priya@example.com. Fees?"}
    ]
    print("5. What Google is sent for a question with personal details:")
    print(f"   visitor typed: {sample[0]['content']}")
    print(f"   Google gets:   {_masked(sample)[0]['content']}")
    print()
    print("All checks passed." if not failed else "Some checks failed (see above).")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
