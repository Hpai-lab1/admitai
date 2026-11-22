"""Vapi phone calls for Module B: check the set-up, connect a Vapi phone number, place a test call.

    source local.env.sh
    python assistant/scripts/vapi_setup.py status
    python assistant/scripts/vapi_setup.py connect --phone-number-id <id> --public-url https://<tunnel> --tenant-id <uuid>
    python assistant/scripts/vapi_setup.py call --to +91XXXXXXXXXX     # your own number, to test

- status (read only): the saved assistant (voice, transcriber, greeting) and the account's
  phone numbers (masked), and whether Module B is reachable at VAPI_PUBLIC_URL.
- connect: inbound calls on that number go through this service: the number's own assistant is
  cleared and its server URL set to <public-url>/voice/vapi/webhook with VAPI_SERVER_SECRET, so
  Vapi asks Module B which assistant to use (the saved one, answering through the engine); the
  number is mapped to the college. Prints the line for VAPI_PHONE_NUMBER_ID (outbound calls).
- call: asks the running Module B (KEY_OPS) to place a call. Calls cost Vapi credit; call only
  numbers whose owner agreed (PRD: test numbers and staff volunteers only).

The saved Vapi assistant itself is never changed by this script. Retell is not touched.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

import httpx

API = "https://api.vapi.ai"


def _key() -> dict:
    key = os.environ.get("VAPI_API_KEY")
    if not key:
        sys.exit("Set VAPI_API_KEY (local.secrets.sh), then source local.env.sh")
    return {"Authorization": f"Bearer {key}"}


def _mask(number: str | None) -> str:
    return f"{number[:4]}…{number[-2:]}" if number else "-"


def status() -> int:
    h = _key()
    aid = os.environ.get("VAPI_ASSISTANT_ID")
    if aid:
        a = httpx.get(f"{API}/assistant/{aid}", headers=h, timeout=30)
        if a.status_code == 200:
            j = a.json()
            print(f"assistant: {j.get('name')!r} | greeting: {j.get('firstMessage')!r}")
            print(
                f"  voice: {j.get('voice')} | transcriber: {(j.get('transcriber') or {}).get('provider')}"
            )
            print(
                f"  saved model: {(j.get('model') or {}).get('provider')} (calls from Module B swap in its engine)"
            )
        else:
            print(f"assistant {aid}: HTTP {a.status_code} {a.text[:200]}")
    else:
        print("VAPI_ASSISTANT_ID is not set")
    nums = httpx.get(f"{API}/phone-number", headers=h, timeout=30).json()
    print(f"phone numbers: {len(nums)}")
    for n in nums:
        server = (n.get("server") or {}).get("url") or n.get("serverUrl")
        print(
            f"  {n.get('id')} {n.get('provider')} {_mask(n.get('number'))} "
            f"assistant={n.get('assistantId')} server={server}"
        )
    if not nums:
        print(
            "  none: add one in the Vapi dashboard (Phone Numbers). A free Vapi number is US-only "
            "and inbound-only; calling or receiving Indian numbers needs an imported Twilio, "
            "Vonage or Telnyx number."
        )
    base = os.environ.get("VAPI_PUBLIC_URL")
    if base:
        try:
            ok = (
                httpx.get(f"{base.rstrip('/')}/health", timeout=10).json().get("module")
            )
            print(
                f"Module B at VAPI_PUBLIC_URL: {'reachable' if ok == 'assistant' else 'NOT Module B'}"
            )
        except httpx.HTTPError as exc:
            print(f"Module B at VAPI_PUBLIC_URL: not reachable ({type(exc).__name__})")
    else:
        print("VAPI_PUBLIC_URL is not set (needed for outbound calls)")
    return 0


def connect(phone_number_id: str, public_url: str, tenant_id: str) -> int:
    h = _key()
    secret = os.environ.get("VAPI_SERVER_SECRET")
    if not secret:
        sys.exit("Set VAPI_SERVER_SECRET first (local.secrets.sh)")
    base = public_url.rstrip("/")
    if not base.startswith("https://"):
        sys.exit(
            "--public-url must start with https:// (Vapi calls it from the internet)"
        )
    body = {
        "assistantId": None,  # Vapi then asks the server (assistant-request)
        "server": {
            "url": f"{base}/voice/vapi/webhook",
            "headers": {"X-Vapi-Secret": secret},
        },
    }
    r = httpx.patch(
        f"{API}/phone-number/{phone_number_id}", headers=h, json=body, timeout=30
    )
    if r.status_code >= 300:
        sys.exit(f"Vapi refused ({r.status_code}): {r.text[:300]}")
    here = os.path.dirname(os.path.abspath(__file__))
    subprocess.run(
        [
            sys.executable,
            "-m",
            "assistant_api.vapi",
            "register",
            phone_number_id,
            "--tenant-id",
            tenant_id,
        ],
        cwd=os.path.dirname(here),
        check=True,
    )
    print(f"Inbound calls on {phone_number_id} now reach {base}/voice/vapi/webhook.")
    print("For outbound calls add to local.secrets.sh, then restart Module B:")
    print(f'export VAPI_PHONE_NUMBER_ID="{phone_number_id}"')
    print(f'export VAPI_PUBLIC_URL="{base}"   # again after every tunnel restart')
    return 0


def call(to: str, service_url: str) -> int:
    key = os.environ.get("KEY_OPS") or os.environ.get("SERVICE_KEY")
    tenant = os.environ.get("VAPI_TENANT_ID")
    if not (key and tenant):
        sys.exit("Needs KEY_OPS and VAPI_TENANT_ID (local.env.sh / local.secrets.sh)")
    r = httpx.post(
        f"{service_url}/voice/vapi/calls",
        json={"to": to},
        headers={"X-Service-Key": key, "X-Tenant-Id": tenant},
        timeout=60,
    )
    print(f"HTTP {r.status_code}: {r.text[:400]}")
    return 0 if r.status_code == 201 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    c = sub.add_parser("connect")
    c.add_argument("--phone-number-id", required=True)
    c.add_argument("--public-url", required=True)
    c.add_argument("--tenant-id", required=True)
    p = sub.add_parser("call")
    p.add_argument("--to", required=True, help="international form, e.g. +91XXXXXXXXXX")
    p.add_argument("--service-url", default="http://127.0.0.1:8150")
    a = ap.parse_args()
    if a.cmd == "status":
        return status()
    if a.cmd == "connect":
        return connect(a.phone_number_id, a.public_url, a.tenant_id)
    return call(a.to, a.service_url)


if __name__ == "__main__":
    sys.exit(main())
