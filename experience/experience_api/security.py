"""Passwords, TOTP (MFA), one-time codes and tokens. Standard library + PyJWT only."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import jwt

# ------------------------------------------------------------------ passwords (scrypt)
_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), digest)


def password_problem(password: str) -> str | None:
    """Minimum rules for staff passwords; None when acceptable."""
    if len(password) < 12:
        return "Use at least 12 characters"
    if (
        password.lower() == password
        or password.upper() == password
        or not any(c.isdigit() for c in password)
    ):
        return "Mix upper- and lower-case letters and at least one digit"
    return None


# ------------------------------------------------------------------ TOTP (RFC 6238), for MFA
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(
    secret: str, at: float | None = None, step: int = 30, digits: int = 6
) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    counter = int((at if at is not None else time.time()) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    now = time.time()
    code = code.strip().replace(" ", "")
    return any(
        hmac.compare_digest(totp_code(secret, now + i * 30), code)
        for i in range(-window, window + 1)
    )


def totp_uri(secret: str, account: str, issuer: str = "AdmitAI") -> str:
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}"


# ------------------------------------------------------------------ one-time codes (guardian verification)
def new_numeric_code(digits: int = 6) -> str:
    return str(secrets.randbelow(10**digits)).zfill(digits)


def hash_code(code: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ------------------------------------------------------------------ JWTs
def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class TokenKeys:
    """How tokens are signed and checked. Production: RS256 with Module C's private key, so other
    modules can verify visitor tokens with the public key but can never mint any token.
    Development and tests: HS256 with one shared secret."""

    alg: str
    sign: str | bytes
    verify: str | bytes


def _keys(keys: "TokenKeys | str") -> TokenKeys:
    return keys if isinstance(keys, TokenKeys) else TokenKeys("HS256", keys, keys)


def make_token(secret: "TokenKeys | str", typ: str, minutes: int, **claims) -> str:
    payload = {
        "typ": typ,
        "iat": _now(),
        "exp": _now() + timedelta(minutes=minutes),
        "jti": str(uuid.uuid4()),
    }
    payload.update(
        {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in claims.items()}
    )
    keys = _keys(secret)
    return jwt.encode(payload, keys.sign, algorithm=keys.alg)


def read_token(secret: "TokenKeys | str", token: str, typ: str) -> dict | None:
    keys = _keys(secret)
    try:
        data = jwt.decode(token, keys.verify, algorithms=[keys.alg])
    except jwt.PyJWTError:
        return None
    return data if data.get("typ") == typ else None
