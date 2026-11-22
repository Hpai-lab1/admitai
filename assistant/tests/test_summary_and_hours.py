"""F1.3 conversation summary and the F3.1 office-hours rule."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg
import pytest

from assistant_api import hours, store
from assistant_api.context import conversation_summary
from conftest import TEST_ADMIN_URL, FakeLLM, needs_db, visitor_token

IST = ZoneInfo("Asia/Kolkata")


# ------------------------------------------------------------------ office hours


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Mon-Sat 09:00-17:00", {d: ("09:00", "17:00") for d in range(6)}),
        ("Mon-Fri 10:00-16:00", {d: ("10:00", "16:00") for d in range(5)}),
        (
            "Mon-Fri 09:30-17:30, Sat 09:00-13:00",
            {**{d: ("09:30", "17:30") for d in range(5)}, 5: ("09:00", "13:00")},
        ),
        ("Monday to Friday 9.00 to 17.00", {d: ("09:00", "17:00") for d in range(5)}),
        ("Fri-Mon 10:00-14:00", {d: ("10:00", "14:00") for d in (4, 5, 6, 0)}),
        ("whenever someone is free", {}),
        (None, {}),
    ],
)
def test_office_hours_text_is_read(text, expected):
    got = {
        d: (a.strftime("%H:%M"), b.strftime("%H:%M"))
        for d, (a, b) in hours.parse(text).items()
    }
    assert got == expected


def test_open_closed_and_next_opening():
    sat_evening = datetime(2026, 10, 31, 19, 0, tzinfo=IST)  # Saturday
    st = hours.status("Mon-Sat 09:00-17:00", "Asia/Kolkata", sat_evening)
    assert st["open"] is False and st["next_open"].startswith(
        "2026-11-02T09:00"
    )  # Monday
    assert hours.spoken_time(st["next_open"], "en", sat_evening) == "Monday at 9:00"
    wed_noon = datetime(2026, 10, 28, 12, 0, tzinfo=IST)
    assert hours.status("Mon-Sat 09:00-17:00", "Asia/Kolkata", wed_noon)["open"] is True
    early = datetime(2026, 10, 28, 7, 0, tzinfo=IST)
    nxt = hours.status("Mon-Sat 09:00-17:00", None, early)["next_open"]
    assert hours.spoken_time(nxt, "en", early) == "today at 9:00"
    assert hours.status("not readable", None, early) is None


def _events(text):
    out = []
    for block in text.split("\n\n"):
        if block.strip():
            name = block.split("\n")[0].removeprefix("event: ")
            out.append((name, json.loads(block.split("data: ", 1)[1])))
    return out


@needs_db
def test_a_handoff_outside_office_hours_says_when_the_team_is_back(
    make_client, settings, tenant
):
    store.save_tenant_config(
        settings.database_url,
        tenant,
        {"office_hours": "Mon-Sat 09:00-17:00", "timezone": "Asia/Kolkata"},
    )
    client, deps = make_client(FakeLLM())
    deps.clock = lambda: datetime(2026, 10, 31, 21, 30, tzinfo=IST)  # Saturday night
    r = client.post(
        "/chat/c-hours-1/messages",
        json={"text": "I want to talk to a person"},
        headers={"Authorization": f"Bearer {visitor_token(tenant)}"},
    )
    events = _events(r.text)
    text = "".join(d["text"] for n, d in events if n == "token")
    assert "Mon-Sat 09:00-17:00" in text and "Monday at 9:00" in text
    handoff = next(d for n, d in events if n == "handoff_requested")
    assert handoff["office_hours"]["open"] is False
    assert handoff["office_hours"]["next_open"].startswith("2026-11-02T09:00")


@needs_db
def test_during_office_hours_nothing_extra_is_said(make_client, settings, tenant):
    store.save_tenant_config(
        settings.database_url, tenant, {"office_hours": "Mon-Sat 09:00-17:00"}
    )
    client, deps = make_client(FakeLLM())
    deps.clock = lambda: datetime(2026, 10, 28, 11, 0, tzinfo=IST)  # Wednesday morning
    r = client.post(
        "/chat/c-hours-2/messages",
        json={"text": "I want to talk to a person"},
        headers={"Authorization": f"Bearer {visitor_token(tenant)}"},
    )
    events = _events(r.text)
    text = "".join(d["text"] for n, d in events if n == "token")
    assert "outside those hours" not in text
    assert (
        next(d for n, d in events if n == "handoff_requested")["office_hours"]["open"]
        is True
    )


# ------------------------------------------------------------------ conversation summary


def test_the_summary_lists_earlier_questions_and_masks_contact_details():
    earlier = [
        {"sender": "user", "content": "What is the fee for CSE?"},
        {"sender": "assistant", "content": "The fee is ..."},
        {"sender": "user", "content": "Write to asha@example.com about hostels"},
    ]
    s = conversation_summary(earlier, ["B.Tech Computer Science and Engineering"])
    assert (
        "What is the fee for CSE?" in s
        and "[email]" in s
        and "asha@example.com" not in s
    )
    assert "programmes discussed: B.Tech Computer Science and Engineering" in s
    assert conversation_summary([], []) is None


@needs_db
def test_after_three_exchanges_earlier_questions_reach_the_prompt_as_a_summary(
    make_client, tenant
):
    answers = ["Hostel gates close at 21:30 [1]."] * 5
    llm = FakeLLM(*answers)
    client, deps = make_client(llm)
    token = visitor_token(tenant)
    questions = [
        "When do the hostel gates close?",
        "Are hostels separate for men and women?",
        "When do the hostel gates close on weekends?",
        "Do hostel gates close earlier in winter?",
        "Can parents visit before the hostel gates close?",
    ]
    for q in questions:
        client.post(
            "/chat/c-sum-1/messages",
            json={"text": q},
            headers={"Authorization": f"Bearer {token}"},
        )
    last_prompt = llm.calls[-1]["messages"][1]["content"]
    assert last_prompt.startswith(
        "Summary of the earlier conversation: the visitor earlier asked: "
    )
    assert (
        questions[0] in last_prompt.split("Conversation so far:")[0]
    )  # summarised, not verbatim
    recent = last_prompt.split("Conversation so far:")[1]
    assert questions[0] not in recent and questions[3] in recent
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        summary = conn.execute(
            "select summary from chat.conversations where id = 'c-sum-1' and tenant_id = %s",
            (tenant,),
        ).fetchone()[0]
    assert questions[-1] in summary


def test_without_a_time_zone_database_office_hours_fall_back_to_utc(monkeypatch):
    """Windows has no system time-zone data (tzdata provides it); missing data must not crash a
    handoff."""
    from zoneinfo import ZoneInfoNotFoundError

    def missing(_name):
        raise ZoneInfoNotFoundError("no time zone data")

    monkeypatch.setattr(hours, "ZoneInfo", missing)
    st = hours.status("Mon-Sat 09:00-17:00", "Asia/Kolkata")
    assert st is not None and st["timezone"] == "UTC" and "open" in st
