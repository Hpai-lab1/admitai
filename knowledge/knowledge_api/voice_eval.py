"""Channel consistency for voice (PRD F2.10): the test set is answered twice through Module B, once
as chat and once as the voice channel (voice model and voice style), and both accuracies go to
Module B's POST /voice/eval-result. Module B moves voice to the answer model if voice scores more
than 3 points below chat.

Once voice already runs on the answer model, a new voice run would measure the answer model, not
the smaller voice model: the result is recorded here but not sent, so the switch does not flip
back and forth. To re-test the voice model, the College Admin resets the switch in Module B first.

python -m knowledge_api.voice_eval --tenant-id <uuid> [--limit 50]
"""

from __future__ import annotations

import argparse
import sys
import uuid

from . import gate
from .assistant_client import Assistant, AssistantClient, AssistantError
from .config import Settings
from .db import Jsonb, tx


def run_voice_check(
    settings: Settings,
    assistant: Assistant,
    tenant: str,
    limit: int | None = None,
    check_id: str | None = None,
) -> dict:
    check_id = check_id or uuid.uuid4().hex[:12]
    trigger = f"voice:{check_id}"
    try:
        current = (assistant.get_settings(tenant) or {}).get("voice_quality") or {}
    except AssistantError:
        current = {}
    chat = gate.run_suite(
        settings,
        assistant,
        tenant,
        tenant,
        "voice_check_chat",
        trigger,
        limit=limit,
        review=False,
    )
    voice = gate.run_suite(
        settings,
        assistant,
        tenant,
        tenant,
        "voice_check_voice",
        trigger,
        limit=limit,
        review=False,
        channel="web_voice",
    )
    chat_acc, voice_acc = (
        chat["scores"].get("accuracy"),
        voice["scores"].get("accuracy"),
    )
    out = {
        "check_id": check_id,
        "chat_run": chat["id"],
        "voice_run": voice["id"],
        "chat_accuracy": chat_acc,
        "voice_accuracy": voice_acc,
        "gap_points": None
        if None in (chat_acc, voice_acc)
        else round((chat_acc - voice_acc) * 100, 2),
        "cases": chat["scores"].get("cases", 0),
        "voice_models": sorted(
            {r["model"] for r in voice["results"] if r.get("model")}
        ),
        "sent_to_assistant": False,
    }
    if chat_acc is None or voice_acc is None:
        out["note"] = "No answerable cases in the test set: nothing to compare"
    elif current.get("use_answer_model"):
        out["note"] = (
            "Voice already runs on the answer model, so this run measured the answer model; "
            "not sent, to keep the switch from flipping back"
        )
    else:
        try:
            out["decision"] = assistant.voice_eval_result(
                tenant,
                {
                    "chat_accuracy": chat_acc,
                    "voice_accuracy": voice_acc,
                    "cases": out["cases"],
                    "run_id": check_id,
                },
            )
            out["sent_to_assistant"] = True
        except AssistantError as exc:
            out["note"] = f"Could not send the result to Module B: {exc}"
    # Both runs carry the comparison, so the quality report and the console can show it.
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "update learn.eval_runs set gate = %s where id::text = any(%s)",
            (Jsonb({"voice_check": out}), [chat["id"], voice["id"]]),
        )
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Voice vs chat on the test set (PRD F2.10)"
    )
    ap.add_argument("--tenant-id", required=True)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    s = Settings()
    if not s.database_url:
        sys.exit("Set KB_DATABASE_URL (source local.env.sh)")
    print(run_voice_check(s, AssistantClient(s), a.tenant_id, a.limit))
