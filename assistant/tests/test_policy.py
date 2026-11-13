"""A college's model policy is enforced (PRD 9.5)."""

from dataclasses import replace

from assistant_api import policy
from assistant_api.config import Settings

S = replace(
    Settings(),
    model_aliases="local/qwen2.5-14b-instruct=qwen14,local/llama3.1-8b=llama8",
    answer_model="default-answer",
    fallback_model="default-fallback",
    max_output_tokens=450,
)
PRD = {
    "primary": "local/qwen2.5-14b-instruct",
    "fallback": "local/llama3.1-8b",
    "external": "none",
}


def test_the_prd_example_policy_is_valid_and_used_in_order():
    assert policy.problems(PRD, S) == []
    chat = policy.models(PRD, S, voice=False)
    assert (chat.primary, chat.fallback, chat.max_tokens) == ("qwen14", "llama8", 450)
    # Voice: the 8B model first (speed), the 14B as its fallback.
    spoken = policy.models(PRD, S, voice=True)
    assert (spoken.primary, spoken.fallback) == (
        "llama8",
        "qwen14",
    ) and spoken.max_tokens <= 150


def test_unknown_or_external_models_are_rejected_with_reasons():
    found = policy.problems(
        {
            "primary": "openai/gpt-4o",
            "external": "anthropic",
            "max_output_tokens": 99999,
            "temp": 1,
        },
        S,
    )
    assert any("openai/gpt-4o" in p and "not offered" in p for p in found)
    assert any("external must be 'none'" in p for p in found)
    assert any("max_output_tokens" in p for p in found)
    assert any("temp is not a known setting" in p for p in found)


def test_a_policy_can_lower_the_output_cap_but_not_raise_it():
    assert policy.models({**PRD, "max_output_tokens": 200}, S, False).max_tokens == 200
    assert policy.models({**PRD, "max_output_tokens": 2000}, S, False).max_tokens == 450


def test_no_policy_means_the_platform_defaults():
    m = policy.models(None, S, False)
    assert (m.primary, m.fallback) == ("default-answer", "default-fallback")
    # Even an unvalidated bad name never reaches the gateway.
    assert (
        policy.models({"primary": "openai/gpt-4o"}, S, False).primary
        == "default-answer"
    )
