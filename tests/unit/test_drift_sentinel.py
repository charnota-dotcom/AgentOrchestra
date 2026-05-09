"""DriftSentinel signal accumulation + flagging."""

from __future__ import annotations

import asyncio

import pytest

from apps.service.dispatch.bus import EventBus
from apps.service.dispatch.drift_sentinel import DriftSentinel
from apps.service.types import Event, EventKind, EventSource


@pytest.mark.asyncio
async def test_sentinel_flags_many_tools_no_commit(store) -> None:
    bus = EventBus()
    store.on_append = bus.publish
    sentinel = DriftSentinel(
        store=store, bus=bus, check_interval_s=0.1, tool_call_threshold_no_commit=3
    )
    await sentinel.start()
    try:
        for _ in range(3):
            await store.append_event(
                Event(
                    source=EventSource.DISPATCH_RUN,
                    kind=EventKind.TOOL_CALLED,
                    run_id="r-1",
                    text="x",
                )
            )
        # Allow the consumer + check loop to fire.
        for _ in range(20):
            cur = await store.db.execute(
                "SELECT text FROM events WHERE run_id = ? AND text LIKE 'drift:%'",
                ("r-1",),
            )
            rows = await cur.fetchall()
            if rows:
                assert "tool calls and zero commits" in rows[0]["text"]
                return
            await asyncio.sleep(0.05)
        raise AssertionError("sentinel did not flag drift")
    finally:
        await sentinel.stop()


@pytest.mark.asyncio
async def test_sentinel_clears_flag_after_commit(store) -> None:
    bus = EventBus()
    store.on_append = bus.publish
    sentinel = DriftSentinel(
        store=store, bus=bus, check_interval_s=0.1, tool_call_threshold_no_commit=2
    )
    await sentinel.start()
    try:
        await store.append_event(
            Event(
                source=EventSource.DISPATCH_RUN,
                kind=EventKind.TOOL_CALLED,
                run_id="r-2",
                text="x",
            )
        )
        await store.append_event(
            Event(
                source=EventSource.DISPATCH_RUN,
                kind=EventKind.TOOL_CALLED,
                run_id="r-2",
                text="x",
            )
        )
        await store.append_event(
            Event(
                source=EventSource.DISPATCH_RUN,
                kind=EventKind.COMMIT_CREATED,
                run_id="r-2",
                text="ok",
            )
        )
        await asyncio.sleep(0.3)
        # Run completes; sentinel forgets it.
        await store.append_event(
            Event(
                source=EventSource.SYSTEM,
                kind=EventKind.RUN_STATE_CHANGED,
                run_id="r-2",
                payload={"to": "merged"},
                text="merged",
            )
        )
        await asyncio.sleep(0.2)
        assert "r-2" not in sentinel._runs
    finally:
        await sentinel.stop()
