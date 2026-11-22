"""Programmes the college's admissions portal offers that its website has no page for.

RUAS (19 Nov 2026): the admissions portal (admissions.msruas.ac.in) lists B.Tech. in Artificial
Intelligence and Machine Learning, B.Tech. in Artificial Intelligence (AI) and Data Science and
others for the 2027 cycle, but their old website pages return 404, so the knowledge base had never
heard of them. This reads the portal's home page (the one page its robots.txt allows besides the
application forms), builds the full programme names, finds those not in the catalogue, and indexes
one document stating only what the portal says: the programme is offered and open for admission,
with the portal link. Fees, eligibility and seats are not on the portal, so none are stated.

    source local.env.sh && python scripts/portal_programmes.py --slug ruas --dry-run
    source local.env.sh && python scripts/portal_programmes.py --slug ruas

The document is uploaded through Module A (replacing the previous version when re-run) and
approved with the actor "portal-programmes-<date>" so staff can review it.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
import time
from datetime import date

import httpx
import psycopg

A = os.environ.get("KNOWLEDGE_API_URL", "http://127.0.0.1:8100")
UA = "AdmitAI-knowledge-crawler (college admissions assistant)"
PORTAL = {"ruas": "https://admissions.msruas.ac.in/"}
TITLE = "Programmes open for admission (admissions portal)"


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def programmes(page: str) -> list[tuple[str, str]]:
    """(level, full name) from the portal's programme blocks: <div class="chCon"> with <h3>level,
    <h4>degree and <li> names. Other tabs (application steps, notices) are not programmes."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pane in re.findall(
        r'<div class="tab-pane[^"]*"[^>]*>(.*?)(?=<div class="tab-pane|\Z)', page, re.S
    ):
        if 'class="chCon"' not in pane:
            continue
        pane = re.sub(
            r"<!--.*?-->", "", pane, flags=re.S
        )  # commented-out entries are not offered
        level = ""
        for block in re.findall(r'<div class="chCon">(.*?)</div>', pane, re.S):
            degree = ""
            for tag, body in re.findall(r"<(h3|h4|ul)[^>]*>(.*?)</\1>", block, re.S):
                if tag == "h3":
                    level = _text(body)
                elif tag == "h4":
                    degree = _text(body)
                else:
                    for item in re.findall(r"<li[^>]*>(.*?)</li>", body, re.S):
                        name = _text(item)
                        if not name or re.match(
                            r"(Faculty|School|Ramaiah University College) of", name
                        ):
                            continue  # a faculty heading, not a programme
                        own = re.match(
                            r"(Bachelor|Master|Doctor|Post Basic|B\.|M\.|BBA|BCA|MBA|MCA|LL\.|Pharm\.D)",
                            name,
                        )
                        full = name if own or not degree else f"{degree} in {name}"
                        key = _norm(full)
                        if key not in seen:
                            seen.add(key)
                            out.append((level or degree, full))
    return out


def allowed(robots_txt: str, path: str) -> bool:
    """RFC 9309 for the "*" group: the longest matching rule wins, Allow on a tie; "$" anchors the
    end and "*" matches anything (urllib's parser takes the first rule and has no "$", so it reads
    "Disallow: /" before "Allow: /$" as forbidding the home page)."""
    rules, active = [], False
    for line in robots_txt.splitlines():
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            active = value == "*"
        elif active and field in ("allow", "disallow") and value:
            rules.append((field == "allow", value))
    best = None
    for allow, pattern in rules:
        rx = "^" + re.escape(pattern).replace(r"\*", ".*")
        rx = rx[:-2] + "$" if rx.endswith(r"\$") else rx
        if re.match(rx, path):
            key = (len(pattern), allow)
            if best is None or key > best[0]:
                best = (key, allow)
    return True if best is None else best[1]


