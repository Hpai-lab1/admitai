"""The reranker remembers (question, passage) scores: a repeated question skips the model, a
changed passage is scored afresh, and the memory stays bounded. No model files needed."""

import threading
from collections import OrderedDict

from knowledge_api import rerank


def fake_reranker(calls):
    r = object.__new__(rerank.Reranker)
    r._lock = threading.Lock()
    r._memo = OrderedDict()

    def run(pairs):
        calls.append(list(pairs))
        return [float(len(q) + len(p)) for q, p in pairs]

    r._run = run
    return r


def test_a_repeated_question_is_not_scored_twice():
    calls = []
    r = fake_reranker(calls)
    first = r.scores("fees?", ["a", "bb", "ccc"])
    again = r.scores("fees?", ["ccc", "a", "bb"])
    assert first == [6.0, 7.0, 8.0]
    assert again == [8.0, 6.0, 7.0]  # same scores, in the order asked
    assert len(calls) == 1


def test_only_new_or_changed_passages_are_scored():
    calls = []
    r = fake_reranker(calls)
    r.scores("fees?", ["a", "bb"])
    r.scores("fees?", ["a", "bb (updated page)"])
    assert calls[1] == [("fees?", "bb (updated page)")]


def test_the_memory_stays_bounded(monkeypatch):
    monkeypatch.setattr(rerank, "MEMO_SIZE", 3)
    r = fake_reranker([])
    r.scores("q", ["1", "2", "3", "4", "5"])
    assert len(r._memo) == 3
