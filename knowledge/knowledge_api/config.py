"""Settings from environment variables. Names are prefixed KB_ so Module A can share a shell with
Modules B and C (see local.env.sh at the repository root)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _dev() -> bool:
    return _env("APP_ENV", "production") == "development"


@dataclass(frozen=True)
class Settings:
    # The API connects as admitai_kb (RLS-restricted); migrations use the admin URL.
    database_url: str = field(default_factory=lambda: _env("KB_DATABASE_URL"))
    admin_database_url: str = field(
        default_factory=lambda: _env("KB_ADMIN_DATABASE_URL")
    )
    # Background jobs connect as the module's worker role (not superuser; BYPASSRLS, rights only in
    # this module's schemas). Unset: the admin connection, as before (migrations still use that).
    worker_database_url: str = field(
        default_factory=lambda: _env("KB_WORKER_DATABASE_URL", "")
        or _env("KB_ADMIN_DATABASE_URL", "")
    )
    # Embeddings through the shared model service (Ollama locally; LiteLLM on the lab servers).
    ollama_url: str = field(
        default_factory=lambda: _env("OLLAMA_URL", "http://127.0.0.1:11434")
    )
    embed_model: str = field(
        default_factory=lambda: _env("KB_EMBED_MODEL", "nomic-embed-text")
    )
    embed_dim: int = 768
    # Multilingual partner for Indic-script questions; empty string turns it off.
    ml_embed_model: str = field(
        default_factory=lambda: _env("KB_ML_EMBED_MODEL", "bge-m3")
    )
    ml_embed_dim: int = 1024
    # Cross-encoder reranker (PRD 9.4): a folder with the ONNX model; empty turns it off.
    reranker: str = field(default_factory=lambda: _env("KB_RERANKER"))
    # "ollama" in normal use; "hash" is a deterministic offline embedder for tests.
    embedder: str = field(default_factory=lambda: _env("KB_EMBEDDER", "ollama"))
    # Chunking (PRD F4.7): ~400 tokens with 15% overlap.
    chunk_tokens: int = 400
    chunk_overlap: float = 0.15
    max_upload_bytes: int = 50 * 1024 * 1024  # F4.1
    # Module B: read answers and signals, and ask questions headlessly for eval runs (PRD 21.4).
    assistant_api_url: str = field(
        default_factory=lambda: _env("KB_ASSISTANT_API_URL", "http://127.0.0.1:8150")
    )
    # Module C relays knowledge events to the college's webhooks (PRD 12.2).
    experience_api_url: str = field(
        default_factory=lambda: _env("KB_EXPERIENCE_API_URL")
    )
    key_to_experience: str = field(
        default_factory=lambda: _env("KEY_A_TO_C")
        or (_env("SERVICE_KEY") if _dev() else "")
    )
    # "development" allows the shared-key set-up; anything else is production (safe default).
    app_env: str = field(default_factory=lambda: _env("APP_ENV", "production"))
    # Inbound keys, one per caller. Module B may only search and read the catalogue.
    key_from_experience: str = field(default_factory=lambda: _env("KEY_C_TO_A"))
    key_from_assistant: str = field(default_factory=lambda: _env("KEY_B_TO_A"))
    ops_key: str = field(
        default_factory=lambda: _env("KEY_OPS")
        or (_env("SERVICE_KEY") if _dev() else "")
    )
    # Outbound: the key this module presents to Module B (eval runs, signals).
    service_key: str = field(
        default_factory=lambda: _env("KEY_A_TO_B")
        or (_env("SERVICE_KEY") if _dev() else "")
    )
    # Learning loop (PRD 8.3-8.4)
    approval_sla_working_days: int = 3
    gate_tolerance_points: float = 1.0
    # "quick": an approval is tested on the questions it can affect (about 30 s on a laptop); the
    # whole test set runs nightly. "full": every approval answers the whole test set (minutes).
    gate_mode: str = field(default_factory=lambda: _env("KB_GATE_MODE", "quick"))
    quick_max_cases: int = field(
        default_factory=lambda: int(_env("KB_QUICK_MAX_CASES") or 12)
    )
    quick_similar: int = field(
        default_factory=lambda: int(_env("KB_QUICK_SIMILAR") or 6)
    )
    # Questions asked at once during a test. On a laptop more than 2 only slows each answer down;
    # a lab GPU can take more.
    suite_parallel: int = field(
        default_factory=lambda: int(_env("KB_SUITE_PARALLEL") or 2)
    )
    cluster_similarity: float = 0.80
    # 8.4: negative ratings of one question beyond this many per hour are treated as coordinated.
    feedback_burst_limit: int = 5
    # Cap the cases per gate run on slow hardware (critical cases always run); unset = all.
    eval_max_cases: int | None = field(
        default_factory=lambda: int(_env("KB_EVAL_MAX_CASES") or 0) or None
    )
    # How often work cut off by a restart (an upload, a test run) is marked as interrupted; 0 = off.
    sweep_interval_s: float = field(
        default_factory=lambda: float(_env("KB_SWEEP_INTERVAL_S") or 300)
    )
    web_origin: str = field(
        default_factory=lambda: _env("WEB_ORIGIN", "http://127.0.0.1:8210")
    )
    # Evaluation Agent (PRD 9.5, 20): rubric grading by the DeepSeek distills through the shared
    # model gateway (OpenAI-compatible), cross-checked by a second judge, 10% human spot checks.
    # Grading runs only in the scheduled window (deploy/README.md), local time.
    judge_url: str = field(
        default_factory=lambda: _env("KB_JUDGE_URL")
        or _env("LLM_BASE_URL")
        or (_env("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/") + "/v1")
    )
    judge_api_key: str = field(default_factory=lambda: _env("KB_JUDGE_API_KEY"))
    judge_model: str = field(
        default_factory=lambda: _env(
            "KB_JUDGE_MODEL", "local/deepseek-r1-distill-qwen-14b"
        )
    )
    judge_check_model: str = field(
        default_factory=lambda: _env(
            "KB_JUDGE_CHECK_MODEL", "local/deepseek-r1-distill-llama-8b"
        )
    )
    judge_window: str = field(
        default_factory=lambda: _env("KB_JUDGE_WINDOW", "22:00-06:00")
    )
    judge_timezone: str = field(
        default_factory=lambda: _env("KB_JUDGE_TIMEZONE", "Asia/Kolkata")
    )
    human_spot_share: float = 0.10
    # Sharing the model with visitors (gate.py): the longest a test suite waits before one
    # question while Module B is answering visitors, and the longest a suite waits for another.
    gate_yield_max_s: float = field(
        default_factory=lambda: float(_env("KB_GATE_YIELD_MAX_S", "30"))
    )
    suite_queue_max_s: float = field(
        default_factory=lambda: float(_env("KB_SUITE_QUEUE_MAX_S", "3600"))
    )
    # Drafting a curated answer from the college's documents for an approver (drafting.py): the
    # answer model, through the same gateway; and the search score below which a document is not
    # taken to cover the question (Module B refuses below 0.50 too).
    draft_model: str = field(
        default_factory=lambda: _env("KB_DRAFT_MODEL")
        or _env("ANSWER_MODEL", "qwen2.5:3b")
    )
    draft_min_score: float = field(
        default_factory=lambda: float(_env("KB_DRAFT_MIN_SCORE", "0.50"))
    )
    # Model comparison (PRD 20): Ollama's /api/ps reports the memory each loaded model uses.
    # Empty = GPU memory not measured (e.g. when only the LiteLLM gateway is reachable).
    model_stats_url: str = field(default_factory=lambda: _env("KB_MODEL_STATS_URL"))
    # Self-learning knowledge layer (knowledge_api/selflearn, docs/self-learning). Off by default:
    # with it off nothing below is read and Module A behaves exactly as before.
    self_learning_enabled: bool = field(
        default_factory=lambda: _env("KNOWLEDGE_SELF_LEARNING_ENABLED", "false").lower()
        in ("1", "true", "yes", "on")
    )
    learning_min_agreeing_sessions: int = field(
        default_factory=lambda: int(_env("LEARNING_MIN_AGREEING_SESSIONS") or 3)
    )
    learning_min_trust: float = field(
        default_factory=lambda: float(_env("LEARNING_MIN_TRUST") or 0.6)
    )
    learning_poll_s: float = field(
        default_factory=lambda: float(_env("LEARNING_POLL_S") or 5)
    )
    # Salt for session fingerprints (required when the flag is on; never commit a real one).
    learning_session_salt: str = field(
        default_factory=lambda: _env("LEARNING_SESSION_SALT")
    )
    # Lesson types that go live after the gate without a person (numbers never do).
    learning_auto_live: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            t.strip()
            for t in _env(
                "LEARNING_AUTO_LIVE", "alias,retrieval_boost,curated_from_source"
            ).split(",")
            if t.strip()
        )
    )
    # "llm" (the answer model through the gateway, rules as fallback) or "rules" (no model).
    learning_classifier: str = field(
        default_factory=lambda: _env("LEARNING_CLASSIFIER", "llm")
    )
    learning_classifier_model: str = field(
        default_factory=lambda: _env("LEARNING_CLASSIFIER_MODEL")
        or _env("KB_DRAFT_MODEL")
        or _env("ANSWER_MODEL", "qwen2.5:3b")
    )
    learning_max_feedback_per_hour: int = field(
        default_factory=lambda: int(_env("LEARNING_MAX_FEEDBACK_PER_HOUR") or 10)
    )
    # A page cited as evidence is fetched again when its copy is older than this.
    learning_refetch_after_h: float = field(
        default_factory=lambda: float(_env("LEARNING_REFETCH_AFTER_H") or 24)
    )
    # Cosine between a question and a lesson's question cluster for the lesson to apply.
    learning_boost_similarity: float = field(
        default_factory=lambda: float(_env("LEARNING_BOOST_SIMILARITY") or 0.85)
    )
    # Self-heal: roll a live lesson back when, after it went live, at least this many trusted
    # votes from distinct sessions on its questions are mostly negative.
    learning_rollback_min_votes: int = field(
        default_factory=lambda: int(_env("LEARNING_ROLLBACK_MIN_VOTES") or 5)
    )
    learning_rollback_down_ratio: float = field(
        default_factory=lambda: float(_env("LEARNING_ROLLBACK_DOWN_RATIO") or 0.6)
    )
    # Questions from the whole test set added to every lesson's gate (stratified by kind).
    learning_gate_sample: int = field(
        default_factory=lambda: int(_env("LEARNING_GATE_SAMPLE") or 20)
    )
    # When the flag is first turned on, older feedback than this is not processed.
    learning_backfill_h: float = field(
        default_factory=lambda: float(_env("LEARNING_BACKFILL_H") or 24)
    )


def check_production(s: Settings) -> None:
    """Refuse an unsafe production configuration loudly instead of running open."""
    if s.app_env == "development":
        return
    problems = []
    if not (s.key_from_experience and s.key_from_assistant):
        problems.append("KEY_C_TO_A and KEY_B_TO_A are required")
    if os.environ.get("SERVICE_KEY"):
        problems.append(
            "SERVICE_KEY is a development setting and must not be set in production"
        )
    if s.self_learning_enabled and not s.learning_session_salt:
        problems.append(
            "LEARNING_SESSION_SALT is required when KNOWLEDGE_SELF_LEARNING_ENABLED is on"
        )
    if problems:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))
