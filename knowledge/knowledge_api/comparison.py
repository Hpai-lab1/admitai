"""Model-comparison harness (PRD 20 "Model comparison", 21.2: "used with B").

The same test set is answered by several answer models through Module B: for each model, the
college's knowledge is copied to a sandbox, the sandbox's model policy names that model, and the
suite runs as in the evaluation gate (deterministic grading and Module B's grounding review).
The report compares accuracy, groundedness, refusals, routing, latency and, when the model
service reports it, the memory of the loaded models. Nothing changes for the real college.

Models are Module B's allow-listed names (e.g. local/qwen2.5-14b-instruct, local/llama3.1-8b,
local/deepseek-r1-distill-qwen-14b). The DeepSeek distills run only in the scheduled window.

python -m knowledge_api.comparison --tenant-id <uuid> --models local/qwen2.5-3b,local/llama3.1-8b
"""

from __future__ import annotations

import argparse
import sys

import httpx

from . import gate
from .assistant_client import Assistant, AssistantClient
from .config import Settings
from .db import Jsonb, tx

MAX_MODELS = 5


def loaded_models(
    settings: Settings, client: httpx.Client | None = None
) -> list[dict] | None:
    """Memory of the models the model service has loaded now (Ollama /api/ps), or None."""
    if not settings.model_stats_url:
        return None
    try:
        r = (client or httpx.Client(timeout=5)).get(
            settings.model_stats_url.rstrip("/") + "/api/ps"
        )
        r.raise_for_status()
        return [
            {
                "name": m.get("name"),
                "vram_gb": round((m.get("size_vram") or 0) / 1e9, 2),
                "size_gb": round((m.get("size") or 0) / 1e9, 2),
            }
            for m in r.json().get("models", [])
        ]
    except (httpx.HTTPError, ValueError):
        return None


def start(
    settings: Settings, tenant: str, models: list[str], requested_by: str | None
) -> str:
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "insert into learn.comparisons (tenant_id, models, requested_by) values (%s, %s, %s) returning id",
            (tenant, models, requested_by),
        )
        return str(cur.fetchone()["id"])


def run_comparison(
    settings: Settings,
    assistant: Assistant,
    tenant: str,
    comparison_id: str,
    models: list[str],
    limit: int | None = None,
) -> dict:
    trigger = f"comparison:{comparison_id}"
    rows = []
    try:
        base_cfg = assistant.get_settings(tenant)
        for model in models:
            sandbox, _ = gate.clone_tenant(settings, tenant)
            try:
                policy = {**(base_cfg.get("model_policy") or {}), "primary": model}
                assistant.put_settings(
                    sandbox, {**base_cfg, "sandbox": True, "model_policy": policy}
                )
                run = gate.run_suite(
                    settings,
                    assistant,
                    tenant,
                    sandbox,
                    "comparison",
                    trigger,
                    limit=limit,
                )
                memory = loaded_models(settings)
            finally:
                gate.drop_sandbox(settings, sandbox)
                try:
                    assistant.delete_sandbox(sandbox)
                except Exception:  # noqa: BLE001 - leftovers are harmless
                    pass
            used = [r.get("model") for r in run["results"] if r.get("model")]
            sc = run["scores"]
            rows.append(
                {
                    "model": model,
                    "run_id": run["id"],
                    **{
                        k: sc.get(k)
                        for k in (
                            "accuracy",
                            "refusal",
                            "groundedness",
                            "routing",
                            "cases",
                            "latency_p50_s",
                            "latency_p95_s",
                            "first_sentence_p50_s",
                            "first_sentence_p95_s",
                            "review_error",
                        )
                    },
                    # If the model failed, Module B fell back to another one: say so, or the
                    # comparison would credit the wrong model.
                    "answered_by_requested_model": round(
                        sum(
                            u == model or str(u).endswith(model.split("/")[-1])
                            for u in used
                        )
                        / len(used),
                        3,
                    )
                    if used
                    else None,
                    "models_seen": sorted(set(used)),
                    "loaded_models": memory,
                }
            )
        report = {
            "models": rows,
            "best_accuracy": max(
                (r for r in rows if r["accuracy"] is not None),
                key=lambda r: r["accuracy"],
                default={},
            ).get("model"),
            "gpu_memory_measured": any(r["loaded_models"] is not None for r in rows),
        }
        with tx(settings.database_url, tenant) as cur:
            cur.execute(
                "update learn.comparisons set status = 'done', report = %s, finished_at = now() where id::text = %s",
                (Jsonb(report), comparison_id),
            )
        return report
    except Exception as exc:
        with tx(settings.database_url, tenant) as cur:
            cur.execute(
                "update learn.comparisons set status = 'error', error = %s, report = %s,"
                " finished_at = now() where id::text = %s",
                (str(exc)[:500], Jsonb({"models": rows}), comparison_id),
            )
        raise


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Compare answer models on the test set (PRD 20)"
    )
    ap.add_argument("--tenant-id", required=True)
    ap.add_argument(
        "--models", required=True, help="comma-separated Module B model names"
    )
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    s = Settings()
    if not s.database_url:
        sys.exit("Set KB_DATABASE_URL (source local.env.sh)")
    names = [m.strip() for m in a.models.split(",") if m.strip()]
    cid = start(s, a.tenant_id, names, "cli")
    print(run_comparison(s, AssistantClient(s), a.tenant_id, cid, names, a.limit))
