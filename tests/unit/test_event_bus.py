"""EventBus pub/sub semantics."""

from __future__ import annotations

import asyncio

import pytest

from apps.service.dispatch.bus import EventBus, all_events, by_run
from apps.service.types import Event, EventKind, EventSource


def _ev(run_id: str | None = None, kind: EventKind = EventKind.RUN_STARTED) -> Event:
    return Event(source=EventSource.SYSTEM, kind=kind, run_id=run_id, text="x")


@pytest.mark.asyncio
async def test_subscriber_receives_published_events() -> None:
    bus = EventBus()
    received: list[Event] = []

    async def consume() -> None:
        async for ev in bus.stream(all_events(), timeout=0.5):
            received.append(ev)
            if len(received) >= 3:
                break

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)  # ensure subscription is live before publishing
    bus.publish(_ev())
    bus.publish(_ev())
    bus.publish(_ev())
    await asyncio.wait_for(task, timeout=2.0)
    assert len(received) == 3


@pytest.mark.asyncio
async def test_filter_excludes_other_runs() -> None:
    bus = EventBus()
    received: list[Event] = []

    async def consume() -> None:
        async for ev in bus.stream(by_run("r-1"), timeout=0.3):
            received.append(ev)
            if len(received) >= 1:
                break

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    bus.publish(_ev(run_id="r-2"))
    bus.publish(_ev(run_id="r-1"))
    await asyncio.wait_for(task, timeout=2.0)
    assert len(received) == 1
    assert received[0].run_id == "r-1"


@pytest.mark.asyncio
async def test_unsubscribe_on_exit() -> None:
    bus = EventBus()
    async with bus.subscribe(all_events()) as _sub:
        assert len(bus._subs) == 1
    assert len(bus._subs) == 0
