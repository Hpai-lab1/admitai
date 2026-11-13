"""The engine must not block the server's event loop (PRD 10.2: 20-40 conversations at once on one
process). Database reads and writes, Module C calls and WhatsApp sends are synchronous, so they run
in worker threads; a slow one must not freeze every other visitor's stream."""

import asyncio
import time
import uuid

from conftest import FakeKnowledge, FakeLLM, needs_db

from assistant_api import store
from assistant_api.auth import Caller
from assistant_api.engine import Deps, handle_turn
from assistant_api.guard import RateLimiter

ANSWER = "Hostel gates close at 21:30 [1]."


async def _heartbeat(stop: asyncio.Event, gaps: list[float]) -> None:
    last = time.monotonic()
    while not stop.is_set():
        await asyncio.sleep(0.01)
        now = time.monotonic()
        gaps.append(now - last)
        last = now


async def _turn(deps, tenant):
    caller = Caller(tenant, str(uuid.uuid4()), "visitor")
    return [
        ev
        async for ev in handle_turn(
            deps, caller, str(uuid.uuid4()), "When do hostel gates close?"
        )
    ]


@needs_db
def test_a_slow_database_call_does_not_freeze_other_work(settings, tenant, monkeypatch):
    real = store.history

    def slow_history(*a, **kw):
        time.sleep(0.4)  # a slow query: blocking, as psycopg's sync API is
        return real(*a, **kw)

    monkeypatch.setattr(store, "history", slow_history)
    deps = Deps(settings, FakeKnowledge(), FakeLLM(ANSWER), RateLimiter(100, 60))

    async def main():
        stop, gaps = asyncio.Event(), []
        beat = asyncio.create_task(_heartbeat(stop, gaps))
        await asyncio.sleep(0.05)  # the heartbeat is running before the turn starts
        events = await _turn(deps, tenant)
        stop.set()
        await beat
        return events, max(gaps)

    events, worst_gap = asyncio.run(main())
    assert events[-1]["event"] == "done" and events[-1]["data"]["outcome"] == "answered"
    assert worst_gap < 0.15, f"the event loop stalled for {worst_gap:.2f} s"


@needs_db
def test_two_turns_with_slow_database_calls_overlap(settings, monkeypatch):
    real = store.tenant_config

    def slow_config(*a, **kw):
        time.sleep(0.3)
        return real(*a, **kw)

    monkeypatch.setattr(store, "tenant_config", slow_config)
    deps = Deps(
        settings, FakeKnowledge(), FakeLLM(ANSWER, ANSWER), RateLimiter(100, 60)
    )

    async def main():
        t0 = time.monotonic()
        a, b = await asyncio.gather(
            _turn(deps, str(uuid.uuid4())), _turn(deps, str(uuid.uuid4()))
        )
        return time.monotonic() - t0, a, b

    elapsed, a, b = asyncio.run(main())
    assert a[-1]["event"] == "done" and b[-1]["event"] == "done"
    assert (
        elapsed < 0.55
    ), f"two turns took {elapsed:.2f} s: they ran one after the other"
