"""Per-IP limit on starting widget sessions (PRD 12.3: 429 with Retry-After). Session creation is
anonymous, so without it one script could create sessions, and visitor tokens, without end."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from .config import Settings


class SlidingWindow:
    def __init__(self, limit: int, window: float = 60.0):
        self.limit, self.window = limit, window
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> int:
        """0 if allowed (and counted), else whole seconds until the key may try again."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return max(1, int(q[0] + self.window - now + 0.999))
            q.append(now)
            return 0


def client_ip(request: Request, settings: Settings) -> str:
    # X-Forwarded-For is believed only behind a proxy we run; otherwise it is the caller's claim.
    if settings.trust_proxy_headers:
        sent = (
            request.headers.get("cf-connecting-ip")
            or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        )
        if sent:
            return sent
    return request.client.host if request.client else "unknown"


def limit_sessions(request: Request, settings: Settings) -> None:
    state = request.app.state
    if not hasattr(state, "session_limit"):
        state.session_limit = SlidingWindow(settings.sessions_per_ip_minute)
    wait = state.session_limit.check(client_ip(request, settings))
    if wait:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many chats started from this network. Please wait a moment.",
            headers={"Retry-After": str(wait)},
        )
