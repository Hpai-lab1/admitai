"""Degraded mode (PRD 10.2, 18): when the assistant cannot answer, the widget still shows the
college's own approved answers, so a visitor is not left with only an error.

The answers come from Module A's degraded export (the most-used approved curated answers). The
last good copy per college is kept in memory for ten minutes and is served even while Module A is
down, which is when it matters. Only questions and approved answers, never visitor data.
"""

from __future__ import annotations

import threading
import time
from uuid import UUID

from fastapi import APIRouter, Depends

from ..clients import UpstreamError, knowledge_get
from ..config import Settings
from ..db import Context
from ..deps import settings_dep, visitor_context

router = APIRouter(tags=["widget"])

FRESH_S = 600
_cache: dict[UUID, tuple[float, list[dict]]] = {}
_lock = threading.Lock()


def approved_answers(
    settings: Settings, tenant: UUID, now=time.monotonic
) -> list[dict]:
    with _lock:
        hit = _cache.get(tenant)
    if hit and now() - hit[0] < FRESH_S:
        return hit[1]
    try:
        data = knowledge_get(settings, "/export/degraded", tenant)
        items = [
            {"question": str(i["question"]), "answer": str(i["answer"])}
            for i in (data.get("items") or [])
            if i.get("question") and i.get("answer")
        ][:30]
    except (UpstreamError, AttributeError, KeyError, TypeError):
        return hit[1] if hit else []  # Module A down: the last good copy, however old
    with _lock:
        _cache[tenant] = (now(), items)
    return items


@router.get(
    "/widget/fallback-answers",
    summary="The college's approved answers, for the widget to show when the assistant is "
    "unavailable (PRD 10.2 degraded mode)",
)
def fallback_answers(
    ctx: Context = Depends(visitor_context), settings: Settings = Depends(settings_dep)
):
    return {"items": approved_answers(settings, ctx.tenant_id)}
