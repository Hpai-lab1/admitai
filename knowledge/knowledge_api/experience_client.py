"""Module A tells Module C about knowledge changes, which C sends on to the college's own systems
as webhooks (PRD 12.2: improvement.deployed, kb.sync.completed). Best effort: a few quick tries,
then logged. The change itself never depends on it; the event id makes repeats harmless."""

from __future__ import annotations

import logging
import time
import uuid

import httpx

from .config import Settings

log = logging.getLogger("admitai.webhooks")


def notify(settings: Settings, tenant: str, event_type: str, data: dict) -> bool:
    if not (settings.experience_api_url and settings.key_to_experience):
        return False
    body = {"event_id": str(uuid.uuid4()), "type": event_type, "data": data}
    for attempt in range(3):
        try:
            r = httpx.post(
                f"{settings.experience_api_url}/webhooks/events",
                json=body,
                headers={
                    "X-Service-Key": settings.key_to_experience,
                    "X-Tenant-Id": str(tenant),
                },
                timeout=5,
            )
            if r.status_code < 500:
                return r.is_success
        except httpx.HTTPError:
            pass
        time.sleep(0.5 * (attempt + 1))
    log.warning("could not relay %s for tenant %s to Module C", event_type, tenant)
    return False
