"""Rate limits (PRD 12.3: 429 with Retry-After), shared by every API process through the database
(migration 011), so running more processes does not multiply the limits.

- Starting widget sessions, per network: session creation is anonymous, so without it one script
  could create sessions, and visitor tokens, without end.
- Failed sign-ins and two-factor codes: only failures count, so someone who signs in correctly is
  never slowed down.

Keys are stored as HMACs, so no network address or e-mail is readable in the table.
"""

from __future__ import annotations

import hashlib
import hmac

from fastapi import HTTPException, Request, status

from .config import Settings
from .db import tx


def _key(settings: Settings, name: str, key: str) -> str:
    secret = (settings.jwt_secret or settings.service_key or "admitai-rate").encode()
    return hmac.new(secret, f"{name}|{key}".encode(), hashlib.sha256).hexdigest()


class SlidingWindow:
    """At most `limit` tries per `window` seconds; every allowed try counts."""

    def __init__(self, settings: Settings, name: str, limit: int, window: float = 60.0):
        self.settings, self.name, self.limit, self.window = (
            settings,
            name,
            limit,
            int(window),
        )

    def check(self, key: str) -> int:
        """0 if allowed (and counted), else whole seconds until the key may try again."""
        return _wait(
            self.settings,
            _key(self.settings, self.name, key),
            self.window,
            self.limit,
            True,
        )


class FailureLimit:
    """At most `limit` failures per `window` seconds; a success clears the count."""

    def __init__(self, settings: Settings, name: str, limit: int, window: float):
        self.settings, self.name, self.limit, self.window = (
            settings,
            name,
            limit,
            int(window),
        )

    def wait(self, key: str) -> int:
        """0 if this key may try, else whole seconds until it may."""
        k = _key(self.settings, self.name, key)
        return _wait(self.settings, k, self.window, self.limit, False)

    def fail(self, key: str) -> None:
        with tx(self.settings.database_url, None) as cur:
            cur.execute(
                "select app.rate_fail(%s)", (_key(self.settings, self.name, key),)
            )

    def clear(self, key: str) -> None:
        with tx(self.settings.database_url, None) as cur:
            cur.execute(
                "select app.rate_clear(%s)", (_key(self.settings, self.name, key),)
            )


def _wait(settings: Settings, key: str, window: int, limit: int, take: bool) -> int:
    with tx(settings.database_url, None) as cur:
        cur.execute(
            "select app.rate_wait(%s, %s, %s, %s) as wait", (key, window, limit, take)
        )
        return cur.fetchone()["wait"]


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
    limit = SlidingWindow(settings, "sessions", settings.sessions_per_ip_minute)
    wait = limit.check(client_ip(request, settings))
    if wait:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many chats started from this network. Please wait a moment.",
            headers={"Retry-After": str(wait)},
        )


# Guessing is slow. Per person and network, so one attacker cannot lock a member of staff out from
# elsewhere; per network, so they cannot try many accounts. Two-factor codes are short, so they
# have the tightest limit, per person.
LOGIN_FAILURES, LOGIN_WINDOW = 10, 15 * 60
NETWORK_FAILURES = 50
MFA_FAILURES, MFA_WINDOW = 5, 5 * 60


def login_limits(settings: Settings) -> tuple[FailureLimit, FailureLimit, FailureLimit]:
    return (
        FailureLimit(settings, "login", LOGIN_FAILURES, LOGIN_WINDOW),
        FailureLimit(settings, "login-network", NETWORK_FAILURES, LOGIN_WINDOW),
        FailureLimit(settings, "mfa", MFA_FAILURES, MFA_WINDOW),
    )


def _refuse(wait: int) -> None:
    if wait:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many failed attempts. Please wait and try again.",
            headers={"Retry-After": str(wait)},
        )


def check_login(request: Request, settings: Settings, who: str) -> tuple[str, str]:
    """Refuse if blocked; returns the keys to report the attempt's result under."""
    person, network, _ = login_limits(settings)
    ip = client_ip(request, settings)
    keys = (f"{who.lower()}|{ip}", ip)
    _refuse(person.wait(keys[0]) or network.wait(keys[1]))
    return keys


def login_failed(settings: Settings, keys: tuple[str, str]) -> None:
    person, network, _ = login_limits(settings)
    person.fail(keys[0])
    network.fail(keys[1])


def login_passed(settings: Settings, keys: tuple[str, str]) -> None:
    login_limits(settings)[0].clear(keys[0])


def check_mfa(settings: Settings, who: str) -> None:
    _refuse(login_limits(settings)[2].wait(who))


def mfa_failed(settings: Settings, who: str) -> None:
    login_limits(settings)[2].fail(who)


def mfa_passed(settings: Settings, who: str) -> None:
    login_limits(settings)[2].clear(who)
