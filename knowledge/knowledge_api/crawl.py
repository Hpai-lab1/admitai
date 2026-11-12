"""Website crawl (PRD F4.2): same-site pages, include/exclude URL patterns, depth limit, robots.txt.

A page whose text changed since the last crawl becomes a new version of its document (draft, needs
approval); an unchanged page is skipped. Fetches retry with backoff x3; if the start page still
fails, the source records the error so the College Admin can be told.
"""

from __future__ import annotations

import fnmatch
import time
from collections import deque
from typing import Callable
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from . import documents, ingest
from .config import Settings
from .db import tx
from .embedder import Embedder

USER_AGENT = "AdmitAI-KnowledgeBot/0.1"


def _fetch(
    client: httpx.Client, url: str, sleep: Callable[[float], None]
) -> httpx.Response | None:
    for attempt in range(3):
        try:
            r = client.get(
                url,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
                timeout=15,
            )
            if r.status_code < 500:
                return r
        except httpx.HTTPError:
            pass
        sleep(2**attempt)
    return None


def _allowed(url: str, include: list[str], exclude: list[str]) -> bool:
    if include and not any(fnmatch.fnmatch(url, p) for p in include):
        return False
    return not any(fnmatch.fnmatch(url, p) for p in exclude)


def crawl(
    settings: Settings,
    embedder: Embedder,
    tenant_id: str,
    source: dict,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    cfg = source["config"]
    start = cfg["start_url"]
    include, exclude = cfg.get("include", []), cfg.get("exclude", [])
    max_depth, max_pages = int(cfg.get("max_depth", 2)), int(cfg.get("max_pages", 50))
    host = urlparse(start).netloc
    client = client or httpx.Client()

    robots = RobotFileParser()
    r = _fetch(client, urljoin(start, "/robots.txt"), sleep)
    robots.parse(r.text.splitlines() if r is not None and r.status_code == 200 else [])

    seen, queue = {start}, deque([(start, 0)])
    stats = {
        "fetched": 0,
        "new": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped_robots": 0,
        "failed": 0,
    }
    error = None
    while queue and stats["fetched"] < max_pages:
        url, depth = queue.popleft()
        if not robots.can_fetch(USER_AGENT, url):
            stats["skipped_robots"] += 1
            continue
        resp = _fetch(client, url, sleep)
        if (
            resp is None
            or resp.status_code != 200
            or "html" not in resp.headers.get("content-type", "html")
        ):
            stats["failed"] += 1
            if url == start:
                error = f"Start page could not be fetched after 3 attempts: {url}"
            continue
        stats["fetched"] += 1
        blocks, title, links = ingest.parse_html(resp.text)
        if depth < max_depth:
            for href in links:
                nxt = urldefrag(urljoin(url, href))[0]
                if (
                    urlparse(nxt).netloc == host
                    and nxt.startswith("http")
                    and nxt not in seen
                    and _allowed(nxt, include, exclude)
                ):
                    seen.add(nxt)
                    queue.append((nxt, depth + 1))
        if not any(b.level == 0 for b in blocks):
            continue
        data = ingest.full_text(blocks).encode()
        with tx(settings.database_url, tenant_id) as cur:
            cur.execute(
                "select id from kb.documents where url = %s and status not in ('rejected','expired')"
                " order by version desc limit 1",
                (url,),
            )
            live = cur.fetchone()
            row, duplicate = documents.create(
                cur,
                tenant_id,
                "page.html",
                data,
                {
                    **cfg.get("defaults", {}),
                    "title": title or url,
                    "url": url,
                    "source_id": source["id"],
                    "replaces": live["id"] if live else None,
                },
            )
        if duplicate:
            stats["unchanged"] += 1
            continue
        documents.process(
            settings,
            embedder,
            tenant_id,
            str(row["id"]),
            "page.html",
            data,
            blocks=blocks,
        )
        stats["updated" if live else "new"] += 1

    with tx(settings.database_url, tenant_id) as cur:
        cur.execute(
            "update kb.sources set last_sync = now(), last_error = %s, updated_at = now() where id = %s",
            (error, source["id"]),
        )
    return {**stats, "error": error}
