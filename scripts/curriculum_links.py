"""Curated answers that point to each programme's official curriculum document.

Many college websites publish course subjects only as pictures or scanned PDFs (RUAS, 19 Nov
2026: every semester's subject table is an image and both curriculum PDFs are scans), so the
crawler has no text to answer "What subjects are taught in B.Tech Civil Engineering?" and the
assistant could only say it was not certain. This gives visitors the college's own document
instead:

    source local.env.sh && python scripts/curriculum_links.py --slug ruas --dry-run   # look first
    source local.env.sh && python scripts/curriculum_links.py --slug ruas             # save

For each catalogue programme with a source page, the page's links to curriculum / syllabus /
programme-structure PDFs are collected and one curated answer is created ("What subjects are
taught in <programme>?", with variants) through Module A's API, then approved with the actor
"curriculum-links-<date>" so staff can review it. Programmes that already have such an answer are
skipped. Reads the college's public pages only, politely (one page at a time, robots.txt honoured).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import urllib.robotparser
from datetime import date
from urllib.parse import quote, urljoin, urlparse

import httpx
import psycopg

A = os.environ.get("KNOWLEDGE_API_URL", "http://127.0.0.1:8100")
UA = "AdmitAI-knowledge-crawler (college admissions assistant)"
WANTED = re.compile(
    r"curricul|syllab|scheme|programme-structure|course-structure|regulation", re.I
)


def pdf_links(html: str, base: str) -> list[str]:
    out = []
    for href, label in re.findall(
        r'href="([^"]+\.pdf)"[^>]*>(.{0,200}?)</a>', html, re.S | re.I
    ):
        url = urljoin(base, href.strip())
        text = re.sub(r"<[^>]+>", " ", label)
        if WANTED.search(url) or WANTED.search(text) or "/uploads/pdf/f" in url:
            parts = urlparse(url)
            url = parts._replace(
                path=quote(parts.path)
            ).geturl()  # spaces in file names
            if url not in out:
                out.append(url)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="ruas")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    key = os.environ.get("KEY_OPS")
    if not key:
        sys.exit("Set KEY_OPS (source local.env.sh)")
    with psycopg.connect(os.environ["ADMIN_DATABASE_URL"]) as conn:
        tenant = conn.execute(
            "select id::text from app.tenants where slug = %s", (args.slug,)
        ).fetchone()[0]
        programmes = conn.execute(
            "select name, source_url, department from kb.programs where tenant_id = %s"
            " and source_url is not null order by name",
            (tenant,),
        ).fetchall()
        # Programmes whose page lists its subjects as text (course codes like "CSC307A") are
        # answered from those chunks; a link-only answer would replace a real subject list.
        text_curricula = {
            url
            for (url,) in conn.execute(
                "select d.url from kb.documents d join kb.chunks c on c.document_id = d.id"
                " where d.tenant_id = %s and d.status = 'approved'"
                " and c.content ~ '[A-Z]{2,5}[0-9]{3}[A-Z]?\\M'"
                " group by d.url having count(*) >= 3",
                (tenant,),
            )
        }
        done = {
            q.lower()
            for (q,) in conn.execute(
                "select question from kb.curated_qa where tenant_id = %s and status <> 'expired'",
                (tenant,),
            )
        }
    programmes = programmes[: args.limit]
    robots: dict[str, urllib.robotparser.RobotFileParser] = {}
    found = []
    with httpx.Client(
        headers={"User-Agent": UA}, follow_redirects=True, timeout=30
    ) as web:
        for name, url, department in programmes:
            question = f"What subjects are taught in {name}?"
            if question.lower() in done or url in text_curricula:
                continue
            host = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
            if host not in robots:
                rp = urllib.robotparser.RobotFileParser(host + "/robots.txt")
                rp.read()
                robots[host] = rp
            if not robots[host].can_fetch(UA, url):
                continue
            r = web.get(url)
            time.sleep(1.0)
            if r.status_code != 200:
                continue
            links = pdf_links(r.text, url)
            # no document: the subjects are still on the programme page (often as pictures)
            shows = bool(re.search(r"curricul|semester", r.text, re.I))
            if links or shows:
                found.append((name, url, department, links))
            print(
                f"  {name}: {len(links)} document(s){'' if links else ', page only' if shows else ', nothing'}"
            )
    print(f"{len(found)} of {len(programmes)} programmes get a curriculum answer")
    if args.dry_run:
        for name, url, _, links in found[:5]:
            print(f"- {name}\n    " + "\n    ".join(links))
        return 0
    actor = f"curriculum-links-{date.today().isoformat()}"
    headers = {"X-Service-Key": key, "X-Tenant-Id": tenant}
    with httpx.Client(timeout=120) as api:
        for name, url, department, links in found:
            if links:
                docs = " and ".join(links[:2])
                answer = (
                    f"Ramaiah University of Applied Sciences publishes the full curriculum of the "
                    f"{name}, with the subjects semester by semester, in its official document: "
                    f"{docs} . The programme page is {url} ."
                )
            else:
                answer = (
                    f"The subjects of the {name}, semester by semester, are shown in the "
                    f"Curriculum Details section of the programme page on the Ramaiah University "
                    f"of Applied Sciences website: {url} ."
                )
            body = {
                "question": f"What subjects are taught in {name}?",
                "variants": [
                    f"What is the curriculum of {name}?",
                    f"What is the syllabus of {name}?",
                    f"Which courses are in {name}?",
                    f"{name} subjects semester wise",
                ],
                "answer": answer,
                "category": "Curriculum",
                "owner_role": "faculty" if department else "admission_officer",
                "department": None,
                "academic_year": "2026-27",
                "valid_to": "2027-07-31",  # as the college's other 2026-27 answers
                "actor": actor,
            }
            r = api.post(f"{A}/curated-qa", headers=headers, json=body)
            if r.status_code >= 300:
                print("  not saved:", name, r.status_code, r.text[:120])
                continue
            qa_id = r.json()["id"]
            api.post(
                f"{A}/curated-qa/{qa_id}/approve",
                headers=headers,
                json={
                    "actor": actor,
                    "reason": "Link to the college's own curriculum document",
                },
            ).raise_for_status()
    print(f"saved and approved as {actor}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
