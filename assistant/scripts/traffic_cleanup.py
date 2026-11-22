"""Test traffic must not look like visitors (used by voice_e2e.py and latency_report.py; not a test module).

The live scripts ask questions as a real visitor would (a Module C widget session, then Module B),
so their conversations start as origin "visitor". Afterwards each one is:
1. marked origin "operator" in Module B's own schema, so analytics and Module A's learning
   signals never count it (005_conversation_origin.sql);
2. erased in Module B (content blanked, recordings and traces deleted), and
3. erased in Module C through its own API (POST /privacy/erase), which also removes the session.
"""

from __future__ import annotations

from assistant_api import privacy
from assistant_api.config import Settings
from assistant_api.db import tx
from assistant_api.experience_client import ExperienceClient, ExperienceError


def retire(tenant: str, sessions: dict[str, str]) -> dict:
    """sessions: {conversation_id: Module C session id}."""
    s = Settings()
    with tx(s.database_url, tenant) as cur:
        ids = list(sessions)
        if ids:
            cur.execute(
                "update chat.conversations set origin = 'operator' where id = any(%s)",
                (ids,),
            )
        erased = privacy.erase_conversations(cur, ids)
    c_erased, c_failed = 0, 0
    exp = ExperienceClient(s)
    for session in set(sessions.values()):
        try:
            exp.erase(tenant, session)
            c_erased += 1
        except ExperienceError:
            c_failed += 1
    return {
        "conversations": len(erased["conversations"]),
        "module_c_sessions_erased": c_erased,
        "module_c_erase_failed": c_failed,
    }
