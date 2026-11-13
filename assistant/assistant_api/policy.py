"""A college's model policy (PRD 9.5, pack.yaml `model_policy`), enforced.

    model_policy: { primary: local/qwen2.5-14b-instruct, fallback: local/llama3.1-8b,
                    voice: local/llama3.1-8b, external: none, max_output_tokens: 450 }

- Only names in the platform's MODEL_ALIASES can be used; each maps to the gateway's own model
  name. A pack naming anything else is rejected when it is loaded, so nothing unexpected (and
  nothing external) can ever be called on a college's behalf.
- `external` must be `none`: external models are Level 2 and need the college's opt-in, PII
  redaction and a data-processing agreement first.
- Voice answers default to the fallback (8B) model: speed first, as the PRD's table says.
- `max_output_tokens` may lower the platform's cap, never raise it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings

KNOWN_KEYS = {"primary", "fallback", "voice", "external", "max_output_tokens"}


def aliases(settings: Settings) -> dict[str, str]:
    out = {}
    for item in settings.model_aliases.split(","):
        if "=" in item:
            name, model = item.split("=", 1)
            out[name.strip()] = model.strip()
    return out


def problems(policy: dict | None, settings: Settings) -> list[str]:
    if not policy:
        return []
    if not isinstance(policy, dict):
        return ["model_policy must be a mapping"]
    known = aliases(settings)
    out = []
    for key in sorted(set(policy) - KNOWN_KEYS):
        out.append(f"model_policy.{key} is not a known setting")
    for key in ("primary", "fallback", "voice"):
        name = policy.get(key)
        if name is not None and name not in known:
            out.append(
                f"model_policy.{key}: '{name}' is not offered here (choose from {', '.join(sorted(known))})"
            )
    if str(policy.get("external", "none")).lower() != "none":
        out.append(
            "model_policy.external must be 'none': external models are a Level 2 option that "
            "needs the college's opt-in and a data-processing agreement"
        )
    cap = policy.get("max_output_tokens")
    if cap is not None and (not isinstance(cap, int) or not 50 <= cap <= 2000):
        out.append(
            "model_policy.max_output_tokens must be a whole number from 50 to 2000"
        )
    return out


@dataclass(frozen=True)
class Models:
    primary: str
    fallback: str
    max_tokens: int


def models(
    policy: dict | None,
    settings: Settings,
    voice: bool,
    voice_on_answer_model: bool = False,
) -> Models:
    """The models to try, in order, for one answer. Anything not allowed falls back to the
    platform defaults (a pack is validated on load, so this is a second line of defence).
    `voice_on_answer_model`: the college's voice answers scored more than 3 points below chat on
    its test set, so voice uses the answer model (with a shorter context) instead (PRD F2.10)."""
    policy, known = policy or {}, aliases(settings)

    def pick(key: str, default: str) -> str:
        return known.get(policy.get(key) or "", default)

    primary = pick("primary", settings.answer_model)
    fallback = pick("fallback", settings.fallback_model)
    cap = settings.max_output_tokens
    if isinstance(policy.get("max_output_tokens"), int):
        cap = max(50, min(cap, policy["max_output_tokens"]))
    if voice and voice_on_answer_model:
        return Models(primary, fallback, min(cap, 150))
    if voice:
        spoken = pick("voice", fallback)
        return Models(spoken, primary if spoken != primary else fallback, min(cap, 150))
    return Models(primary, fallback, cap)
