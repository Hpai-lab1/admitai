"""Outside voice services only for a college that opted in (model_policy.voice_external)."""

from assistant_api import policy, speech
from assistant_api.config import Settings


def test_voice_external_reads_only_known_services():
    assert policy.voice_external(None) == set()
    assert policy.voice_external({"voice_external": ["Retell", "sarvam", "x"]}) == {
        "retell",
        "sarvam",
    }
    assert policy.voice_external({"voice_external": "vapi"}) == {"vapi"}


def test_a_pack_naming_an_unknown_voice_service_is_refused():
    s = Settings()
    assert policy.problems({"voice_external": ["retell", "bolna"]}, s)
    assert not policy.problems({"voice_external": ["retell", "sarvam", "vapi"]}, s)


class _Speech:
    def __init__(self):
        self.sarvam = object()


def test_sarvam_is_removed_for_a_college_that_did_not_opt_in():
    shared = _Speech()
    assert speech.for_college(shared, {"sarvam"}) is shared
    local = speech.for_college(shared, set())
    assert (
        local.sarvam is None and shared.sarvam is not None
    )  # the shared one is untouched
