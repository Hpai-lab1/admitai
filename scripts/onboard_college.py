"""Onboard a college from its Tenant Pack with zero code changes, timed (PRD F10, goal G4).

    source local.env.sh && python scripts/onboard_college.py packs/riverside

Super-Admin uploads the pack -> readiness checklist -> widget is private (onboarding) -> the
college's own staff preview it -> launch: the test set runs through Module B -> live -> a visitor
asks a question.
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path

import httpx
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experience"))
from experience_api.security import totp_code  # noqa: E402

C, B = "http://127.0.0.1:8200", "http://127.0.0.1:8150"


def zip_pack(folder: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in folder.rglob("*"):
            if f.is_file() and not f.name.startswith("."):
                zf.write(f, f"{folder.name}/{f.relative_to(folder).as_posix()}")
    return buf.getvalue()


def super_admin(s: httpx.Client) -> dict:
    body = s.post(
        f"{C}/auth/login",
        json={
            "tenant": "demo-college",
            "email": "platform@example.com",
            "password": os.environ["DEMO_PASSWORD"],
        },
    ).json()
    if body["status"] != "ok":
        secret = body.get("secret")
        if not secret:  # local test only: MFA was enrolled on an earlier run
            with psycopg.connect(os.environ["ADMIN_DATABASE_URL"]) as conn:
                secret = conn.execute(
                    "select mfa_secret from app.profiles where email = 'platform@example.com'"
                ).fetchone()[0]
        body = s.post(
            f"{C}/auth/mfa",
            json={"mfa_token": body["mfa_token"], "code": totp_code(secret)},
        ).json()
    return {"Authorization": f"Bearer {body['access_token']}"}


def ask(s: httpx.Client, token: str, text: str) -> tuple[str, dict]:
    reply, done = "", {}
    with s.stream(
        "POST",
        f"{B}/chat/onboard-{int(time.time())}/messages",
        json={"text": text},
        headers={"Authorization": f"Bearer {token}"},
        timeout=120,
    ) as r:
        ev = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                d = json.loads(line[5:])
                reply += d["text"] if ev == "token" else ""
                done = d if ev == "done" else done
    return reply.strip(), done


def main(folder: Path) -> int:
    s = httpx.Client(timeout=900)
    t0 = time.time()
    sa = super_admin(s)
    print(f"[1] Upload {folder.name} ({len(zip_pack(folder)) // 1024} KB zip)")
    r = s.post(
        f"{C}/platform/tenants",
        headers=sa,
        files={"file": ("pack.zip", zip_pack(folder), "application/zip")},
    )
    if r.status_code != 201:
        print("    refused:", r.status_code, r.text[:500])
        return 1
    t = r.json()
    print(
        f"    created {t['slug']} ({t['status']}) in {time.time() - t0:.0f} s; loaded {t['loaded']}"
    )
    print(f"    staff accounts: {[x['email'] for x in t['staff']]}")

    print("[2] Readiness checklist")
    ready = s.get(f"{C}/platform/tenants/{t['id']}/readiness", headers=sa).json()
    for i in ready["items"]:
        print(
            f"    {'OK ' if i['ok'] else ('NO ' if i['blocking'] else '!! ')} {i['label']}"
            + (f" ({i['detail']})" if i["detail"] else "")
        )

    print("[3] Before launch the widget is private")
    print(
        "    anonymous visitor:",
        s.post(f"{C}/widget/session", json={"public_key": t["public_key"]}).status_code,
    )
    officer = next(x for x in t["staff"] if x["role"] == "admission_officer")
    login = s.post(
        f"{C}/auth/login",
        json={
            "tenant": t["slug"],
            "email": officer["email"],
            "password": officer["temporary_password"],
        },
    ).json()
    preview = s.post(
        f"{C}/widget/session",
        json={"public_key": t["public_key"]},
        headers={"Authorization": f"Bearer {login['access_token']}"},
    )
    print("    the college's own officer (sandbox preview):", preview.status_code)
    answer, _ = ask(s, preview.json()["session_token"], "What is the BBA fee?")
    print(f"    preview answer: {answer}")

    print("[4] Launch: the test set runs through Module B")
    r = s.post(f"{C}/platform/tenants/{t['id']}/launch", headers=sa)
    if r.status_code != 202:
        print("    blocked:", r.text)
        return 1
    while True:
        time.sleep(5)
        st = s.get(f"{C}/platform/tenants/{t['id']}/launch", headers=sa).json()
        if st["status"] != "testing":
            break
    print(
        f"    {st['status']}: {st.get('scores')}"
        + (f" reasons: {st['reasons']}" if st.get("reasons") else "")
    )
    if st["status"] != "live":
        return 1

    print("[5] A visitor on the college's website")
    visitor = s.post(f"{C}/widget/session", json={"public_key": t["public_key"]}).json()
    print(f"    greeting: {visitor['tenant']['greeting']}")
    for q in ("Is there a hostel?", "Which MBA specialisations do you offer?"):
        answer, done = ask(s, visitor["session_token"], q)
        print(f"    Q: {q}\n    A: {answer}  ({done.get('outcome')})")
    print(
        f"\nOnboarded and live in {time.time() - t0:.0f} s, with no code changes (PRD G4 target: 5 working days)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "packs/riverside")))
