"""Settings from environment variables (a git-ignored .env file locally; see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .security import TokenKeys


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader so no extra package is needed; real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _dev() -> bool:
    return os.environ.get("APP_ENV", "production") == "development"


@dataclass(frozen=True)
class Settings:
    # The API connects as the RLS-restricted role; migrations use the admin URL.
    database_url: str = field(
        default_factory=lambda: os.environ.get("DATABASE_URL", "")
    )
    admin_database_url: str = field(
        default_factory=lambda: os.environ.get("ADMIN_DATABASE_URL", "")
    )
    # "development" turns on DEV_* conveniences; anything else is treated as production, so a
    # server that forgets the variable is safe by default.
    app_env: str = field(
        default_factory=lambda: os.environ.get("APP_ENV", "production")
    )
    jwt_secret: str = field(default_factory=lambda: os.environ.get("JWT_SECRET", ""))
    # Production: RS256 private key (PEM file). Module B gets only the public key.
    jwt_private_key_file: str = field(
        default_factory=lambda: os.environ.get("JWT_PRIVATE_KEY_FILE", "")
    )
    # Inbound: the key Module B sends as X-Service-Key (/handoffs, /consents, /leads, ...).
    service_key: str = field(
        default_factory=lambda: os.environ.get("KEY_B_TO_C", "")
        or (os.environ.get("SERVICE_KEY", "") if _dev() else "")
    )
    # Inbound: operator tooling (bootstrap, checks). Never give it to another module.
    ops_key: str = field(default_factory=lambda: os.environ.get("KEY_OPS", ""))
    # Outbound: keys this module presents to Module A and Module B.
    key_to_knowledge: str = field(
        default_factory=lambda: os.environ.get("KEY_C_TO_A", "")
        or (os.environ.get("SERVICE_KEY", "") if _dev() else "")
    )
    key_to_assistant: str = field(
        default_factory=lambda: os.environ.get("KEY_C_TO_B", "")
        or (os.environ.get("SERVICE_KEY", "") if _dev() else "")
    )
    # Module A's key for relaying knowledge events to the college's webhooks (PRD 12.2).
    key_from_knowledge: str = field(
        default_factory=lambda: os.environ.get("KEY_A_TO_C", "")
        or (os.environ.get("SERVICE_KEY", "") if _dev() else "")
    )
    knowledge_api_url: str = field(
        default_factory=lambda: os.environ.get(
            "KNOWLEDGE_API_URL", "http://127.0.0.1:8230/a"
        )
    )
    assistant_api_url: str = field(
        default_factory=lambda: os.environ.get(
            "ASSISTANT_API_URL", "http://127.0.0.1:8230/b"
        )
    )
    # PRD 12.3 rate limits (per IP); proxy headers only behind a proxy we run.
    sessions_per_ip_minute: int = field(
        default_factory=lambda: int(os.environ.get("SESSIONS_PER_IP_MINUTE", "30"))
    )
    trust_proxy_headers: bool = field(
        default_factory=lambda: os.environ.get("TRUST_PROXY_HEADERS") == "1"
    )
    web_origin: str = field(
        default_factory=lambda: os.environ.get("WEB_ORIGIN", "http://127.0.0.1:8210")
    )
    # Development only: return guardian codes in the API response instead of sending them.
    dev_echo_codes: bool = field(
        default_factory=lambda: os.environ.get("DEV_ECHO_CODES", "") == "1" and _dev()
    )
    # Development only: a fixed sign-in code accepted in addition to the authenticator app's code.
    # Ignored unless DEV_ECHO_CODES=1, so it can never weaken a real deployment.
    dev_mfa_code: str = field(
        default_factory=lambda: os.environ.get("DEV_MFA_CODE", "") if _dev() else ""
    )
    # E-mail through the college's SMTP server or a free-tier service (PRD 15). Unset: nothing is
    # e-mailed, messages wait in app.message_outbox, and guardian codes by e-mail are refused.
    smtp_host: str = field(default_factory=lambda: os.environ.get("SMTP_HOST", ""))
    smtp_port: int = field(
        default_factory=lambda: int(os.environ.get("SMTP_PORT", "587"))
    )
    smtp_user: str = field(default_factory=lambda: os.environ.get("SMTP_USER", ""))
    smtp_password: str = field(
        default_factory=lambda: os.environ.get("SMTP_PASSWORD", "")
    )
    smtp_from: str = field(default_factory=lambda: os.environ.get("SMTP_FROM", ""))
    # STARTTLS unless SMTP_STARTTLS=0 (only for a local test relay).
    smtp_starttls: bool = field(
        default_factory=lambda: os.environ.get("SMTP_STARTTLS", "1") != "0"
    )
    access_minutes: int = 15  # PRD 5.4
    refresh_days: int = 7  # PRD 5.4
    visitor_hours: int = 24  # PRD 5.4
    guardian_code_minutes: int = 10


@lru_cache(maxsize=8)
def _read_private_key(path: str) -> TokenKeys:
    from cryptography.hazmat.primitives import serialization

    pem = Path(path).read_bytes()
    public = (
        serialization.load_pem_private_key(pem, password=None)
        .public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    return TokenKeys("RS256", pem, public)


def token_keys(s: Settings) -> TokenKeys:
    if s.jwt_private_key_file:
        return _read_private_key(s.jwt_private_key_file)
    return TokenKeys("HS256", s.jwt_secret, s.jwt_secret)


def check_production(s: Settings) -> None:
    """Refuse to start an unsafe production configuration, loudly, instead of running open."""
    if s.app_env == "development":
        return
    problems = []
    if not s.jwt_private_key_file:
        problems.append(
            "JWT_PRIVATE_KEY_FILE is required (RS256); a shared HS256 secret is for development only"
        )
    for name in ("DEV_ECHO_CODES", "DEV_MFA_CODE", "SERVICE_KEY"):
        if os.environ.get(name):
            problems.append(
                f"{name} is a development setting and must not be set in production"
            )
    if not s.service_key:
        problems.append("KEY_B_TO_C is required (the key Module B presents)")
    if problems:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))


def get_settings() -> Settings:
    s = Settings()
    if not s.jwt_private_key_file and (not s.jwt_secret or len(s.jwt_secret) < 32):
        raise RuntimeError(
            "JWT_SECRET must be set (at least 32 characters) in experience/.env"
        )
    return s
