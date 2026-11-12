"""Module A tests. Pure-logic tests always run; database tests need KB_TEST_DATABASE_URL and
KB_TEST_ADMIN_DATABASE_URL (a throw-away database) and are skipped otherwise."""

import os
import sys
import uuid
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("APP_ENV", "development")  # tests use the development set-up
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
    "KB_RERANKER",
):
    os.environ.pop(_k, None)
OPS = {"X-Service-Key": "test-ops-key"}

from knowledge_api.config import Settings  # noqa: E402
from knowledge_api.embedder import HashEmbedder  # noqa: E402

TEST_URL = os.environ.get("KB_TEST_DATABASE_URL", "")
TEST_ADMIN_URL = os.environ.get("KB_TEST_ADMIN_DATABASE_URL", "")
needs_db = pytest.mark.skipif(
    not (TEST_URL and TEST_ADMIN_URL), reason="no KB test database configured"
)


@pytest.fixture(scope="session")
def migrated():
    from knowledge_api import migrate

    with psycopg.connect(TEST_ADMIN_URL, autocommit=True) as conn:
        conn.execute("drop schema if exists kb cascade")
        conn.execute("drop schema if exists learn cascade")
    migrate.migrate(TEST_ADMIN_URL)
    return True


@pytest.fixture
def settings(migrated):
    from knowledge_api import search

    with psycopg.connect(TEST_ADMIN_URL, autocommit=True) as conn:
        conn.execute(
            "truncate kb.chunks, kb.documents, kb.sources, kb.curated_qa_history, kb.curated_qa,"
            " kb.program_dates, kb.program_fees, kb.programs, kb.sandboxes, learn.events, learn.signals,"
            " learn.proposals, learn.eval_cases, learn.eval_runs cascade"
        )
    search.invalidate()
    return replace(
        Settings(),
        database_url=TEST_URL,
        admin_database_url=TEST_ADMIN_URL,
        embedder="hash",
        ops_key="test-ops-key",
        key_from_experience="test-key-from-c",
        key_from_assistant="test-key-from-b",
    )


@pytest.fixture
def embedder():
    return HashEmbedder()


@pytest.fixture
def client(settings, embedder):
    from fastapi.testclient import TestClient

    from knowledge_api.main import create_app

    return TestClient(create_app(settings, embedder), headers=OPS)


@pytest.fixture
def tenant():
    return str(uuid.uuid4())


@pytest.fixture
def other_tenant():
    return str(uuid.uuid4())


class FakeAssistant:
    """Stands in for Module B. It answers by searching the target tenant in Module A in-process
    (cites the top hit if it matches well, otherwise refuses) and hands off sensitive topics, so the
    evaluation gate measures the real effect of a change on the sandbox."""

    def __init__(self, settings, embedder):
        self.settings, self.embedder = settings, embedder
        self.config: dict[str, dict] = {}
        self.messages: dict[str, dict] = {}
        self.turns: list[dict] = []
        self.reviews: list[dict] = []
        self.asked: list[tuple[str, str]] = []
        self.answers: dict[str, str] = {}  # message id -> text, for grounding reviews
        self.unsupported_texts: set[str] = set()  # answers the fake judge rejects
        self.reviewed: list[str] = []
        self.channels: list[str] = []
        self.voice_misses: set[str] = set()  # words the fake voice model fails on
        self.voice_results: list[dict] = []
        self.models: dict[str, str] = {}  # tenant -> answer model the fake reports

    def message(self, tenant, message_id):
        return self.messages.get(message_id)

    def unsupported(self, tenant, since):
        return [
            r
            for r in self.reviews
            if since is None or r["created_at"].isoformat() > since
        ]

    def signals(self, tenant, since):
        return [
            t
            for t in self.turns
            if since is None or t["created_at"].isoformat() > since
        ]

    def ask(self, tenant, conversation_id, text, channel="web_chat"):
        from knowledge_api import search

        self.asked.append((tenant, text))
        self.channels.append(channel)
        if channel == "web_voice" and any(w in text.lower() for w in self.voice_misses):
            # The smaller voice model gets these wrong (F2.10 tests).
            return {
                "text": "I am not certain.",
                "done": {"outcome": "no_answer", "sources": [], "route": {}},
            }
        topics = self.config.get(tenant, {}).get("sensitive_topics", [])
        if any(t in text.lower() for t in topics):
            return {
                "text": "A person will call you.",
                "done": {"outcome": "handoff", "sources": [], "route": {}},
            }
        res = search.search(
            self.settings.database_url, self.embedder, tenant, text, top_k=1
        )
        if not res["items"] or res["items"][0]["vector_score"] < 0.3:
            return {
                "text": "I don't know.",
                "done": {"outcome": "no_answer", "sources": [], "route": {}},
            }
        hit = res["items"][0]
        c = hit["citation"]
        source = {
            "source_type": c["source_type"],
            "title": c["title"],
            "ref_id": c["qa_id"] or c["document_id"],
        }
        mid = str(uuid.uuid4())
        self.answers[mid] = hit["content"]
        return {
            "text": hit["content"],
            "done": {
                "outcome": "answered",
                "message_id": mid,
                "sources": [source],
                "route": {"routes": [{"role": "admission"}]},
                "versions": {
                    "model": (
                        self.config.get(tenant, {}).get("model_policy") or {}
                    ).get("primary", "local/qwen2.5-3b")
                },
            },
        }

    def review(self, tenant, message_ids):
        self.reviewed += message_ids
        out = {}
        for mid in message_ids:
            text = self.answers.get(mid) or ""
            ok = not any(u in text for u in self.unsupported_texts)
            out[mid] = {
                "verdict": "supported" if ok else "unsupported",
                "claims": 1,
                "supported": int(ok),
                "unsupported": [] if ok else [self.answers.get(mid)],
            }
        return out

    def get_settings(self, tenant):
        return dict(self.config.get(tenant, {"sensitive_topics": []}))

    def put_settings(self, tenant, config):
        self.config[tenant] = dict(config)

    def voice_eval_result(self, tenant, result):
        self.voice_results.append({"tenant": tenant, **result})
        gap = round((result["chat_accuracy"] - result["voice_accuracy"]) * 100, 2)
        decision = {"gap_points": gap, "use_answer_model": gap > 3}
        self.config.setdefault(tenant, {"sensitive_topics": []})["voice_quality"] = (
            decision
        )
        return decision

    def delete_sandbox(self, tenant):
        assert self.config.get(tenant, {}).get(
            "sandbox"
        ), "only sandboxes may be deleted"
        self.config.pop(tenant)


@pytest.fixture
def assistant(settings, embedder):
    return FakeAssistant(settings, embedder)


@pytest.fixture
def lclient(settings, embedder, assistant):
    from fastapi.testclient import TestClient

    from knowledge_api.main import create_app

    return TestClient(create_app(settings, embedder, assistant), headers=OPS)
