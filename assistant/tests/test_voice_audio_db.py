"""Consented voice recordings (PRD F2.6): off by default, kept only when the college turned it on
AND the visitor consented (recorded in Module C first), expiring after retention.audio_days, and
gone on erasure or when audio consent is withdrawn."""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient

from assistant_api import audio_store, pack, privacy, store
from assistant_api.engine import Deps
from assistant_api.experience_client import ExperienceError
from assistant_api.guard import RateLimiter
from assistant_api.main import create_app
from conftest import FakeKnowledge, FakeLLM, needs_db, visitor_token
from test_voice import FakeSpeech

pytestmark = needs_db


class Exp:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def consent(self, tenant, session, purpose, version, channel):
        if self.fail:
            raise ExperienceError("down")
        self.calls.append((purpose, version, channel))


def client(settings, exp=None, heard="What is the hostel fee?"):
    app = create_app(
        settings,
        Deps(
            settings,
            FakeKnowledge(),
            FakeLLM("The hostel fee is 90000 INR [1]."),
            RateLimiter(100, 60),
        ),
        exp or Exp(),
        speech=FakeSpeech(heard),
    )
    return TestClient(app)


def enable(settings, tenant, days=None):
    cfg = {"voice": {"store_audio": True}}
    if days:
        cfg["retention"] = {"audio_days": days}
    store.save_tenant_config(settings.database_url, tenant, cfg)


def token(c, tenant, session, consent, conv="a1"):
    r = c.post(
        "/voice/token",
        json={"conversation_id": conv, "audio_consent": consent},
        headers={"Authorization": f"Bearer {visitor_token(tenant, session)}"},
    )
    assert r.status_code == 200
    return r.json()


def turn(c, tok):
    return c.post(
        "/voice/turn",
        data={"token": tok},
        files={"audio": ("q.webm", b"webm-bytes", "audio/webm")},
    )


def test_off_by_default_even_with_consent(settings, tenant):
    exp = Exp()
    c = client(settings, exp)
    body = token(c, tenant, str(uuid.uuid4()), consent=True)
    assert body["audio_recording"] == "off" and body["audio_days"] is None
    claims = jwt.decode(
        body["token"], settings.voice_token_secret, algorithms=["HS256"]
    )
    assert (
        "aud_rec" not in claims and exp.calls == []
    )  # no consent asked for, none recorded
    assert turn(c, body["token"]).status_code == 200
    assert audio_store.listing(settings.database_url, tenant, "a1") == []


def test_college_on_and_visitor_consents_keeps_the_question_for_30_days(
    settings, tenant
):
    enable(settings, tenant)
    exp = Exp()
    c = client(settings, exp)
    session = str(uuid.uuid4())
    offered = token(c, tenant, session, consent=False)
    assert offered["audio_recording"] == "offered" and offered["audio_days"] == 30
    assert turn(c, offered["token"]).status_code == 200
    assert audio_store.listing(settings.database_url, tenant, "a1") == []  # no consent

    body = token(c, tenant, session, consent=True)
    assert body["audio_recording"] == "on"
    assert exp.calls == [("audio_recording", "voice-audio-v1", "web_voice")]
    assert turn(c, body["token"]).status_code == 200
    (row,) = audio_store.listing(settings.database_url, tenant, "a1")
    assert (
        row["bytes"] == len(b"webm-bytes")
        and row["transcript"] == "What is the hostel fee?"
    )
    assert row["expires_at"] - row["created_at"] == timedelta(days=30)


def test_no_recording_when_the_consent_cannot_be_recorded(settings, tenant):
    enable(settings, tenant)
    c = client(settings, Exp(fail=True))
    body = token(c, tenant, str(uuid.uuid4()), consent=True)
    assert body["audio_recording"] == "offered"
    turn(c, body["token"])
    assert audio_store.listing(settings.database_url, tenant, "a1") == []


def test_turning_storage_off_stops_recording_even_for_a_consented_token(
    settings, tenant
):
    enable(settings, tenant)
    c = client(settings)
    body = token(c, tenant, str(uuid.uuid4()), consent=True)
    store.save_tenant_config(
        settings.database_url, tenant, {"voice": {"store_audio": False}}
    )
    turn(c, body["token"])
    assert audio_store.listing(settings.database_url, tenant, "a1") == []


