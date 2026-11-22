"""The college's programmes for its own website (the demo site), straight from Module A's
knowledge base, which builds itself from the college's website (knowledge_api/site_kb.py).

Public and read-only, keyed by the widget's public key like /widget/session: programme names,
levels, durations, intake and fees are what the college publishes, never visitor data. The last
good copy per college is kept for ten minutes and served while Module A is down.
"""

from __future__ import annotations

import threading
import time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..clients import UpstreamError, knowledge_get
from ..config import Settings
from ..db import tx
from ..deps import settings_dep

router = APIRouter(tags=["widget"])

FRESH_S = 600
_cache: dict[UUID, tuple[float, list[dict]]] = {}
_lock = threading.Lock()


def _indian(amount: str) -> str:
    """'200000.00' -> '2,00,000' (as the college's site writes it)."""
    whole = str(amount).split(".")[0]
    if len(whole) <= 3:
        return whole
    head, tail = whole[:-3], whole[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    return ",".join(([head] if head else []) + parts + [tail])


def _duration(months: int | None) -> str:
    if not months:
        return ""
    if months % 12 == 0:
        y = months // 12
        return f"{y} Year{'s' if y != 1 else ''}"
    return f"{months} Months"


def shape(p: dict) -> dict:
    """A catalogue entry as the website lists it."""
    fees = [
        f"{f['component']}"
        + (
            f" ({f['category']})"
            if f.get("category") not in (None, "", "general")
            else ""
        )
        + f": ₹{_indian(f['amount'])} {f.get('period') or ''}".rstrip()
        for f in p.get("fees") or []
    ]
    return {
        "code": p["code"],
        "title": p["name"],
        "level": p["level"],
        "faculty": p.get("department") or "",
        "duration": _duration(p.get("duration_months")),
        "intake": f"{p['intake']} Seats" if p.get("intake") else "",
        "fees": fees,
        "url": p.get("source_url") or "",
        "eligibility": bool(p.get("eligibility")),
        "careers": False,
    }


def programmes_for(settings: Settings, tenant: UUID, now=time.monotonic) -> list[dict]:
    with _lock:
        hit = _cache.get(tenant)
    if hit and now() - hit[0] < FRESH_S:
        return hit[1]
    try:
        data = knowledge_get(settings, "/catalogue/programs", tenant, {"facts": "true"})
        items = [shape(p) for p in data.get("items", [])]
    except (UpstreamError, AttributeError, KeyError, TypeError):
        return hit[1] if hit else []
    with _lock:
        _cache[tenant] = (now(), items)
    return items


@router.get(
    "/widget/programmes",
    summary="The college's programmes as its website lists them, from the knowledge base that "
    "builds itself from the college's website (public, read-only)",
)
def programmes(
    public_key: str = Query(..., min_length=4, max_length=100),
    settings: Settings = Depends(settings_dep),
):
    with tx(settings.database_url, None) as cur:
        cur.execute("select * from app.tenant_by_public_key(%s)", (public_key,))
        t = cur.fetchone()
        if t:
            cur.execute("select app.tenant_status(%s) as s", (t["id"],))
            live = cur.fetchone()["s"] == "live"
    if not t or not live:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown widget key")
    return {"items": programmes_for(settings, t["id"])}
