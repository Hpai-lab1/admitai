"""A college's model policy (PRD 9.5, pack.yaml `model_policy`), enforced.

    model_policy: { primary: local/qwen2.5-14b-instruct, fallback: local/llama3.1-8b,
                    voice: local/llama3.1-8b, external: none, max_output_tokens: 450 }

- Only names in the platform's MODEL_ALIASES can be used; each maps to the gateway's own model
  name. A pack naming anything else is rejected when it is loaded, so nothing unexpected (and
  nothing external) can ever be called on a college's behalf.
- `external`: `none` (the default), or `gemini` when the college opts in to Google AI Studio
  (PRD Level 2: the college's opt-in, PII redaction and a data-processing agreement). Gemini then
  writes the answers, chat and voice; personal details are masked before anything is sent
  (clients.RoutingLLM), and the college's local model is the fallback if Gemini fails. The
  router and the nightly grounding review stay local.
- Voice answers default to the fallback (8B) model: speed first, as the PRD's table says.
- `max_output_tokens` may lower the platform's cap, never raise it.
- `general_answers: true` (needs `external: gemini`): when the college's own information has
  nothing on a question, Gemini gives a short general answer under a label saying it is not the
  college's information, with no numbers and no promises (engine._general_answer), and a person
  is still offered. Off by default: Level 1 answers only from the college's sources.
"""

from __future__ import annotations

from dataclasses import dataclass

from .clients import GEMINI_PREFIX
from .config import Settings

EXTERNAL = ("none", "gemini")


def uses_gemini(policy: dict | None, settings: Settings) -> bool:
    """The college opted in to Gemini and the platform has a key for it."""
    return bool(
        settings.gemini_api_key
        and str((policy or {}).get("external", "none")).lower() == "gemini"
    )


def gemini_model(policy: dict | None, settings: Settings) -> str | None:
    """Gemini, for a college that opted in to it (with a key on the platform), else None."""
    if uses_gemini(policy, settings):
        return GEMINI_PREFIX + settings.gemini_model
    return None


def translated_languages(policy: dict | None, settings: Settings) -> set[str]:
    """Languages whose visitors get this college's checked English answer translated
    (engine._translated): TRANSLATED_ANSWER_LANGUAGES for every college, and for a college that
    opted in to Gemini also GEMINI_TRANSLATED_LANGUAGES (Hindi and Kannada by default), which
    Gemini translates. A language the answer model writes itself needs no translation."""
    langs = set(settings.translated_answer_languages)
    if uses_gemini(policy, settings):
        langs |= set(settings.gemini_translated_languages)
    return langs - set(settings.native_answer_languages)


def translation_model(
    policy: dict | None, settings: Settings, language: str, answered_by: str | None
) -> str:
    """Gemini for a college that opted in to it (it writes Hindi and Kannada well); otherwise the
    model that wrote the answer, as configured with TRANSLATED_ANSWER_LANGUAGES."""
    if (
        uses_gemini(policy, settings)
        and language in settings.gemini_translated_languages
    ):
        return GEMINI_PREFIX + settings.gemini_model
    return answered_by or settings.answer_model


def general_model(policy: dict | None, settings: Settings) -> str | None:
    """The model for a labelled general answer, or None when the college has not opted in."""
    if (policy or {}).get("general_answers") is True and uses_gemini(policy, settings):
        return GEMINI_PREFIX + settings.gemini_model
    return None


KNOWN_KEYS = {
    "primary",
    "fallback",
    "voice",
    "external",
    "max_output_tokens",
    "general_answers",
    "voice_external",
}

# Outside voice services a college may opt in to (PRD 16 privacy: L1 makes no external calls; L2
# needs the college's opt-in and an agreement). Each receives visitors' voices or transcripts:
# retell (web calls; Soniox hears, ElevenLabs speaks), sarvam (push-to-talk and WhatsApp voice
# notes), vapi (telephone). Default: none, so a new college's voice stays on this platform.
VOICE_EXTERNAL = ("retell", "sarvam", "vapi")


def voice_external(policy: dict | None) -> set[str]:
    """The outside voice services this college opted in to (`model_policy.voice_external`)."""
    value = (policy or {}).get("voice_external") or []
    if isinstance(value, str):
        value = [value]
    return {str(v).lower() for v in value} & set(VOICE_EXTERNAL)


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
    ve = policy.get("voice_external")
    if ve is not None:
        items = [ve] if isinstance(ve, str) else ve
        if not isinstance(items, list) or any(
            str(v).lower() not in VOICE_EXTERNAL for v in items
        ):
            out.append(
                "model_policy.voice_external must list only "
                + ", ".join(VOICE_EXTERNAL)
                + ": each receives visitors' voices, so only what the college agreed to"
            )
    for key in ("primary", "fallback", "voice"):
        name = policy.get(key)
        if name is not None and name not in known:
            out.append(
                f"model_policy.{key}: '{name}' is not offered here (choose from {', '.join(sorted(known))})"
            )
    external = str(policy.get("external", "none")).lower()
    if external not in EXTERNAL:
        out.append(
            f"model_policy.external must be one of {', '.join(EXTERNAL)}: an external model "
            "needs the college's opt-in and a data-processing agreement"
        )
    elif external == "gemini" and not settings.gemini_api_key:
        out.append(
            "model_policy.external is 'gemini' but this platform has no GEMINI_API_KEY"
        )
    general = policy.get("general_answers")
    if general is not None and not isinstance(general, bool):
        out.append("model_policy.general_answers must be true or false")
    elif general and external != "gemini":
        out.append(
            "model_policy.general_answers needs external: gemini (general answers are written "
            "by Gemini)"
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
    if uses_gemini(policy, settings):
        # The college opted in: Gemini answers (chat and voice, it is fast enough for both) and
        # its own local model is the fallback.
        gemini = GEMINI_PREFIX + settings.gemini_model
        local = pick("voice", fallback) if voice else primary
        return Models(gemini, local, min(cap, 150) if voice else cap)
    if voice and voice_on_answer_model:
        return Models(primary, fallback, min(cap, 150))
    if voice:
        spoken = pick("voice", fallback)
        return Models(spoken, primary if spoken != primary else fallback, min(cap, 150))
    return Models(primary, fallback, cap)
