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
    cluster_similarity: float = 0.80
    # 8.4: negative ratings of one question beyond this many per hour are treated as coordinated.
    feedback_burst_limit: int = 5
    # Cap the cases per gate run on slow hardware (critical cases always run); unset = all.
    eval_max_cases: int | None = field(
        default_factory=lambda: int(_env("KB_EVAL_MAX_CASES") or 0) or None
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
    # Model comparison (PRD 20): Ollama's /api/ps reports the memory each loaded model uses.
    # Empty = GPU memory not measured (e.g. when only the LiteLLM gateway is reachable).
    model_stats_url: str = field(default_factory=lambda: _env("KB_MODEL_STATS_URL"))


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
    if problems:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))
