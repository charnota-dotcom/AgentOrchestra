"""Drift Sentinel — periodic Tracker watcher for long runs.

Subscribes to the EventBus, accumulates per-run signals (commits,
tool calls, state changes, latest text deltas), and every ``interval``
seconds checks each tracked run for divergence from its declared
plan.  When divergence is detected (heuristic: cumulative tool calls
> threshold without a CommitCreated event, or repeated tool failures)
the sentinel emits a high-signal Event so the GUI can surface it.

Runtime model: a single asyncio.Task started by the service entry,
shared across all runs.  Light enough to leave on permanently.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any

from apps.service.dispatch.bus import EventBus, all_events
from apps.service.store.events import EventStore
from apps.service.types import Event, EventKind, EventSource

log = logging.getLogger(__name__)


@dataclass
class _RunStats:
    tool_calls: int = 0
    tool_errors: int = 0
    commits: int = 0
    last_event_at: float = 0.0
    flagged: bool = False
    last_text: str = ""
    state: str = ""


@dataclass
class DriftSentinel:
    store: EventStore
    bus: EventBus
    check_interval_s: float = 30.0
    tool_call_threshold_no_commit: int = 5
    consecutive_tool_error_threshold: int = 3
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _runs: dict[str, _RunStats] = field(default_factory=dict)
    _consume_task: asyncio.Task[Any] | None = None
    _check_task: asyncio.Task[Any] | None = None

    async def start(self) -> None:
        if self._consume_task is not None:
            return
        self._stop.clear()
        self._consume_task = asyncio.create_task(
            self._consume(),
            name="drift-sentinel-consume",
        )
        self._check_task = asyncio.create_task(
            self._check_loop(),
            name="drift-sentinel-check",
        )
        log.info("drift sentinel started")

    async def stop(self) -> None:
        self._stop.set()
        for t in (self._consume_task, self._check_task):
            if t and not t.done():
                t.cancel()
                with contextlib.suppress(BaseException):
                    await t
        self._consume_task = None
        self._check_task = None

    async def _consume(self) -> None:
        async for ev in self.bus.stream(all_events()):
            if self._stop.is_set():
                return
            if not ev.run_id:
                continue
            stats = self._runs.setdefault(ev.run_id, _RunStats())
            stats.last_event_at = ev.occurred_at.timestamp()
            if ev.kind is EventKind.TOOL_CALLED:
                stats.tool_calls += 1
                if ev.payload.get("is_error"):
                    stats.tool_errors += 1
                else:
                    stats.tool_errors = 0
            elif ev.kind is EventKind.COMMIT_CREATED:
                stats.commits += 1
                stats.flagged = False
            elif ev.kind is EventKind.RUN_STATE_CHANGED:
                state = ev.payload.get("to") or ev.payload.get("state")
                if isinstance(state, str):
                    stats.state = state
                    if state in ("merged", "rejected", "aborted"):
                        self._runs.pop(ev.run_id, None)
                        continue
            elif ev.kind is EventKind.LLM_CALL_COMPLETED:
                if ev.text:
                    stats.last_text = ev.text[-200:]

    async def _check_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._check_once()
            except Exception:
                log.exception("drift sentinel check failed")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.check_interval_s,
                )

    async def _check_once(self) -> None:
        for run_id, stats in list(self._runs.items()):
            if stats.flagged:
                continue
            warning: str | None = None
            payload: dict[str, Any] = {
                "run_id": run_id,
                "tool_calls": stats.tool_calls,
                "commits": stats.commits,
                "tool_errors": stats.tool_errors,
                "state": stats.state,
            }
            if stats.tool_calls >= self.tool_call_threshold_no_commit and stats.commits == 0:
                warning = (
                    f"{stats.tool_calls} tool calls and zero commits — agent "
                    f"may be exploring without producing changes"
                )
            elif stats.tool_errors >= self.consecutive_tool_error_threshold:
                warning = f"{stats.tool_errors} consecutive tool errors — agent may be stuck"
            if warning:
                stats.flagged = True
                payload["warning"] = warning
                await self.store.append_event(
                    Event(
                        source=EventSource.SYSTEM,
                        kind=EventKind.RUN_STATE_CHANGED,
                        run_id=run_id,
                        payload=payload,
                        text=f"drift: {warning}",
                    )
                )
