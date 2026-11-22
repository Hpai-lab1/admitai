import pytest
from pydantic import ValidationError

from experience_api.schemas import FeedbackIn, LeadIn, TenantConfig, WidgetSessionIn
from experience_api.scoring import band, fallback_score


def test_phone_is_normalised_to_e164():
    assert LeadIn(phone="+1 555 010-0001").phone == "+15550100001"


@pytest.mark.parametrize("bad", ["5550100001", "+0123", "phone", "+1555abc0001"])
def test_bad_phone_rejected(bad):
    with pytest.raises(ValidationError):
        LeadIn(phone=bad)


def test_email_lowercased_and_checked():
    assert LeadIn(email="Student@Example.COM").email == "student@example.com"
    with pytest.raises(ValidationError):
        LeadIn(email="not-an-email")


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        LeadIn(name="A", aadhaar="1234")


def test_feedback_value_ranges():
    assert (
        FeedbackIn(conversation_id="c", message_id="m", kind="thumb", value=-1).value
        == -1
    )
    with pytest.raises(ValidationError):
        FeedbackIn(conversation_id="c", message_id="m", kind="thumb", value=0)
    with pytest.raises(ValidationError):
        FeedbackIn(conversation_id="c", message_id="m", kind="rating", value=6)


def test_only_utm_keys_kept():
    s = WidgetSessionIn(
        public_key="pk_demo_0001", utm={"utm_source": "ig", "evil": "x"}
    )
    assert s.utm == {"utm_source": "ig"}


def test_tenant_colour_must_be_hex():
    with pytest.raises(ValidationError):
        TenantConfig(primary_color="blue")


def test_score_bands():
    assert (
        band(70) == "hot"
        and band(69) == "warm"
        and band(40) == "warm"
        and band(39) == "cold"
    )


def test_fallback_score_is_bounded():
    assert fallback_score(["B.Tech"], True, True, True, True) == 90
    assert fallback_score([], False, False, False, False) == 20


def test_email_check_accepts_real_shapes_and_is_fast_on_crafted_input():
    """CodeQL py/polynomial-redos (Hpai-lab1/admitai PR 17, 22 Nov 2026)."""
    import time

    from experience_api.schemas import check_email

    assert (
        check_email("Name.Surname+x@college.example")
        == "name.surname+x@college.example"
    )
    for bad in [
        "name@college",
        "name@.example",
        "name@college..example",
        "a b@c.example",
    ]:
        with pytest.raises(ValueError):
            check_email(bad)
    start = time.perf_counter()
    with pytest.raises(ValueError):
        check_email("!@!." + "!." * 20_000 + " x")
    assert time.perf_counter() - start < 0.5


def test_a_generated_demo_password_is_saved_privately_not_returned(
    tmp_path, monkeypatch
):
    import os
    import stat

    from experience_api import seed

    monkeypatch.setattr(
        seed, "PASSWORD_FILE", tmp_path / ".local" / "demo-password.txt"
    )
    shown = seed._saved("Secret-Demo-1Aa")
    assert "Secret-Demo-1Aa" not in shown and "demo-password.txt" in shown
    assert seed.PASSWORD_FILE.read_text().strip() == "Secret-Demo-1Aa"
    assert stat.S_IMODE(os.stat(seed.PASSWORD_FILE).st_mode) == 0o600
