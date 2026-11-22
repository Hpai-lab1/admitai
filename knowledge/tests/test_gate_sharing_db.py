"""The evaluation gate shares the model with visitors: one suite at a time, visitors first."""

from dataclasses import replace

import pytest

from conftest import needs_db
from knowledge_api import gate
from knowledge_api.config import Settings


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class Busy:
    """Module B's GET /load, answering from a script."""

    def __init__(self, *counts, fail=False):
        self.counts, self.fail, self.asked = list(counts), fail, []

    def load(self, tenant):
        self.asked.append(tenant)
        if self.fail:
            raise RuntimeError("down")
        return {"live_turns": self.counts.pop(0) if self.counts else 0}


@pytest.fixture
def gate_settings():
    return Settings(
        database_url="postgresql://unused",
        admin_database_url="postgresql://unused",
        worker_database_url="postgresql://unused",
    )


def test_a_suite_waits_while_visitors_are_answered(gate_settings):
    clock = Clock()
    b = Busy(2, 1, 0)
    waited = gate.yield_to_visitors(
        gate_settings, b, "t1", sleep=clock.sleep, clock=clock
    )
    assert waited == 2.0 and b.asked == ["t1"] * 3


def test_a_suite_never_stalls_for_long_or_when_module_b_cannot_say(gate_settings):
    clock = Clock()
    s = replace(gate_settings, gate_yield_max_s=5)
    always = Busy(*([3] * 100))
    assert (
        gate.yield_to_visitors(s, always, "t1", sleep=clock.sleep, clock=clock) == 5.0
    )
    assert (
        gate.yield_to_visitors(s, Busy(fail=True), "t1", sleep=clock.sleep, clock=clock)
        == 0
    )
    assert gate.yield_to_visitors(s, object(), "t1") == 0  # an assistant without /load
    off = replace(gate_settings, gate_yield_max_s=0)
    assert gate.yield_to_visitors(off, Busy(5), "t1") == 0


@needs_db
def test_suites_use_the_model_one_at_a_time(settings):
    first = gate._suite_lock(settings)
    try:
        with pytest.raises(RuntimeError, match="Another test run"):
            gate._suite_lock(replace(settings, suite_queue_max_s=1))
    finally:
        first.close()
    gate._suite_lock(settings).close()  # free again once the first suite ends
