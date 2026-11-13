"""Which Whisper model hears which language (no real models: fakes record what was asked)."""

from types import SimpleNamespace

from assistant_api.voice import Speech


class FakeModel:
    def __init__(self, size, says, detects):
        self.size, self.says, self.detects, self.calls = size, says, detects, []

    def transcribe(self, audio, **kw):
        self.calls.append(kw["language"])
        seg = SimpleNamespace(text=self.says, avg_logprob=-0.2)
        return iter([seg]), SimpleNamespace(language=kw["language"] or self.detects)


class TwoModels(Speech):
    def __init__(self, detects="en"):
        super().__init__("/nowhere", "base", "none", whisper_size_indic="small")
        self.models = {
            "base": FakeModel("base", "Kittani Hai", detects),
            "small": FakeModel("small", "फीस कितनी है", detects),
        }

    def _whisper(self, size=None):
        return self.models[size or self.whisper_size]


def test_english_uses_the_fast_model_only():
    s = TwoModels("en")
    t = s.transcribe(b"wav")
    assert (
        t.language == "en"
        and s.models["base"].calls == [None]
        and s.models["small"].calls == []
    )


def test_a_chosen_indic_language_goes_straight_to_the_larger_model():
    s = TwoModels()
    t = s.transcribe(b"wav", language="hi")
    assert (
        t.text == "फीस कितनी है"
        and s.models["base"].calls == []
        and s.models["small"].calls == ["hi"]
    )


def test_detected_hindi_is_heard_again_by_the_larger_model_in_hindi():
    s = TwoModels("hi")
    t = s.transcribe(b"wav")
    assert t.text == "फीस कितनी है" and t.language == "hi"
    assert s.models["base"].calls == [None] and s.models["small"].calls == ["hi"]


def test_one_model_for_everything_when_not_configured():
    s = Speech("/nowhere", "base", "none")
    assert s.whisper_size_indic == "base"
