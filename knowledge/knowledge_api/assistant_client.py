"""Calls from Module A to Module B (PRD 21.4: B -> A trace ids and versions on every message;
/chat usable headless by the eval runner). A small interface so tests can substitute a fake."""

from __future__ import annotations

import json
import time
from typing import Protocol

import httpx

from .config import Settings


class AssistantError(RuntimeError):
    pass


class Assistant(Protocol):
    def message(self, tenant: str, message_id: str) -> dict | None: ...
    def signals(self, tenant: str, since: str | None) -> list[dict]: ...
    def ask(
        self, tenant: str, conversation_id: str, text: str, channel: str = "web_chat"
    ) -> dict: ...
    def unsupported(self, tenant: str, since: str | None) -> list[dict]: ...
    def review(self, tenant: str, message_ids: list[str]) -> dict[str, dict]: ...
    def get_settings(self, tenant: str) -> dict: ...
    def put_settings(self, tenant: str, config: dict) -> None: ...
    def delete_sandbox(self, tenant: str) -> None: ...
    def voice_eval_result(self, tenant: str, result: dict) -> dict: ...


class AssistantClient:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.base = settings.assistant_api_url
        self.key = settings.service_key
        self.client = client or httpx.Client(timeout=30)

    def _h(self, tenant: str) -> dict:
        return {"X-Service-Key": self.key, "X-Tenant-Id": tenant}

    def _get(self, path: str, tenant: str, **params) -> httpx.Response:
        try:
            return self.client.get(
                f"{self.base}{path}", headers=self._h(tenant), params=params
            )
        except httpx.HTTPError as exc:
            raise AssistantError(
                f"Assistant service: {exc.__class__.__name__}"
            ) from exc

    def message(self, tenant: str, message_id: str) -> dict | None:
        r = self._get(f"/messages/{message_id}", tenant)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def signals(self, tenant: str, since: str | None) -> list[dict]:
        params = {"outcomes": "no_answer,blocked,partial"}
        if since:
            params["since"] = since
        r = self._get("/signals", tenant, **params)
        r.raise_for_status()
        return r.json()["items"]

    def unsupported(self, tenant: str, since: str | None) -> list[dict]:
        r = self._get(
            "/reviews/unsupported", tenant, **({"since": since} if since else {})
        )
        r.raise_for_status()
        return r.json()["items"]

    def ask(
        self, tenant: str, conversation_id: str, text: str, channel: str = "web_chat"
    ) -> dict:
        """One headless turn: the released text and the final 'done' event. `channel` web_voice
        makes Module B answer as it does on voice (voice model and style), for PRD F2.10."""
        out: dict = {"text": "", "done": None, "events": []}
        started = time.monotonic()
        try:
            with self.client.stream(
                "POST",
                f"{self.base}/chat/{conversation_id}/messages",
                json={"text": text, "channel": channel},
                headers=self._h(tenant),
                timeout=180,
            ) as r:
                r.raise_for_status()
                event = None
                for line in r.iter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data = json.loads(line[5:])
                        if event == "token":
                            if "first_token_s" not in out:
                                out["first_token_s"] = time.monotonic() - started
                            out["text"] += data["text"]
                        elif event == "done":
                            out["done"] = data
                        else:
                            out["events"].append(event)
        except httpx.HTTPError as exc:
            raise AssistantError(
                f"Assistant service: {exc.__class__.__name__}"
            ) from exc
        out["text"] = out["text"].strip()
        out["total_s"] = time.monotonic() - started
        return out

    def review(self, tenant: str, message_ids: list[str]) -> dict[str, dict]:
        """Claim-level grounding review of these answers (B's judge; reused if already done)."""
        out: dict[str, dict] = {}
        for i in range(0, len(message_ids), 100):
            try:
                r = self.client.post(
                    f"{self.base}/reviews/messages",
                    json={"message_ids": message_ids[i : i + 100]},
                    headers=self._h(tenant),
                    timeout=600,
                )
                r.raise_for_status()
            except httpx.HTTPError as exc:
                raise AssistantError(
                    f"Assistant service: {exc.__class__.__name__}"
                ) from exc
            out.update(r.json()["items"])
        return out

    def get_settings(self, tenant: str) -> dict:
        r = self._get("/tenant-settings", tenant)
        r.raise_for_status()
        return r.json()

    def put_settings(self, tenant: str, config: dict) -> None:
        r = self.client.put(
            f"{self.base}/tenant-settings", json=config, headers=self._h(tenant)
        )
        r.raise_for_status()

    def delete_sandbox(self, tenant: str) -> None:
        r = self.client.delete(f"{self.base}/tenant-data", headers=self._h(tenant))
        r.raise_for_status()

    def voice_eval_result(self, tenant: str, result: dict) -> dict:
        """PRD F2.10: tell Module B how voice scored against chat on the test set."""
        try:
            r = self.client.post(
                f"{self.base}/voice/eval-result", json=result, headers=self._h(tenant)
            )
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise AssistantError(
                f"Assistant service: {exc.__class__.__name__}"
            ) from exc
        return r.json()
