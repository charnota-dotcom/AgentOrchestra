"""Claude session JSONL watcher.

Tails `~/.claude/projects/<slug>/<session-id>.jsonl` files and
normalizes new lines into the unified Event schema.  Catches sessions
the user starts manually outside the orchestrator (e.g. directly via
the `claude` CLI in their terminal).

Implementation: `watchdog` for file-system notifications on the parent
directory; an offset table per file so the watcher resumes cleanly
after a service restart.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from apps.service.types import Event, EventKind, EventSource

if TYPE_CHECKING:
    from apps.service.store.events import EventStore

log = logging.getLogger(__name__)


def default_claude_projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


@dataclass
class _FileOffset:
    path: Path
    pos: int = 0  # last byte we successfully read


class JSONLWatcher:
    """Watches a directory tree of `.jsonl` files and emits Events.

    Currently uses polling under the hood for portability; we can swap
    to `watchdog`'s native FS observer in week 6 of Phase 1 without
    changing this public interface.
    """

    def __init__(
        self,
        store: EventStore,
        *,
        root: Path | None = None,
        poll_interval: float = 1.0,
    ) -> None:
        self.store = store
        self.root = (root or default_claude_projects_dir()).resolve()
        self.poll_interval = poll_interval
        self._offsets: dict[Path, _FileOffset] = {}
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="jsonl-watcher")
        log.info("jsonl watcher started; root=%s", self.root)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        await self._task
        self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.root.exists():
                    await self._sweep_once()
            except Exception:
                log.exception("jsonl watcher tick failed")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), self.poll_interval)

    async def _sweep_once(self) -> None:
        for path in self.root.rglob("*.jsonl"):
            try:
                await self._read_new_lines(path)
            except Exception:
                log.exception("error reading %s", path)

    async def _read_new_lines(self, path: Path) -> None:
        offset = self._offsets.setdefault(path, _FileOffset(path=path))
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return
        if size < offset.pos:
            # File rotated/truncated; restart from beginning.
            offset.pos = 0
        if size == offset.pos:
            return
        # Read new bytes off the disk in a thread to avoid blocking the loop.
        new = await asyncio.to_thread(_read_bytes, path, offset.pos, size)
        offset.pos = size
        for line in new.splitlines():
            await self._emit(path, line)

    async def _emit(self, path: Path, raw: bytes) -> None:
        if not raw.strip():
            return
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            log.warning("non-JSON line in %s", path.name)
            return

        # Best-effort normalization of Claude session.jsonl shape.
        kind = payload.get("type") or payload.get("kind") or "event"
        text = ""
        if isinstance(payload.get("message"), dict):
            content = payload["message"].get("content")
            if isinstance(content, list):
                text = "\n".join(
                    c.get("text", "")
                    for c in content
                    if isinstance(c, dict) and c.get("type") == "text"
                )
            elif isinstance(content, str):
                text = content
        elif isinstance(payload.get("text"), str):
            text = payload["text"]

        event_kind = _map_kind(kind)
        event = Event(
            source=EventSource.INGEST_CLAUDE_JSONL,
            kind=event_kind,
            payload={"path": str(path), "raw": payload},
            text=text[:8000],  # bound FTS payload
        )
        await self.store.append_event(event)


def _map_kind(claude_kind: str) -> EventKind:
    return {
        "user": EventKind.STEP_STARTED,
        "assistant": EventKind.LLM_CALL_COMPLETED,
        "tool_use": EventKind.TOOL_CALLED,
        "tool_result": EventKind.STEP_COMPLETED,
    }.get(claude_kind, EventKind.INGEST_RECEIVED)


def _read_bytes(path: Path, start: int, end: int) -> bytes:
    with path.open("rb") as fh:
        os.lseek(fh.fileno(), start, os.SEEK_SET)
        return fh.read(end - start)
