"""Module B tests. Pure-logic tests always run; database tests need CHAT_TEST_DATABASE_URL and
CHAT_TEST_ADMIN_DATABASE_URL (a throw-away database) and are skipped otherwise. Module A and the
model gateway are replaced by fakes."""

import os
import sys
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault(
    "APP_ENV", "development"
)  # tests use the shared-secret, development set-up
# Tests are hermetic: the developer's real keys (local.env.sh) must not change how tokens are checked.
for _k in (
    "JWT_PRIVATE_KEY_FILE",
    "JWT_PUBLIC_KEY_FILE",
    "VOICE_TOKEN_SECRET",
    "KEY_OPS",
    "KEY_C_TO_A",
    "KEY_B_TO_A",
    "KEY_C_TO_B",
    "KEY_A_TO_B",
    "KEY_B_TO_C",
    "KEY_A_TO_C",
    "KB_EXPERIENCE_API_URL",
):
    os.environ.pop(_k, None)

from assistant_api.clients import UpstreamError  # noqa: E402
from assistant_api.config import Settings  # noqa: E402

TEST_URL = os.environ.get("CHAT_TEST_DATABASE_URL", "")
TEST_ADMIN_URL = os.environ.get("CHAT_TEST_ADMIN_DATABASE_URL", "")
needs_db = pytest.mark.skipif(
    not (TEST_URL and TEST_ADMIN_URL), reason="no chat test database configured"
)
SECRET = "test-secret-" + "x" * 40
SERVICE_KEY = "test-service-key"

PROGRAMS = {
    "items": [
        {
            "id": "p1",
            "code": "BTECH-CSE",
            "name": "B.Tech Computer Science and Engineering",
            "aliases": ["CSE", "Computer Science"],
            "department": "Computer Science",
            "level": "UG",
            "duration_months": 48,
            "intake": 120,
            "eligibility": "10+2 with PCM, at least 50%",
            "contact": None,
            "academic_year": "2027-28",
            "fees": [
                {
                    "category": "general",
                    "component": "tuition",
                    "amount": "250000",
                    "currency": "INR",
                    "period": "per year",
                },
                {
                    "category": "general",
                    "component": "hostel",
                    "amount": "90000",
                    "currency": "INR",
                    "period": "per year",
                },
            ],
            "dates": [{"label": "Last date to apply", "date": "2027-06-30"}],
        }
    ],
    "general_dates": [
        {
            "label": "Last date to apply",
            "date": "2027-06-30",
            "academic_year": "2027-28",
        }
    ],
}


def hit(id_, content, title, score=0.8, kind="chunk", heading="", dept=None):
    return {
        "id": id_,
        "kind": kind,
        "content": content,
        "score": score / 10,
        "vector_score": score,
        "citation": {
            "source_type": "document" if kind == "chunk" else "curated_qa",
            "document_id": id_ if kind == "chunk" else None,
            "qa_id": id_ if kind != "chunk" else None,
            "title": title,
            "heading_path": heading,
            "academic_year": "2027-28",
            "department": dept,
            "url": None,
        },
    }


HOSTEL = hit(
    "d1",
    "Separate hostels for men and women. Hostel gates close at 21:30.",
    "Hostel rules 2027-28",
    heading="Hostel",
)
ELECTIVES = hit(
    "d2",
    "From the fifth semester students choose electives in machine learning and cloud computing.",
    "B.Tech CSE curriculum 2027-28",
    heading="Electives",
    dept="Computer Science",
)


class FakeKnowledge:
    def __init__(self, results=None, fail=False):
        self.results = results if results is not None else [HOSTEL, ELECTIVES]
        self.fail = fail
        self.calls: list[dict] = []

    async def search(self, tenant, query, **kw):
        self.calls.append({"tenant": tenant, "query": query, **kw})
        if self.fail:
            raise UpstreamError("down")
        return {"items": self.results}

    async def programs(self, tenant):
        if self.fail:
            raise UpstreamError("down")
        return PROGRAMS


class FakeLLM:
    """Streams scripted replies (one per answer call, in order). A reply may be an Exception."""

    def __init__(self, *replies, route_reply="ADMISSION"):
        self.replies = list(replies)
        self.route_reply = route_reply
        self.calls: list[dict] = []
        self.chunks_sent = 0

    async def stream(self, model, messages, max_tokens, timeout):
        self.calls.append({"model": model, "messages": messages})
        reply = self.replies.pop(0) if self.replies else "NO_ANSWER"
        if isinstance(reply, Exception):
            raise reply
        for i in range(0, len(reply), 7):  # small chunks, like a real stream
            self.chunks_sent += 1
            yield reply[i : i + 7]

    async def complete(self, model, messages, max_tokens, timeout):
        self.calls.append({"model": model, "router": True})
        return self.route_reply


@pytest.fixture(scope="session")
def migrated():
    from assistant_api import migrate

    with psycopg.connect(TEST_ADMIN_URL, autocommit=True) as conn:
        conn.execute("drop schema if exists chat cascade")
    migrate.migrate(TEST_ADMIN_URL)
    return True


@pytest.fixture
def settings(migrated):
    with psycopg.connect(TEST_ADMIN_URL, autocommit=True) as conn:
        conn.execute(
            "truncate chat.message_citations, chat.traces, chat.messages, chat.conversations,"
            " chat.tenant_settings, chat.channels, chat.wa_messages, chat.wa_outbox,"
            " chat.guardian_codes cascade"
        )
    return replace(
        Settings(),
        database_url=TEST_URL,
        admin_database_url=TEST_ADMIN_URL,
        jwt_secret=SECRET,
        voice_token_secret=SECRET,
        service_key=SERVICE_KEY,
        key_from_experience="key-from-c",
        key_from_knowledge="key-from-a",
        answer_model="answer-model",
        fallback_model="fallback-model",
        # These tests check what the model writes; test_direct and the engine test for
        # catalogue answers turn it back on.
        direct_catalogue_answers=False,
    )


@pytest.fixture
def tenant():
    return str(uuid.uuid4())


def visitor_token(tenant, session=None, secret=SECRET, typ="visitor"):
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "typ": typ,
            "tid": tenant,
            "sid": session or str(uuid.uuid4()),
            "iat": now,
            "exp": now + timedelta(hours=1),
        },
        secret,
        algorithm="HS256",
    )


@pytest.fixture
def make_client(settings):
    from fastapi.testclient import TestClient

    from assistant_api.engine import Deps
    from assistant_api.guard import RateLimiter
    from assistant_api.main import create_app

    def make(llm=None, knowledge=None, limit=100):
        deps = Deps(
            settings,
            knowledge or FakeKnowledge(),
            llm or FakeLLM(),
            RateLimiter(limit, 60),
        )
        return TestClient(create_app(settings, deps)), deps

    return make
