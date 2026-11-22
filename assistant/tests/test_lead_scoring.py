"""F5.4 lead score: per-tenant weights and bands, the defaults unchanged."""

from assistant_api.leads import (
    DEFAULT_SCORING,
    score_lead,
    scoring_problems,
    scoring_rules,
)


def test_defaults_score_as_before():
    # 4 turns -> engagement 24; fees/dates/process -> intent 30; a programme -> fit 25
    hot = score_lead(4, ["fees", "dates", "process"], ["BTECH-CSE"], False)
    assert hot == {
        "score": 79,
        "band": "hot",
        "parts": {"engagement": 24, "intent": 30, "fit": 25},
    }
    assert score_lead(1, [], [], False) == {
        "score": 6,
        "band": "cold",
        "parts": {"engagement": 6, "intent": 0, "fit": 0},
    }
    assert scoring_rules(None) == DEFAULT_SCORING


def test_a_college_sets_its_own_weights_and_bands():
    rules = scoring_rules({"program_fit": 40, "intent_max": 30, "bands": {"hot": 60}})
    assert rules["bands"] == {"hot": 60, "warm": 40} and rules["engagement_max"] == 30
    r = score_lead(2, ["fees"], ["MBA"], True, rules)
    assert r["parts"] == {"engagement": 12, "intent": 25, "fit": 40}
    assert r["score"] == 77 and r["band"] == "hot"


def test_settings_that_would_break_the_0_to_100_scale_are_refused():
    assert scoring_problems(None) == [] and scoring_problems({}) == []
    assert scoring_problems({"program_fit": 60}) == [
        "lead_scoring: engagement_max + intent_max + program_fit must not exceed 100"
    ]
    assert "lead_scoring.bands.hot must be above bands.warm" in scoring_problems(
        {"bands": {"hot": 30, "warm": 40}}
    )
    assert scoring_problems({"bonus": 5}) == [
        "lead_scoring.bonus is not a known setting"
    ]
    assert scoring_problems({"program_fit": -1})[0].startswith(
        "lead_scoring.program_fit"
    )
    assert scoring_problems({"program_fit": True})  # not a number
    assert scoring_problems({"bands": {"cold": 10}}) == [
        "lead_scoring.bands takes only hot and warm"
    ]
    assert scoring_problems({"prompt_after_answers": 0}) == [
        "lead_scoring.prompt_after_answers must be at least 1"
    ]
    assert scoring_problems("high") == ["lead_scoring must be a mapping"]


def test_a_pack_with_bad_scoring_is_rejected():
    from assistant_api.config import Settings
    from assistant_api.pack import agent_config, validate

    settings = Settings()  # no database needed: validation only

    pack = {"college": {"name": "X"}, "lead_scoring": {"program_fit": 90}}
    assert any("must not exceed 100" in p for p in validate(settings, pack))
    good = {"college": {"name": "X"}, "lead_scoring": {"bands": {"hot": 75}}}
    assert validate(settings, good) == []
    assert agent_config(good)["lead_scoring"] == {"bands": {"hot": 75}}
