import base64
import time

from experience_api.security import (
    hash_code,
    hash_password,
    make_token,
    new_numeric_code,
    password_problem,
    read_token,
    totp_code,
    verify_password,
    verify_totp,
)

SECRET = "s" * 40


def test_password_hash_round_trip():
    stored = hash_password("Correct-Horse-9")
    assert stored.startswith("scrypt$")
    assert verify_password("Correct-Horse-9", stored)
    assert not verify_password("correct-horse-9", stored)


def test_same_password_different_hashes():
    assert hash_password("Correct-Horse-9") != hash_password("Correct-Horse-9")


def test_verify_password_rejects_garbage():
    assert not verify_password("x", "not-a-hash")


def test_password_rules():
    assert password_problem("short1A")
    assert password_problem("alllowercase123")
    assert password_problem("NoDigitsHereAtAll")
    assert password_problem("Good-Password-2026") is None


# The published test value from RFC 6238 appendix B, not a real secret.
RFC6238_SECRET = base64.b32encode(b"12345678901234567890").decode()


def test_totp_rfc6238_vector():
    # RFC 6238 appendix B, SHA-1 secret "12345678901234567890", T=59 -> 94287082 (last 6 digits)
    secret = RFC6238_SECRET
    assert totp_code(secret, at=59) == "287082"
    assert totp_code(secret, at=1111111109) == "081804"


def test_verify_totp_window():
    secret = RFC6238_SECRET
    now = time.time()
    assert verify_totp(secret, totp_code(secret, at=now))
    assert verify_totp(secret, totp_code(secret, at=now - 30))
    assert not verify_totp(secret, totp_code(secret, at=now - 120))


def test_tokens_check_type_and_signature():
    token = make_token(SECRET, "access", 5, sub="a", tid="b", role="faculty")
    assert read_token(SECRET, token, "access")["role"] == "faculty"
    assert read_token(SECRET, token, "visitor") is None
    assert read_token("t" * 40, token, "access") is None


def test_expired_token_rejected():
    assert (
        read_token(SECRET, make_token(SECRET, "access", -1, sub="a"), "access") is None
    )


def test_codes():
    code = new_numeric_code()
    assert len(code) == 6 and code.isdigit()
    assert hash_code(code, "salt-1") != hash_code(code, "salt-2")


# ------------------------------------------------------------------ production key handling (Day 8)
def _rsa_key_file(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "jwt.pem"
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(path)


def test_rs256_tokens_cannot_be_forged_with_a_shared_secret(tmp_path):
    from dataclasses import replace

    import jwt as pyjwt

    from experience_api.config import Settings, token_keys
    from experience_api.security import make_token, read_token

    s = replace(
        Settings(), jwt_private_key_file=_rsa_key_file(tmp_path), jwt_secret="x" * 40
    )
    keys = token_keys(s)
    assert keys.alg == "RS256" and b"PUBLIC KEY" in keys.verify
    good = make_token(keys, "access", 5, sub="u", tid="t", role="college_admin")
    assert read_token(keys, good, "access")["role"] == "college_admin"
    forged = pyjwt.encode(
        {"typ": "access", "sub": "u", "tid": "t", "role": "college_admin"},
        "x" * 40,
        algorithm="HS256",
    )
    assert read_token(keys, forged, "access") is None


def test_production_refuses_unsafe_configuration(monkeypatch, tmp_path):
    from dataclasses import replace

    import pytest

    from experience_api.config import Settings, check_production

    for name in ("DEV_ECHO_CODES", "DEV_MFA_CODE", "SERVICE_KEY"):
        monkeypatch.delenv(name, raising=False)
    prod = replace(
        Settings(), app_env="production", jwt_private_key_file="", service_key="k"
    )
    with pytest.raises(RuntimeError, match="JWT_PRIVATE_KEY_FILE is required"):
        check_production(prod)
    ok = replace(prod, jwt_private_key_file=_rsa_key_file(tmp_path))
    check_production(ok)  # a safe production set-up starts
    monkeypatch.setenv("DEV_MFA_CODE", "123456")
    with pytest.raises(RuntimeError, match="DEV_MFA_CODE is a development setting"):
        check_production(ok)
    check_production(replace(ok, app_env="development"))  # allowed in development


def test_dev_flags_are_inert_outside_development(monkeypatch):
    from experience_api.config import Settings

    monkeypatch.setenv("DEV_ECHO_CODES", "1")
    monkeypatch.setenv("DEV_MFA_CODE", "123456")
    monkeypatch.setenv("APP_ENV", "production")
    s = Settings()
    assert s.dev_echo_codes is False and s.dev_mfa_code == ""
    monkeypatch.setenv("APP_ENV", "development")
    s = Settings()
    assert s.dev_echo_codes is True and s.dev_mfa_code == "123456"
