"""E2BSandbox — Firecracker microVM tier via the E2B Python SDK.

Mounts the worktree into a fresh microVM, brokers read/write/list
through the SDK's filesystem API.  Cold-start time is sub-second on a
healthy E2B account but still 50-100x slower than LocalSandbox, so it
should only be requested for cards that actually need the isolation
(red team, untrusted MCPs, agents allowed to run shell commands).

Lazy SDK import; if ``e2b`` isn't installed or ``E2B_API_KEY`` isn't
set, ``open_async`` raises SandboxError and the dispatcher falls back
to LocalSandbox with a warning event.

V4 ships read/write/list parity with LocalSandbox.  Bash exec inside
the sandbox stays a card-feature-flag for a future phase.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from apps.service.sandbox.local import SandboxError

log = logging.getLogger(__name__)


def _import_sdk() -> Any:
    try:
        from e2b import Sandbox  # type: ignore[import-not-found]

        return Sandbox
    except ImportError as exc:
        raise SandboxError("e2b SDK not installed; install with `pip install e2b`") from exc


@dataclass
class E2BSandbox:
    worktree: Path
    api_key: str = ""
    template: str = "base"
    _sb: Any = None
    _written_paths: set[str] = field(default_factory=set)

    @classmethod
    async def open_async(
        cls,
        worktree: Path,
        *,
        template: str = "base",
        api_key: str | None = None,
    ) -> E2BSandbox:
        Sandbox = _import_sdk()
        key = api_key or os.environ.get("E2B_API_KEY")
        if not key:
            raise SandboxError("E2B_API_KEY not set")
        sb = cls(worktree=worktree, api_key=key, template=template)
        # E2B's SDK is synchronous on instantiation; run in a thread.
        import asyncio

        sb._sb = await asyncio.to_thread(
            Sandbox,
            template=template,
            api_key=key,
        )
        # Upload the worktree into the sandbox under /home/user/work.
        await asyncio.to_thread(sb._upload_initial)
        return sb

    def _upload_initial(self) -> None:
        for p in self.worktree.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(self.worktree).as_posix()
            if rel.startswith(".git/") or rel.startswith(".agent-worktrees/"):
                continue
            self._sb.files.write(f"/home/user/work/{rel}", p.read_bytes())

    async def close(self) -> None:
        if self._sb is None:
            return
        import asyncio

        # Pull written files back so the local worktree reflects the
        # sandbox state for the diff capture step.
        await asyncio.to_thread(self._download_changes)
        await asyncio.to_thread(self._sb.kill)
        self._sb = None

    def _download_changes(self) -> None:
        for rel in self._written_paths:
            try:
                blob = self._sb.files.read(f"/home/user/work/{rel}")
            except Exception as exc:
                log.warning("e2b download %s failed: %s", rel, exc)
                continue
            target = self.worktree / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(blob, str):
                target.write_text(blob)
            else:
                target.write_bytes(blob)

    async def read_file(self, rel: str) -> bytes:
        if self._sb is None:
            raise SandboxError("E2BSandbox not opened")
        import asyncio

        blob = await asyncio.to_thread(
            self._sb.files.read,
            f"/home/user/work/{rel}",
        )
        return blob.encode() if isinstance(blob, str) else blob

    async def write_file(self, rel: str, content: bytes) -> None:
        if self._sb is None:
            raise SandboxError("E2BSandbox not opened")
        import asyncio

        await asyncio.to_thread(
            self._sb.files.write,
            f"/home/user/work/{rel}",
            content,
        )
        self._written_paths.add(rel)

    async def list_paths(self) -> Iterable[Path]:
        if self._sb is None:
            return []
        import asyncio

        entries = await asyncio.to_thread(
            self._sb.files.list,
            "/home/user/work",
        )
        return [self.worktree / e.path.replace("/home/user/work/", "") for e in entries]