def _norm(name: str) -> str:
    n = re.sub(
        r"\(.*?\)|[^a-z0-9 ]",
        " ",
        name.lower().replace("&", "and").replace("b.tech.", "b.tech"),
    )
    return " ".join(
        w for w in n.split() if w not in {"in", "of", "and", "the", "hons", "honours"}
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="ruas")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    portal = PORTAL.get(args.slug)
    key = os.environ.get("KEY_OPS")
    if not portal or not key:
        sys.exit("Unknown college portal, or KEY_OPS not set (source local.env.sh)")
    robots = httpx.get(
        portal.rstrip("/") + "/robots.txt", headers={"User-Agent": UA}, timeout=30
    )
    if not allowed(robots.text if robots.status_code == 200 else "", "/"):
        sys.exit("The portal's robots.txt does not allow reading its home page")
    page = httpx.get(
        portal, headers={"User-Agent": UA}, timeout=30, follow_redirects=True
    ).text
    offered = programmes(page)
    with psycopg.connect(os.environ["ADMIN_DATABASE_URL"]) as conn:
        tenant = conn.execute(
            "select id::text from app.tenants where slug = %s", (args.slug,)
        ).fetchone()[0]
        known = [
            _norm(n)
            for (n,) in conn.execute(
                "select name from kb.programs where tenant_id = %s", (tenant,)
            )
        ]
        previous = conn.execute(
            "select id::text from kb.documents where tenant_id = %s and title = %s"
            " and status = 'approved' order by created_at desc limit 1",
            (tenant, TITLE),
        ).fetchone()
    missing = [
        (lvl, n)
        for lvl, n in offered
        if _norm(n) not in known
        and not any(_norm(n) in k or k in _norm(n) for k in known if len(k) > 12)
    ]
    print(
        f"{len(offered)} programmes on the portal; {len(missing)} not in the catalogue:"
    )
    for lvl, n in missing:
        print(f"  [{lvl}] {n}")
    if args.dry_run or not missing:
        return 0
    stamp = date.today().isoformat()
    lines = [
        f"<h1>{TITLE}</h1>",
        f"<p>Ramaiah University of Applied Sciences lists these programmes on its admissions "
        f"portal ({portal}) for the 2027 admission cycle. The portal does not publish their fees, "
        f"eligibility or number of seats; the Directorate of Admissions confirms those.</p>",
    ]
    for lvl, n in missing:
        lines.append(f"<h2>{html.escape(n)}</h2>")
        lines.append(
            f"<p>Ramaiah University of Applied Sciences offers the {html.escape(n)} "
            f"({html.escape(lvl.lower() or 'programme')}). It is listed on the admissions portal, "
            f"{portal}, where applications for the 2027 cycle are made.</p>"
        )
    body = "<html><body>" + "\n".join(lines) + "</body></html>"
    actor = f"portal-programmes-{stamp}"
    headers = {"X-Service-Key": key, "X-Tenant-Id": tenant}
    data = {
        "title": TITLE,
        "academic_year": "2026-27",
        "owner_role": "admission_officer",
        "valid_to": "2027-07-31",
    }
    if previous:
        data["replaces"] = previous[0]
    with httpx.Client(timeout=300) as api:
        r = api.post(
            f"{A}/kb/documents",
            headers=headers,
            data=data,
            files={"file": ("portal-programmes.html", body.encode(), "text/html")},
        )
        r.raise_for_status()
        doc = r.json()
        # the upload is parsed and embedded in the background: approve once it is ready
        for _ in range(120):
            if doc.get("status") != "processing":
                break
            time.sleep(2)
            doc = api.get(f"{A}/kb/documents/{doc['id']}", headers=headers).json()
        if doc.get("status") != "approved":
            api.patch(
                f"{A}/kb/documents/{doc['id']}",
                headers=headers,
                json={
                    "action": "approve",
                    "actor": actor,
                    "reason": "From the college's own admissions portal",
                },
            ).raise_for_status()
    print(f"indexed and approved as {actor} ({len(missing)} programmes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