def test_expiry_erasure_and_withdrawal_remove_recordings(settings, tenant):
    db = settings.database_url
    s1, s2 = str(uuid.uuid4()), str(uuid.uuid4())
    store.open_conversation(db, tenant, "old", s1, "web_voice")
    store.open_conversation(db, tenant, "mine", s1, "web_voice")
    store.open_conversation(db, tenant, "theirs", s2, "web_voice")
    past = datetime.now(timezone.utc) - timedelta(days=31)
    audio_store.save(db, tenant, "old", s1, "livekit", b"x", "old q", 30, now=past)
    audio_store.save(db, tenant, "mine", s1, "livekit", b"y", "q", 30)
    audio_store.save(db, tenant, "theirs", s2, "web_voice", b"z", "q", 30)
    r = privacy.retention_sweep(db, tenant)
    assert r["recordings_expired"] == 1
    assert audio_store.listing(db, tenant, "old") == []

    c = TestClient(
        create_app(
            settings, Deps(settings, FakeKnowledge(), FakeLLM(), RateLimiter(100, 60))
        )
    )
    svc = {"X-Service-Key": settings.service_key, "X-Tenant-Id": tenant}
    r = c.post("/privacy/erase", json={"session_id": s2, "scope": "audio"}, headers=svc)
    assert r.status_code == 200 and r.json()["recordings"] == 1
    assert audio_store.listing(db, tenant, "theirs") == []
    assert (
        store.history(db, tenant, "theirs", 10) == []
    )  # transcripts untouched (none here)
    r = c.post("/privacy/erase", json={"session_id": s1}, headers=svc)
    assert r.json()["recordings"] == 1
    assert audio_store.listing(db, tenant, "mine") == []


def test_another_college_cannot_see_the_recordings(settings, tenant):
    db = settings.database_url
    store.open_conversation(db, tenant, "c", None, "web_voice")
    audio_store.save(db, tenant, "c", "s-1", "livekit", b"x", "q", 30)
    assert audio_store.listing(db, str(uuid.uuid4()), "c") == []
    assert len(audio_store.listing(db, tenant, "c")) == 1


def test_pack_values_are_checked(settings):
    assert audio_store.problems({"voice": {"store_audio": "yes"}}) == [
        "voice.store_audio must be true or false"
    ]
    assert audio_store.problems({"retention": {"audio_days": 0}})
    assert audio_store.problems({"retention": {"audio_days": True}})
    assert (
        audio_store.problems(
            {"voice": {"store_audio": True}, "retention": {"audio_days": 30}}
        )
        == []
    )
    cfg = pack.agent_config({"college": {"name": "X"}, "voice": {"store_audio": True}})
    assert cfg["voice"] == {"store_audio": True}
    assert pack.agent_config({"college": {"name": "X"}})["voice"] == {
        "store_audio": False
    }


def test_tenant_settings_refuse_a_bad_audio_value(settings, tenant):
    c = TestClient(
        create_app(
            settings, Deps(settings, FakeKnowledge(), FakeLLM(), RateLimiter(100, 60))
        )
    )
    r = c.put(
        "/tenant-settings",
        json={"retention": {"audio_days": 9999}},
        headers={"X-Service-Key": settings.service_key, "X-Tenant-Id": tenant},
    )
    assert r.status_code == 422


def test_withdrawal_and_erasure_stop_recording_at_once_even_with_an_old_token(
    settings, tenant
):
    """F2.6: a voice token lives 10 minutes; withdrawing consent must not wait for it."""
    enable(settings, tenant)
    c = client(settings)
    session = str(uuid.uuid4())
    body = token(c, tenant, session, consent=True)
    svc = {"X-Service-Key": settings.service_key, "X-Tenant-Id": tenant}
    turn(c, body["token"])
    assert len(audio_store.listing(settings.database_url, tenant, "a1")) == 1
    r = c.post(
        "/privacy/erase", json={"session_id": session, "scope": "audio"}, headers=svc
    )
    assert r.json()["recordings"] == 1
    turn(c, body["token"])  # the same, still valid token
    assert audio_store.listing(settings.database_url, tenant, "a1") == []
    # consenting again (a new token with the box ticked) lifts the withdrawal
    again = token(c, tenant, session, consent=True)
    turn(c, again["token"])
    assert len(audio_store.listing(settings.database_url, tenant, "a1")) == 1
    # a full erasure request also stops recording on the old token
    c.post("/privacy/erase", json={"session_id": session}, headers=svc)
    turn(c, again["token"])
    assert audio_store.listing(settings.database_url, tenant, "a1") == []


def test_no_recording_without_a_visitor_session(settings, tenant):
    db = settings.database_url
    store.open_conversation(db, tenant, "c", None, "web_voice")
    assert audio_store.save(db, tenant, "c", None, "livekit", b"x", "q", 30) is None
