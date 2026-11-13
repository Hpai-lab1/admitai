"""Calls from Module B to Module C (PRD 21.4: B records decisions, C owns the data and the people
side): visitor sessions for non-browser channels, consents, leads, callbacks and feedback."""

from __future__ import annotations

from typing import Protocol

import httpx

from .config import Settings


class ExperienceError(RuntimeError):
    pass


class Experience(Protocol):
    def open_session(self, tenant: str, channel: str) -> str: ...
    def consent(
        self, tenant: str, session: str, purpose: str, text_version: str, channel: str
    ) -> None: ...
    def lead(self, tenant: str, session: str, body: dict) -> str: ...
    def handoff(self, tenant: str, session: str, body: dict) -> dict: ...
    def feedback(self, tenant: str, session: str | None, body: dict) -> None: ...
    def erase(self, tenant: str, session: str) -> dict: ...
    def withdraw(
        self, tenant: str, session: str | None, purposes: list[str]
    ) -> dict: ...


class ExperienceClient:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.base = settings.experience_api_url
        self.key = settings.key_to_experience
        self.client = client or httpx.Client(timeout=10)

    def _post(self, path: str, tenant: str, session: str | None, body: dict) -> dict:
        headers = {"X-Service-Key": self.key, "X-Tenant-Id": tenant}
        if session:
            headers["X-Session-Id"] = session
        try:
            r = self.client.post(f"{self.base}{path}", json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise ExperienceError(
                f"Experience service: {exc.__class__.__name__}"
            ) from exc
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = None
            raise ExperienceError(
                detail
                if isinstance(detail, str)
                else f"Experience service: {r.status_code}"
            )
        return r.json() if r.content else {}

    def open_session(self, tenant: str, channel: str) -> str:
        return self._post("/channels/sessions", tenant, None, {"channel": channel})[
            "session_id"
        ]

    def consent(
        self, tenant: str, session: str, purpose: str, text_version: str, channel: str
    ) -> None:
        self._post(
            "/consents",
            tenant,
            session,
            {
                "purpose": purpose,
                "granted": True,
                "text_version": text_version,
                "channel": channel,
            },
        )

    def lead(self, tenant: str, session: str, body: dict) -> str:
        return self._post("/leads", tenant, session, body)["id"]

    def handoff(self, tenant: str, session: str, body: dict) -> dict:
        return self._post("/handoffs", tenant, session, body)

    def feedback(self, tenant: str, session: str | None, body: dict) -> None:
        self._post("/feedback", tenant, session, body)

    def erase(self, tenant: str, session: str) -> dict:
        """Erasure on request: Module C coordinates it, and calls back here for the transcripts."""
        return self._post("/privacy/erase", tenant, session, {})

    def withdraw(self, tenant: str, session: str | None, purposes: list[str]) -> dict:
        """Consent withdrawn (PRD F5 error handling): e.g. WhatsApp STOP withdraws the WhatsApp
        opt-in and marketing; withdrawing contact would also anonymise the person's leads."""
        if not session:
            raise ExperienceError("No visitor session to withdraw consent for")
        return self._post("/consents/withdraw", tenant, session, {"purposes": purposes})
