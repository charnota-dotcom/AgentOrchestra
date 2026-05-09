"""In-process pubsub bus for Run events.

Subscribers register with ``subscribe(filter_fn)`` and consume events
from an asyncio.Queue.  The dispatcher and the SSE endpoint both
attach to this bus so live UIs can stream events as the agent runs
without polling SQLite.

The bus is intentionally process-local; cross-process fan-out would
move to NATS/Redis when (and if) we ever support remote workers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from apps.service.types import Event

log = logging.getLogger(__name__)

EventFilter = Callable[[Event], bool]


@dataclass
class _Subscriber:
    filter: EventFilter
    queue: asyncio.Queue[Event] = field(default_factory=asyncio.Queue)
    closed: bool = False


class EventBus:
    """Tiny pub/sub.  Publishers call ``publish`` (sync); subscribers
    iterate via ``stream``.  Slow subscribers drop events past a
    bounded queue depth rather than blocking the publisher.
    """

    def __init__(self, *, max_queue: int = 1024) -> None:
        self._subs: list[_Subscriber] = []
        self._lock = asyncio.Lock()
        self._max_queue = max_queue

    def publish(self, event: Event) -> None:
        for sub in self._subs:
            if sub.closed:
                continue
            try:
                if sub.filter(event):
                    if sub.queue.qsize() >= self._max_queue:
                        # Drop oldest to avoid stalling the publisher.
                        try:
                            sub.queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    sub.queue.put_nowait(event)
            except Exception:
                log.exception("subscriber filter raised")

    @asynccontextmanager
    async def subscribe(self, filter_fn: EventFilter):
        sub = _Subscriber(filter=filter_fn)
        async with self._lock:
            self._subs.append(sub)
        try:
            yield sub
        finally:
            sub.closed = True
            async with self._lock:
                if sub in self._subs:
                    self._subs.remove(sub)

    async def stream(self, filter_fn: EventFilter, *, timeout: float | None = None):
        """Yield events forever (or until cancelled).

        Use as: ``async for event in bus.stream(...)``.
        """
        async with self.subscribe(filter_fn) as sub:
            while not sub.closed:
                try:
                    if timeout is None:
                        ev = await sub.queue.get()
                    else:
                        ev = await asyncio.wait_for(sub.queue.get(), timeout=timeout)
                    yield ev
                except TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break


# Filter helpers --------------------------------------------------------------


def by_run(run_id: str) -> EventFilter:
    return lambda e: e.run_id == run_id


def by_workspace(workspace_id: str) -> EventFilter:
    return lambda e: e.workspace_id == workspace_id


def all_events() -> EventFilter:
    return lambda _e: True
